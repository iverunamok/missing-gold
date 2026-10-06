import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

import ytlib
from commands import init as init_cmd


class Args:
    def __init__(self, key="", force=False):
        self.key = key
        self.force = force


class InitTests(unittest.TestCase):
    def setUp(self):
        self._quiet = contextlib.redirect_stdout(io.StringIO())
        self._quiet.__enter__()

    def tearDown(self):
        self._quiet.__exit__(None, None, None)

    def test_writes_env_seen_and_folder(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(init_cmd, "HERE", d):
            self.assertEqual(init_cmd.cmd_init(Args(key="abc")), 0)
            self.assertEqual(open(os.path.join(d, ".env")).read(), "YOUTUBE_API_KEY=abc\n")
            self.assertTrue(open(os.path.join(d, "seen.md")).read().startswith("| date | id |"))
            self.assertTrue(os.path.isdir(os.path.join(d, "shortlists")))

    def test_keeps_existing_env_without_force(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(init_cmd, "HERE", d):
            open(os.path.join(d, ".env"), "w").write("YOUTUBE_API_KEY=old\n")
            init_cmd.cmd_init(Args(key="new"))
            self.assertEqual(open(os.path.join(d, ".env")).read(), "YOUTUBE_API_KEY=old\n")
            init_cmd.cmd_init(Args(key="new", force=True))
            self.assertEqual(open(os.path.join(d, ".env")).read(), "YOUTUBE_API_KEY=new\n")

    def test_no_key_and_no_tty_exits(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(init_cmd, "HERE", d), \
                mock.patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(SystemExit):
                init_cmd.cmd_init(Args())


if __name__ == "__main__":
    unittest.main()
