import unittest

from submodule_pr_relay import validate_merge_tree


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
            validate_merge_tree(self.base, self.head, merge, self.tree),
            self.tree,
        )

    def test_rejects_merge_with_a_different_parent(self):
        merge = {
            "parents": [{"sha": "d" * 40}, {"sha": self.head}],
            "tree": {"sha": self.tree},
        }
        with self.assertRaises(SystemExit):
            validate_merge_tree(self.base, self.head, merge, self.tree)

    def test_rejects_merge_with_a_different_tree(self):
        merge = {
            "parents": [{"sha": self.base}, {"sha": self.head}],
            "tree": {"sha": "e" * 40},
        }
        with self.assertRaises(SystemExit):
            validate_merge_tree(self.base, self.head, merge, self.tree)

    def test_rejects_missing_tree(self):
        merge = {
            "parents": [{"sha": self.base}, {"sha": self.head}],
            "tree": {},
        }
        with self.assertRaises(SystemExit):
            validate_merge_tree(self.base, self.head, merge, self.tree)


if __name__ == "__main__":
    unittest.main()
