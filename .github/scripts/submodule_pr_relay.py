#!/usr/bin/env python3
"""Coordinate an internal submodule PR with a parent variant PR."""

from __future__ import annotations

import configparser
import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
OWNER = "NomadAnget"
PARENT = "atelier-studio"
BASE = "master"
MARKER = re.compile(r"<!-- atelier-submodule-pr (\{.*?\}) -->", re.S)


def die(message: str) -> None:
    raise SystemExit(message)


def api(method: str, path: str, payload=None, missing_ok: bool = False):
    token = os.environ.get("ATELIER_CI_TOKEN", "")
    if not token:
        die("Repository secret ATELIER_CI_TOKEN is required (Contents and Pull requests: write on parent and internal children).")
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        API + path, data=body, method=method,
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": f"Bearer {token}",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return None if not raw else json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        if missing_ok and exc.code == 404:
            return None
        die(f"GitHub API {method} {path} failed ({exc.code}): {detail}")


def internal_modules() -> dict[str, str]:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(".gitmodules", encoding="utf-8")
    result = {}
    for section in cfg.sections():
        path, url = cfg.get(section, "path", fallback=""), cfg.get(section, "url", fallback="")
        if not path or not url.startswith("../"):
            continue
        name = url.rsplit("/", 1)[-1].removesuffix(".git")
        if re.fullmatch(r"atelier-[A-Za-z0-9._-]+", name):
            result[f"{OWNER}/{name}"] = path
    return result


def marker_body(repo: str, number: int, sha: str, path: str, phase: str, tree: str = "",
                parent_sha: str = "", base_sha: str = "") -> str:
    data = {"repo": repo, "pr_number": number, "head_sha": sha, "path": path, "phase": phase}
    if tree:
        data["tree_sha"] = tree
    if parent_sha:
        data["parent_head_sha"] = parent_sha
    if base_sha:
        data["base_sha"] = base_sha
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    key = os.environ.get("ATELIER_CI_TOKEN", "").encode()
    if not key:
        die("ATELIER_CI_TOKEN is required to sign automation state.")
    data["sig"] = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    return (
        "由子模块 PR 自动生成的变体集成检查。\n\n"
        f"- 子模块：[{repo}#{number}](https://github.com/{repo}/pull/{number})\n"
        f"- 候选提交：`{sha}`\n\n"
        "<!-- atelier-submodule-pr " + json.dumps(data, separators=(",", ":")) + " -->\n"
    )


def intake(event):
    payload = event.get("client_payload") or {}
    repo = str(payload.get("child_repo", ""))
    action = str(payload.get("action", ""))
    try:
        number = int(payload.get("pr_number", 0))
    except (TypeError, ValueError):
        number = 0
    sha = str(payload.get("head_sha", ""))
    if action not in {"opened", "synchronize", "reopened", "ready_for_review"}:
        print(f"Ignored child PR action: {action or '(empty)'}")
        return
    path = internal_modules().get(repo)
    if not path or number < 1 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        die("Invalid dispatch data or child repository is not an internal submodule.")
    child = api("GET", f"/repos/{repo}/pulls/{number}")
    if child.get("state") != "open" or child.get("draft"):
        die("Child PR must be open and ready for review.")
    if child.get("base", {}).get("repo", {}).get("full_name") != repo or child.get("base", {}).get("ref") != BASE:
        die(f"Child PR must target {repo}:{BASE}.")
    if child.get("head", {}).get("repo", {}).get("full_name") != repo:
        die("Fork-origin child PRs cannot be promoted automatically.")
    if child.get("head", {}).get("sha") != sha:
        die("Dispatch SHA is stale; wait for the child PR's latest notification.")

    parent_repo = f"{OWNER}/{PARENT}"
    parent_ref = api("GET", f"/repos/{parent_repo}/git/ref/heads/{BASE}")
    parent_sha = parent_ref["object"]["sha"]
    parent_commit = api("GET", f"/repos/{parent_repo}/git/commits/{parent_sha}")
    tree = api("POST", f"/repos/{parent_repo}/git/trees", {
        "base_tree": parent_commit["tree"]["sha"],
        "tree": [{"path": path, "mode": "160000", "type": "commit", "sha": sha}],
    })
    commit = api("POST", f"/repos/{parent_repo}/git/commits", {
        "message": f"ci(submodule): validate {repo}#{number}",
        "tree": tree["sha"], "parents": [parent_sha],
    })
    branch = "automation/submodule-pr/" + path.replace("/", "-") + f"-{number}"
    ref_path = f"/repos/{parent_repo}/git/ref/heads/{urllib.parse.quote(branch, safe='/')}"
    ref = api("GET", ref_path, missing_ok=True)
    if ref is None:
        api("POST", f"/repos/{parent_repo}/git/refs", {
            "ref": "refs/heads/" + branch, "sha": commit["sha"]})
    else:
        api("PATCH", ref_path, {"sha": commit["sha"], "force": True})

    body = marker_body(repo, number, sha, path, "check")
    query = urllib.parse.urlencode({"state": "open", "head": f"{OWNER}:{branch}", "base": BASE})
    prs = api("GET", f"/repos/{parent_repo}/pulls?{query}")
    title = f"ci(submodule): validate {repo}#{number}"
    if prs:
        pr = prs[0]
        api("PATCH", f"/repos/{parent_repo}/pulls/{pr['number']}",
            {"title": title, "body": body, "state": "open"})
        print(f"Updated variant PR #{pr['number']}")
    else:
        pr = api("POST", f"/repos/{parent_repo}/pulls",
                 {"title": title, "head": branch, "base": BASE, "body": body})
        print(f"Created variant PR #{pr['number']}")


def get_marker(body: str) -> dict:
    match = MARKER.search(body or "")
    if not match:
        die("This PR has no trusted submodule automation marker.")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        die("Malformed submodule automation marker.")


def verify_reconcile(event):
    pr = event.get("pull_request") or {}
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        die("GITHUB_OUTPUT is missing.")
    skip = False
    try:
        marker = get_marker(pr.get("body", ""))
        if marker.get("phase") == "reconcile":
            signature = marker.pop("sig", "")
            canonical = json.dumps(marker, sort_keys=True, separators=(",", ":")).encode()
            key = os.environ.get("ATELIER_CI_TOKEN", "").encode()
            valid = bool(key) and hmac.compare_digest(
                signature, hmac.new(key, canonical, hashlib.sha256).hexdigest()
            )
            skip = (
                valid
                and marker.get("parent_head_sha") == pr.get("head", {}).get("sha")
                and marker.get("base_sha") == pr.get("base", {}).get("sha")
                and pr.get("base", {}).get("ref") == BASE
                and pr.get("base", {}).get("repo", {}).get("full_name") == f"{OWNER}/{PARENT}"
                and pr.get("head", {}).get("repo", {}).get("full_name") == f"{OWNER}/{PARENT}"
            )
    except SystemExit:
        skip = False
    with open(output, "a", encoding="utf-8") as stream:
        stream.write("skip_full=" + ("true" if skip else "false") + "\\n")
    print("Signed gitlink reconciliation is valid; skipping full kernel check." if skip
          else "Full kernel validation is required.")


def merge_pr(repo: str, number: int, expected_sha: str) -> None:
    result = api("PUT", f"/repos/{repo}/pulls/{number}/merge",
                 {"sha": expected_sha, "merge_method": "merge"})
    if not result or not result.get("merged"):
        die(f"GitHub did not merge {repo}#{number}: {(result or {}).get('message', 'no response')}")


def ref_sha(repo: str, branch: str) -> str:
    return api("GET", f"/repos/{repo}/git/ref/heads/{branch}")["object"]["sha"]


def commit_tree(repo: str, sha: str) -> str:
    return api("GET", f"/repos/{repo}/git/commits/{sha}")["tree"]["sha"]


def gitlink_sha(repo: str, commit_sha: str, path: str) -> str:
    tree_sha = api("GET", f"/repos/{repo}/git/commits/{commit_sha}")["tree"]["sha"]
    tree = api("GET", f"/repos/{repo}/git/trees/{tree_sha}?recursive=1")
    for item in tree.get("tree", []):
        if item.get("path") == path and item.get("type") == "commit":
            return item["sha"]
    die(f"Could not find gitlink {path} in parent commit {commit_sha}.")


def set_gitlink(parent_repo: str, branch: str, old_head: str, path: str, child_sha: str) -> str:
    current = api("GET", f"/repos/{parent_repo}/git/ref/heads/{urllib.parse.quote(branch, safe='/')}")
    if current["object"]["sha"] != old_head:
        die("Variant PR branch changed during finalization; refusing to update it.")
    old_commit = api("GET", f"/repos/{parent_repo}/git/commits/{old_head}")
    tree = api("POST", f"/repos/{parent_repo}/git/trees", {
        "base_tree": old_commit["tree"]["sha"],
        "tree": [{"path": path, "mode": "160000", "type": "commit", "sha": child_sha}],
    })
    commit = api("POST", f"/repos/{parent_repo}/git/commits", {
        "message": f"ci(submodule): pin {path} to {child_sha[:12]}",
        "tree": tree["sha"], "parents": [old_head],
    })
    api("PATCH", f"/repos/{parent_repo}/git/ref/heads/{urllib.parse.quote(branch, safe='/')}",
        {"sha": commit["sha"], "force": False})
    return commit["sha"]


def publish_reused_check(parent_repo: str, head_sha: str) -> None:
    api("POST", f"/repos/{parent_repo}/check-runs", {
        "name": "kernel-check",
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": "success",
        "output": {
            "title": "Kernel validation reused",
            "summary": "完整内核检查已通过。子模块合并后的文件树与受检提交完全一致；此提交只更新 gitlink SHA，已完成签名核对。",
        },
    })


def finalize(event):
    pr = event.get("pull_request") or {}
    number = int(pr.get("number", 0))
    parent_repo = f"{OWNER}/{PARENT}"
    current_pr = api("GET", f"/repos/{parent_repo}/pulls/{number}")
    if current_pr.get("state") != "open":
        print("Variant PR is already closed; no further action.")
        return
    marker = get_marker(current_pr.get("body", ""))
    child_repo = marker.get("repo", "")
    path = internal_modules().get(child_repo)
    if not path or path != marker.get("path"):
        die("PR marker does not identify an internal submodule.")
    head = current_pr["head"]["sha"]
    expected_head = pr.get("head", {}).get("sha")
    if head != expected_head:
        die("Variant PR head changed after the check; refusing to finalize.")
    branch = current_pr["head"]["ref"]
    base_at_check = pr.get("base", {}).get("sha")
    if ref_sha(parent_repo, BASE) != base_at_check:
        die("Variant master advanced during the check; rerun the full validation before merging.")
    child_number = int(marker["pr_number"])
    child = api("GET", f"/repos/{child_repo}/pulls/{child_number}")
    phase = marker.get("phase")
    candidate_sha = marker["head_sha"]
    if phase in {"check", "recheck"} and gitlink_sha(parent_repo, head, path) != candidate_sha:
        die("The variant PR does not pin the child SHA that was checked; refusing to merge either PR.")
    if phase == "reconcile":
        if child.get("state") != "closed" or not child.get("merged"):
            die("A reconciled variant PR requires its child PR to be merged already.")
        latest_sha = ref_sha(child_repo, BASE)
        latest_tree = commit_tree(child_repo, latest_sha)
        checked_tree = marker.get("tree_sha", "")
        if not checked_tree:
            die("Reconciliation marker is missing its checked tree SHA.")
        if latest_tree != checked_tree:
            body = marker_body(child_repo, child_number, latest_sha, path, "recheck", latest_tree)
            api("PATCH", f"/repos/{parent_repo}/pulls/{number}", {"body": body})
            set_gitlink(parent_repo, branch, head, path, latest_sha)
            print("Child tree changed after reconciliation; a full kernel validation will run.")
            return
        if gitlink_sha(parent_repo, head, path) != latest_sha:
            new_head = set_gitlink(parent_repo, branch, head, path, latest_sha)
            body = marker_body(child_repo, child_number, latest_sha, path, "reconcile",
                               latest_tree, new_head, base_at_check)
            api("PATCH", f"/repos/{parent_repo}/pulls/{number}", {"body": body})
            publish_reused_check(parent_repo, new_head)
            print(f"Refreshed child gitlink to {latest_sha}; validated tree is unchanged.")
            return
        if ref_sha(parent_repo, BASE) != base_at_check:
            die("Variant master advanced; refusing to merge the checked result.")
        merge_pr(parent_repo, number, head)
        print(f"Merged already-validated variant PR #{number}.")
        return
    if phase == "check" and child.get("state") == "open":
        if child.get("draft") or child.get("head", {}).get("sha") != candidate_sha:
            die("Child PR changed or became draft after validation; wait for its next notification.")
        if child.get("head", {}).get("repo", {}).get("full_name") != child_repo:
            die("Child PR is no longer from its own repository.")
        if child.get("base", {}).get("repo", {}).get("full_name") != child_repo or child.get("base", {}).get("ref") != BASE:
            die(f"Child PR must still target {child_repo}:{BASE}.")
        merge_pr(child_repo, child_number, candidate_sha)
    elif child.get("state") != "closed" or not child.get("merged"):
        die("Child PR is neither the checked open PR nor already merged.")

    latest_child_sha = ref_sha(child_repo, BASE)
    latest_tree = commit_tree(child_repo, latest_child_sha)
    checked_tree = commit_tree(child_repo, candidate_sha)
    if phase == "recheck":
        expected_tree = marker.get("tree_sha", "")
        if not expected_tree:
            die("Recheck marker is missing its checked tree SHA.")
        checked_tree = expected_tree

    if latest_tree != checked_tree:
        body = marker_body(child_repo, child_number, latest_child_sha, path, "recheck", latest_tree)
        api("PATCH", f"/repos/{parent_repo}/pulls/{number}", {"body": body})
        new_head = set_gitlink(parent_repo, branch, head, path, latest_child_sha)
        print(f"Child master differs from the checked tree; updated variant PR to {new_head}. Full kernel validation will run on the new head.")
        return

    if gitlink_sha(parent_repo, head, path) != latest_child_sha:
        final_head = set_gitlink(parent_repo, branch, head, path, latest_child_sha)
        body = marker_body(child_repo, child_number, latest_child_sha, path, "reconcile",
                           latest_tree, final_head, base_at_check)
        api("PATCH", f"/repos/{parent_repo}/pulls/{number}", {"body": body})
        publish_reused_check(parent_repo, final_head)
    else:
        final_head = head

    if ref_sha(parent_repo, BASE) != base_at_check:
        die("Variant master advanced before merge; refusing an unchecked merge result.")
    merge_pr(parent_repo, number, final_head)
    print(f"Merged child PR #{child_number} and variant PR #{number}.")


def main():
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as source:
        event = json.load(source)
    mode = os.environ.get("ATELIER_RELAY_MODE", "")
    if mode == "intake":
        intake(event)
    elif mode == "finalize":
        finalize(event)
    elif mode == "verify":
        verify_reconcile(event)
    else:
        die("ATELIER_RELAY_MODE must be intake or finalize.")


if __name__ == "__main__":
    main()
