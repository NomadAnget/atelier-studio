import unittest
from unittest.mock import patch

import submodule_pr_relay as relay


class ValidateMergeTreeTests(unittest.TestCase):
    def setUp(self):
        self.base = "a" * 40
        self.head = "b" * 40
        self.tree = "c" * 40

    def test_accepts_exact_checked_merge_tree_and_parents(self):
        merge = {
            "parents": [{"sha": self.base}, {"sha": self.head}],
            "tree": {"sha": self.tree},
        }
        self.assertEqual(
            relay.validate_merge_tree(self.base, self.head, merge, self.tree),
            self.tree,
        )

    def test_rejects_wrong_parent(self):
        merge = {
            "parents": [{"sha": "d" * 40}, {"sha": self.head}],
            "tree": {"sha": self.tree},
        }
        with self.assertRaises(SystemExit):
            relay.validate_merge_tree(self.base, self.head, merge, self.tree)

    def test_rejects_different_tree(self):
        merge = {
            "parents": [{"sha": self.base}, {"sha": self.head}],
            "tree": {"sha": "e" * 40},
        }
        with self.assertRaises(SystemExit):
            relay.validate_merge_tree(self.base, self.head, merge, self.tree)

    def test_rejects_missing_tree(self):
        merge = {
            "parents": [{"sha": self.base}, {"sha": self.head}],
            "tree": {},
        }
        with self.assertRaises(SystemExit):
            relay.validate_merge_tree(self.base, self.head, merge, self.tree)


class PromoteChildPrTests(unittest.TestCase):
    def setUp(self):
        self.repo = "NomadAnget/atelier-web"
        self.number = 7
        self.base = "a" * 40
        self.head = "b" * 40
        self.merge_sha = "d" * 40
        self.tree = "c" * 40
        self.live = {
            "state": "open",
            "draft": False,
            "head": {"repo": {"full_name": self.repo}, "sha": self.head},
            "base": {"repo": {"full_name": self.repo}, "ref": "master", "sha": self.base},
            "merge_commit_sha": self.merge_sha,
        }
        self.merge = {
            "parents": [{"sha": self.base}, {"sha": self.head}],
            "tree": {"sha": self.tree},
        }

    def test_does_not_advance_master_when_merge_tree_differs(self):
        with patch.object(relay, "api", side_effect=[self.live, self.merge]) as api, \
             patch.object(relay, "ref_sha", return_value=self.base), \
             patch.object(relay, "commit_tree", return_value="e" * 40):
            with self.assertRaises(SystemExit):
                relay.promote_child_pr(self.repo, self.number, self.head, "e" * 40)
        self.assertFalse(any(call.args[0] == "PATCH" for call in api.call_args_list))

    def test_advances_only_to_the_exact_checked_merge_candidate(self):
        merged = {"state": "closed", "merged": True}
        with patch.object(
            relay, "api", side_effect=[self.live, self.merge, self.live, None, merged]
        ) as api, \
             patch.object(relay, "ref_sha", side_effect=[self.base, self.base, self.merge_sha]), \
             patch.object(relay, "commit_tree", return_value=self.tree):
            relay.promote_child_pr(self.repo, self.number, self.head, self.tree)

        patch_calls = [call for call in api.call_args_list if call.args[0] == "PATCH"]
        self.assertEqual(len(patch_calls), 1)
        self.assertEqual(
            patch_calls[0].args[1],
            f"/repos/{self.repo}/git/refs/heads/master",
        )
        self.assertEqual(
            patch_calls[0].args[2],
            {"sha": self.merge_sha, "force": False},
        )


class PromoteRegularPrTests(unittest.TestCase):
    def setUp(self):
        self.repo = "NomadAnget/atelier-studio"
        self.base = "a" * 40
        self.head = "b" * 40
        self.merge_sha = "d" * 40
        self.tree = "c" * 40
        self.event = {
            "repository": {"full_name": self.repo},
            "pull_request": {
                "number": 42,
                "head": {"sha": self.head},
                "base": {"sha": self.base},
            },
        }
        self.live = {
            "state": "open",
            "draft": False,
            "base": {"ref": "master", "sha": self.base},
            "head": {"sha": self.head},
            "merge_commit_sha": self.merge_sha,
        }
        self.merge = {
            "parents": [{"sha": self.base}, {"sha": self.head}],
            "tree": {"sha": self.tree},
        }

    def test_refuses_a_different_tree_before_advancing_master(self):
        with patch.object(relay, "api", side_effect=[self.live, self.merge]) as api, patch.object(relay, "ref_sha", return_value=self.base):
            with self.assertRaises(SystemExit):
                relay.promote_regular_pr(self.event, self.merge_sha, "e" * 40)
        self.assertFalse(any(call.args[0] == "PATCH" for call in api.call_args_list))

    def test_refuses_a_new_live_merge_candidate(self):
        with patch.object(relay, "api", return_value=self.live) as api, patch.object(relay, "ref_sha", return_value=self.base):
            with self.assertRaises(SystemExit):
                relay.promote_regular_pr(self.event, "f" * 40, self.tree)
        self.assertEqual(len(api.call_args_list), 1)

    def test_advances_only_after_exact_candidate_tree_is_rechecked(self):
        with patch.object(
            relay, "api", side_effect=[self.live, self.merge, self.live, None]
        ) as api, patch.object(relay, "ref_sha", side_effect=[self.base, self.base]):
            relay.promote_regular_pr(self.event, self.merge_sha, self.tree)
        patch_calls = [call for call in api.call_args_list if call.args[0] == "PATCH"]
        self.assertEqual(len(patch_calls), 1)
        self.assertEqual(
            patch_calls[0].args[2],
            {"sha": self.merge_sha, "force": False},
        )


if __name__ == "__main__":
    unittest.main()
