from __future__ import annotations

import contextlib
import io
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from repotrials import console


@dataclass
class Payload:
    value: int


class ConsoleTests(unittest.TestCase):
    def test_plain_status_output_and_json(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(console, "supports_color", return_value=False),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            console.title("Title")
            console.success("green")
            console.warning("yellow")
            console.info("blue")
            console.failure("red")
            console.print_json(Payload(7))

        self.assertIn("OK  green", stdout.getvalue())
        self.assertIn('"value": 7', stdout.getvalue())
        self.assertIn("FAIL  red", stderr.getvalue())

    def test_table_alignment_and_invalid_rows(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            console.table(("name", "score"), (("long-name", 1), ("x", 20)))
        self.assertIn("long-name  1", stdout.getvalue())

        with self.assertRaises(ValueError):
            console.table(("one", "two"), (("only-one",),))

    def test_untrusted_terminal_controls_are_rendered_inert(self) -> None:
        hostile = "before\x1b]52;c;Y2xpcGJvYXJk\x07\u202eevil\nafter"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(console, "supports_color", return_value=False),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            console.title(hostile)
            console.success(hostile)
            console.warning(hostile)
            console.info(hostile)
            console.failure(hostile)
            console.table(("value",), ((hostile,),))

        rendered = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\x07", rendered)
        self.assertNotIn("\u202e", rendered)
        self.assertIn(r"\x1b]52;c;Y2xpcGJvYXJk\x07\u202eevil\x0aafter", rendered)

    def test_flattens_nested_mappings(self) -> None:
        self.assertEqual(
            console.flatten_mapping({"outer": {"inner": 1}, "plain": 2}),
            [("outer.inner", 1), ("plain", 2)],
        )


if __name__ == "__main__":
    unittest.main()
