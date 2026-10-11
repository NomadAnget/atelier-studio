from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from propose_submodule_updates import (
    matching_modules,
    parse_event,
    repository_slug,
    short_branch,
)


class ProposeSubmoduleUpdateTests(unittest.TestCase):
    def test_parse_event_returns_repository_commit_and_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            event_path = Path(temp_dir) / "event.json"
            event_path.write_text(
                json.dumps(
                    {
                        "client_payload": {
                            "repository": "NomadAnget/atelier-engines",
                            "sha": "a" * 40,
                            "ref": "refs/heads/master",
                            "event_id": "1234-1",
                        }
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                parse_event(event_path),
                ("nomadanget/atelier-engines", "a" * 40, "refs/heads/master", "1234-1"),
            )

    def test_parse_event_rejects_invalid_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            event_path = Path(temp_dir) / "event.json"
            event_path.write_text(
                json.dumps(
                    {
                        "client_payload": {
                            "repository": "NomadAnget/atelier-engines",
                            "sha": "not-a-commit",
                            "ref": "refs/heads/master",
                            "event_id": "1234-1",
                        }
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "client_payload.sha"):
                parse_event(event_path)

    def test_repository_slug_handles_github_relative_resolution(self) -> None:
        self.assertEqual(
            repository_slug("https://github.com/NomadAnget/atelier-engines.git"),
            "nomadanget/atelier-engines",
        )

    def test_repository_slug_maps_forgejo_owner_to_github_owner(self) -> None:
        self.assertEqual(
            repository_slug("https://forgejo.hurcaguari.top/Carnation/atelier-engines.git"),
            "nomadanget/atelier-engines",
        )

    def test_branch_is_stable_per_child_repository(self) -> None:
        self.assertEqual(
            short_branch("NomadAnget/atelier-platform_adapters"),
            "automation/submodule-atelier-platform-adapters",
        )

    def test_matching_modules_resolves_relative_github_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            child = parent / "atelier-engines"
            child.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=child, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "--quiet", "-m", "Initial commit"],
                cwd=child,
                check=True,
            )
            child_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=child,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(["git", "init", "--quiet"], cwd=parent, check=True)
            subprocess.run(
                [
                    "git",
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/NomadAnget/atelier-studio.git",
                ],
                cwd=parent,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"160000,{child_commit},src/engines",
                ],
                cwd=parent,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "config",
                    "--file",
                    ".gitmodules",
                    "submodule.engines.path",
                    "src/engines",
                ],
                cwd=parent,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "config",
                    "--file",
                    ".gitmodules",
                    "submodule.engines.url",
                    "../atelier-engines.git",
                ],
                cwd=parent,
                check=True,
            )
            subprocess.run(["git", "add", ".gitmodules"], cwd=parent, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--quiet", "-m", "Add submodule"],
                cwd=parent,
                check=True,
            )

            self.assertEqual(
                matching_modules(parent, "nomadanget/atelier-engines"),
                [("engines", "src/engines", "https://github.com/NomadAnget/atelier-engines.git")],
            )


if __name__ == "__main__":
    unittest.main()
