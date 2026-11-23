"""Tests for the non-backtracking forgiveness comment parser."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sins.policy import _parse_forgiveness


class ParseForgivenessTests(unittest.TestCase):
    """Cover the id list, separator and reason handling."""

    def test_multiple_ids_and_reason(self) -> None:
        self.assertEqual(_parse_forgiveness("sins: forgive a-b, c -- because"), ({"a-b", "c"}, "because"))

    def test_missing_separator_is_rejected(self) -> None:
        self.assertIsNone(_parse_forgiveness("sins: forgive a-b"))

    def test_empty_reason_is_returned_empty(self) -> None:
        self.assertEqual(_parse_forgiveness("sins:forgive x --"), ({"x"}, ""))

    def test_invalid_id_is_rejected(self) -> None:
        self.assertIsNone(_parse_forgiveness("sins: forgive Bad_ID! -- reason"))

    def test_long_input_is_fast(self) -> None:
        self.assertIsNone(_parse_forgiveness("sins: forgive " + "a-" * 20000 + "! -- reason"))
        self.assertEqual(_parse_forgiveness("sins: forgive " + "a-" * 20000 + "a -- reason")[1], "reason")


if __name__ == "__main__":
    unittest.main()
