from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def run(command: list[str], cwd: Path, timeout: int = 900) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return 124, f"Timed out after {timeout}s.\n{output}"
    except FileNotFoundError:
        return 127, f"Command not found: {command[0]}"


def npm_command(*args: str) -> list[str]:
    if os.name == "nt":
        return ["cmd", "/c", "npm.cmd", *args]
    return ["npm", *args]


def repository_slug(url: str) -> str:
    if url.startswith("git@"):
        url = url.split(":", 1)[1]
    else:
        parsed = urlparse(url)
        url = parsed.path.lstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    return url.rstrip("/").lower()


def event_commit() -> tuple[str, str, str] | None:
    if os.environ.get("GITHUB_EVENT_NAME") != "repository_dispatch":
        return None
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise ValueError("GITHUB_EVENT_PATH is required for repository_dispatch.")
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    payload = event.get("client_payload")
    if not isinstance(payload, dict):
        raise ValueError("repository_dispatch client_payload is missing.")
    repository = payload.get("repository")
    commit = payload.get("sha")
    ref = payload.get("ref", "")
    if not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        raise ValueError("Invalid or missing client_payload.repository.")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise ValueError("Invalid or missing client_payload.sha.")
    if not isinstance(ref, str) or not ref.startswith(("refs/heads/", "refs/tags/")):
        raise ValueError("Invalid client_payload.ref.")
    if run(["git", "check-ref-format", ref], ROOT)[0] != 0:
        raise ValueError("Invalid client_payload.ref.")
    return repository.lower(), commit.lower(), ref


def validate_commit(
    url: str,
    commit: str,
    path: str,
    report: Callable[[str], None],
    ref: str | None = None,
) -> tuple[bool, bool]:
    with tempfile.TemporaryDirectory(prefix="submodule-check-") as temp_dir:
        checkout = Path(temp_dir) / "repo"
        clone_code, clone_output = run(
            ["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none",
             "--no-checkout", url, str(checkout)],
            ROOT,
        )
        if clone_code:
            report(f"| {markdown_cell(path)} | Checkout | FAILED | {markdown_cell(clone_output[-500:])} |")
            return False, False

        if ref:
            fetch_code, fetch_output = run(
                [
                    "git", "fetch", "--quiet", "--unshallow",
                    "--filter=blob:none", "origin", ref,
                ],
                checkout,
            )
            if fetch_code:
                report(f"| {markdown_cell(path)} | Fetch ref | FAILED | {markdown_cell(fetch_output[-500:])} |")
                return False, False

        checkout_code, checkout_output = run(
            ["git", "checkout", "--quiet", "--detach", commit], checkout
        )
        if checkout_code:
            report(f"| {markdown_cell(path)} | Checkout | FAILED | {markdown_cell(checkout_output[-500:])} |")
            return False, False

        test_files = [
            item for item in checkout.rglob("*")
            if item.is_file()
            and ".git" not in item.parts
            and "node_modules" not in item.parts
            and any(part in {"test", "tests", "__tests__"} for part in item.parts)
        ]
        test_ran = False
        checks_passed = True
        package_files = [
            item for item in checkout.rglob("package.json")
            if ".git" not in item.parts and "node_modules" not in item.parts
        ]
        package_test_configured = False
        for package_file in package_files:
            package_dir = package_file.parent
            package = json.loads(package_file.read_text(encoding="utf-8"))
            scripts = package.get("scripts", {})
            has_test_script = "test" in scripts
            has_build_script = "build" in scripts
            has_lint_script = "lint" in scripts
            package_test_configured = package_test_configured or has_test_script
            if not has_test_script and not has_build_script and not has_lint_script:
                continue
            relative_dir = package_dir.relative_to(checkout)
            check_name = str(relative_dir) if str(relative_dir) != "." else "root"
            if not (package_dir / "package-lock.json").is_file():
                report(f"| {markdown_cell(path)} | npm checks ({check_name}) | NOT RUN | package-lock.json missing; reproducible install unavailable |")
                checks_passed = False
                continue
            code, output = run(npm_command("ci"), package_dir)
            if code:
                report(f"| {markdown_cell(path)} | npm ci ({check_name}) | FAILED | {markdown_cell(output[-500:])} |")
                checks_passed = False
                continue
            if has_test_script:
                test_ran = True
                code, output = run(npm_command("test"), package_dir)
                report(f"| {markdown_cell(path)} | npm test ({check_name}) | {'PASSED' if code == 0 else 'FAILED'} | {markdown_cell(output[-500:])} |")
                checks_passed = checks_passed and code == 0
            if has_build_script:
                code, output = run(npm_command("run", "build"), package_dir)
                report(f"| {markdown_cell(path)} | npm run build ({check_name}) | {'PASSED' if code == 0 else 'FAILED'} | {markdown_cell(output[-500:])} |")
                checks_passed = checks_passed and code == 0
            if has_lint_script:
                code, output = run(npm_command("run", "lint"), package_dir)
                report(f"| {markdown_cell(path)} | npm run lint ({check_name}) | {'PASSED' if code == 0 else 'FAILED'} | {markdown_cell(output[-500:])} |")
                checks_passed = checks_passed and code == 0

        python_tests = [
            item for item in test_files if item.suffix == ".py"
        ]
        if not package_test_configured and python_tests and shutil.which("pytest"):
            test_ran = True
            code, output = run([sys.executable, "-m", "pytest", "-q"], checkout)
            report(f"| {markdown_cell(path)} | pytest | {'PASSED' if code == 0 else 'FAILED'} | {markdown_cell(output[-500:])} |")
            checks_passed = checks_passed and code == 0
        elif not test_ran and not python_tests and not package_test_configured:
            report(f"| {markdown_cell(path)} | Tests | NOT CONFIGURED | No recognized test command or Python test files found |")
        elif python_tests and not package_test_configured and not shutil.which("pytest"):
            report(f"| {markdown_cell(path)} | Tests | NOT RUN | Python test files exist but pytest is unavailable |")
            checks_passed = False

        if any(
            item.is_file() and item.suffix == ".py"
            for item in checkout.rglob("*.py")
            if ".git" not in item.parts and "node_modules" not in item.parts
        ):
            code, output = run([
                sys.executable, "-m", "compileall", "-q",
                "-x", r"(^|[\\/])(\.git|node_modules)([\\/]|$)",
                str(checkout),
            ], checkout)
            report(f"| {markdown_cell(path)} | Python syntax | {'PASSED' if code == 0 else 'FAILED'} | {markdown_cell(output[-500:]) or 'compileall completed'} |")
            checks_passed = checks_passed and code == 0

        report(
            f"| {markdown_cell(path)} | Contract | NOT VERIFIED | "
            "atelier-core contract docs are not accessible to this workflow; no automated contract checker is configured |"
        )
        return checks_passed, test_ran


def main() -> int:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    summary = open(summary_path, "a", encoding="utf-8") if summary_path else None

    def report(text: str) -> None:
        print(text)
        if summary:
            summary.write(text + "\n")

    report("## Submodule update monitor")
    report("| Submodule | Pinned commit | Remote/event commit | Result |")
    report("|---|---|---|---|")

    git("submodule", "init")
    entries = git("config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$")
    modules: list[tuple[str, str]] = []
    for line in entries.splitlines():
        match = re.match(r"submodule\.(.+)\.path\s+(.+)$", line)
        if match:
            modules.append((match.group(1), match.group(2)))

    outdated: list[str] = []
    errors: list[str] = []
    pending_checks: list[tuple[str, str, str, str | None]] = []
    try:
        triggered_commit = event_commit()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid repository_dispatch payload: {exc}") from exc

    if triggered_commit:
        event_repository, event_sha, event_ref = triggered_commit
        matched = False
        for name, path in modules:
            url = git("config", "--get", f"submodule.{name}.url")
            if repository_slug(url) != event_repository:
                continue
            matched = True
            pinned = git("rev-parse", f"HEAD:{path}")
            state = "Already pinned" if pinned == event_sha else "PINNED POINTER DIFFERS"
            report(
                f"| {markdown_cell(path)} | `{pinned[:12]}` | "
                f"`{event_sha[:12]}` | {state} |"
            )
            pending_checks.append((path, url, event_sha, event_ref))
            if pinned != event_sha:
                outdated.append(path)
            break
        if not matched:
            raise SystemExit(
                f"Dispatch repository {event_repository!r} is not a configured submodule."
            )
        report("")
        report(f"### Validation for `{event_repository}@{event_sha[:12]}` ({markdown_cell(event_ref)})")
    else:
        for name, path in modules:
            try:
                url = git("config", "--get", f"submodule.{name}.url")
                pinned = git("rev-parse", f"HEAD:{path}")
                refs = git("ls-remote", "--symref", url, "HEAD")
                head_refs = [
                    line.split("\t", 1)[0]
                    for line in refs.splitlines()
                    if line.endswith("\tHEAD") and not line.startswith("ref:")
                ]
                if not head_refs:
                    raise ValueError("Remote returned no HEAD ref.")
                remote_head = head_refs[0]
                is_current = pinned == remote_head
                result = "Aligned" if is_current else "HEAD MISMATCH"
                report(
                    f"| {markdown_cell(path)} | `{pinned[:12]}` | "
                    f"`{remote_head[:12]}` | {result} |"
                )
                if not is_current:
                    outdated.append(path)
                    pending_checks.append((path, url, remote_head, None))
            except (OSError, ValueError, subprocess.CalledProcessError, StopIteration) as exc:
                detail = (
                    exc.stderr.strip()
                    if isinstance(exc, subprocess.CalledProcessError) and exc.stderr
                    else str(exc) or "Could not resolve the remote default-branch HEAD."
                )
                errors.append(path)
                report(f"| {markdown_cell(path)} | — | — | ERROR: {markdown_cell(detail)} |")

    checks_failed: list[str] = []
    tests_not_run: list[str] = []
    if pending_checks:
        if not triggered_commit:
            report("")
            report("### Checks for commits newer than the pinned submodule revision")
        report("| Submodule | Check | Status | Details |")
        report("|---|---|---|---|")
        for path, url, commit, ref in pending_checks:
            passed, test_ran = validate_commit(url, commit, path, report, ref)
            if not passed:
                checks_failed.append(path)
            if not test_ran:
                tests_not_run.append(path)

    if outdated or errors or checks_failed:
        report("")
        report(
            f"Found {len(outdated)} submodule(s) with a pending pointer update, "
            f"{len(errors)} monitor error(s), and {len(checks_failed)} failed validation(s)."
        )
        if tests_not_run:
            report("Tests were not run for: " + ", ".join(tests_not_run))
        report("Contract compliance remains unverified; passing syntax/build checks are not a contract review.")
        if summary:
            summary.close()
        return 1

    report("")
    if triggered_commit:
        report("The dispatched commit is pinned and all configured checks passed.")
    else:
        report(f"All {len(modules)} submodules are aligned with their remote default-branch HEAD.")
        report("No newer submodule commits were found; no new-commit checks were needed.")
    if summary:
        summary.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else str(exc)
        print(f"Submodule monitoring failed: {detail}", file=sys.stderr)
        raise SystemExit(1)
