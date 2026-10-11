from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PARENTS_FILE = ROOT / ".github" / "submodule-parents.json"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def run(
    command: list[str],
    cwd: Path,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")
    return result


def parse_event(path: Path) -> tuple[str, str, str, str | None]:
    event = json.loads(path.read_text(encoding="utf-8"))
    payload = event.get("client_payload")
    if not isinstance(payload, dict):
        raise ValueError("repository_dispatch client_payload is missing.")

    repository = payload.get("repository")
    commit = payload.get("sha")
    ref = payload.get("ref")
    event_id = payload.get("event_id")
    if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
        raise ValueError("Invalid or missing client_payload.repository.")
    if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
        raise ValueError("Invalid or missing client_payload.sha.")
    if not isinstance(ref, str) or not ref.startswith("refs/heads/"):
        raise ValueError("client_payload.ref must identify a branch.")
    check_ref = run(["git", "check-ref-format", ref], ROOT, check=False)
    if check_ref.returncode:
        raise ValueError("Invalid client_payload.ref.")
    if event_id is not None and (
        not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", event_id)
    ):
        raise ValueError("Invalid client_payload.event_id.")

    return repository.lower(), commit.lower(), ref, event_id


def read_parents() -> list[str]:
    parents = json.loads(PARENTS_FILE.read_text(encoding="utf-8"))
    if not isinstance(parents, list) or not parents:
        raise ValueError(f"{PARENTS_FILE} must contain a non-empty repository list.")
    if any(not isinstance(repo, str) or not REPOSITORY_RE.fullmatch(repo) for repo in parents):
        raise ValueError(f"{PARENTS_FILE} contains an invalid repository name.")
    return parents


def matching_modules(parent: Path, repository: str) -> list[tuple[str, str, str]]:
    entries = run(
        ["git", "config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"],
        parent,
        check=False,
    )
    if entries.returncode == 1 and not entries.stdout:
        return []
    if entries.returncode:
        raise RuntimeError(f"Could not read .gitmodules in {parent}: {entries.stderr.strip()}")

    run(["git", "submodule", "init"], parent)
    matches: list[tuple[str, str, str]] = []
    for line in entries.stdout.splitlines():
        key, path = line.split(None, 1)
        name = key.removeprefix("submodule.").removesuffix(".path")
        url = run(["git", "config", "--get", f"submodule.{name}.url"], parent).stdout.strip()
        if repository_slug(url) == repository:
            matches.append((name, path, url))
    return matches


def repository_slug(url: str) -> str:
    value = url.rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if value.startswith("git@"):
        value = value.split(":", 1)[1]
    elif "://" in value:
        host, _, path = value.split("://", 1)[1].partition("/")
        value = path
        owner, separator, name = value.partition("/")
        if separator and owner.lower() == "carnation" and host.lower() == "forgejo.hurcaguari.top":
            value = f"NomadAnget/{name}"
    if value.lower().startswith("carnation/"):
        value = "NomadAnget/" + value.split("/", 1)[1]
    return value.lower()


def short_branch(repository: str) -> str:
    slug = repository.split("/", 1)[1].lower().replace("_", "-").replace(".", "-")
    return f"automation/submodule-{slug}"


def fetch_submodule_history(
    parent: Path,
    module_path: str,
    ref: str,
    commit: str,
) -> tuple[str, str]:
    run(["git", "submodule", "update", "--init", "--depth", "1", "--", module_path], parent)
    submodule = parent / module_path
    run(["git", "fetch", "--quiet", "--unshallow", "origin", ref], submodule, check=False)
    fetch = run(["git", "fetch", "--quiet", "origin", ref], submodule, check=False)
    if fetch.returncode:
        raise RuntimeError(f"Could not fetch {ref} for submodule {module_path}: {fetch.stderr.strip()}")

    target = run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], submodule, check=False)
    if target.returncode:
        run(["git", "fetch", "--quiet", "origin", commit], submodule)
    branch_head = run(["git", "rev-parse", "FETCH_HEAD"], submodule).stdout.strip()
    reachable = run(
        ["git", "merge-base", "--is-ancestor", commit, branch_head],
        submodule,
        check=False,
    )
    if reachable.returncode:
        raise RuntimeError(
            f"Event commit {commit} is not reachable from {ref} in {module_path}; "
            "refusing to propose an unmerged or stale commit."
        )
    return str(submodule), branch_head


def update_parent(
    repository: str,
    child_repository: str,
    commit: str,
    ref: str,
    event_id: str,
) -> str:
    default_branch = run(
        ["gh", "api", f"repos/{repository}", "--jq", ".default_branch"],
        ROOT,
    ).stdout.strip()
    if not default_branch:
        raise RuntimeError(f"Could not determine the default branch for {repository}.")

    with tempfile.TemporaryDirectory(prefix="submodule-pr-") as temp_dir:
        parent = Path(temp_dir) / "parent"
        run(
            [
                "git",
                "clone",
                "--quiet",
                "--filter=blob:none",
                "--branch",
                default_branch,
                f"https://github.com/{repository}.git",
                str(parent),
            ],
            ROOT,
        )
        modules = matching_modules(parent, child_repository)
        if not modules:
            return f"{repository}: no matching submodule; skipped"

        branch = short_branch(child_repository)
        existing = run(
            ["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
            parent,
        ).stdout.strip()
        if existing:
            run(["git", "fetch", "--quiet", "origin", branch], parent)
            run(["git", "checkout", "-B", branch, "FETCH_HEAD"], parent)
            run(["git", "rebase", f"origin/{default_branch}"], parent)
        else:
            run(["git", "checkout", "-b", branch, f"origin/{default_branch}"], parent)

        changed_paths: list[str] = []
        for name, module_path, _ in modules:
            pinned = run(["git", "rev-parse", f"HEAD:{module_path}"], parent).stdout.strip()
            submodule_text, _ = fetch_submodule_history(parent, module_path, ref, commit)
            submodule = Path(submodule_text)

            event_is_stale = run(
                ["git", "merge-base", "--is-ancestor", commit, pinned],
                submodule,
                check=False,
            )
            if pinned == commit or event_is_stale.returncode == 0:
                continue

            pinned_is_ancestor = run(
                ["git", "merge-base", "--is-ancestor", pinned, commit],
                submodule,
                check=False,
            )
            if pinned_is_ancestor.returncode:
                raise RuntimeError(
                    f"{repository}:{module_path} is pinned to {pinned}, which is not an "
                    f"ancestor of event commit {commit}; refusing to rewind or replace history."
                )

            run(["git", "checkout", "--detach", commit], submodule)
            run(["git", "add", "--", module_path], parent)
            changed_paths.append(module_path)

        if not changed_paths:
            return f"{repository}: already aligned with {child_repository}@{commit[:12]}"

        run(["git", "config", "user.name", "atelier-bot"], parent)
        run(["git", "config", "user.email", "atelier-bot@users.noreply.github.com"], parent)
        run(
            [
                "git",
                "commit",
                "-m",
                f"chore(auto): propose {child_repository}@{commit[:12]}",
            ],
            parent,
        )
        run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], parent)

        body = (
            f"Automated submodule pointer update from `{child_repository}`.\n\n"
            f"- Exact merged commit: `{commit}`\n"
            f"- Source ref: `{ref}`\n"
            f"- Event: `{event_id}`\n"
            f"- Updated paths: {', '.join(f'`{path}`' for path in changed_paths)}\n\n"
            "This pull request is the integration boundary. Parent assembly and "
            "machine tests must pass before maintainer approval and merge."
        )
        current_pr = run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repository,
                "--head",
                branch,
                "--state",
                "open",
                "--json",
                "number,url",
                "--jq",
                ".[0].url // empty",
            ],
            ROOT,
        ).stdout.strip()
        if current_pr:
            run(
                [
                    "gh",
                    "pr",
                    "edit",
                    current_pr,
                    "--repo",
                    repository,
                    "--title",
                    f"chore(auto): update {child_repository} to {commit[:12]}",
                    "--body",
                    body,
                ],
                ROOT,
            )
            return f"{repository}: updated {current_pr} ({', '.join(changed_paths)})"

        pull_request = run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                repository,
                "--head",
                branch,
                "--base",
                default_branch,
                "--title",
                f"chore(auto): update {child_repository} to {commit[:12]}",
                "--body",
                body,
            ],
            ROOT,
        ).stdout.strip()
        return f"{repository}: created {pull_request} ({', '.join(changed_paths)})"


def main() -> int:
    if not os.environ.get("GH_TOKEN"):
        raise SystemExit("GH_TOKEN is required for parent repository read/write and pull request creation.")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise SystemExit("GITHUB_EVENT_PATH is required.")

    try:
        repository, commit, ref, event_id = parse_event(Path(event_path))
        workflow_run_id = os.environ.get("GITHUB_RUN_ID", "repository-dispatch")
        parents = read_parents()
        results = [
            update_parent(parent, repository, commit, ref, event_id or workflow_run_id)
            for parent in parents
        ]
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Submodule pointer proposal failed: {exc}", file=sys.stderr)
        return 1

    print(f"Received {repository}@{commit} from {ref}.")
    for result in results:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
