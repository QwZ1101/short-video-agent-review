import errno
import unittest
from unittest.mock import patch

from check_materials import PROJECT_ROOT
from launch_review import previous_port
from review_server import create_server


class PreviewRecoveryTests(unittest.TestCase):
    def test_port_requires_matching_workspace_and_loopback(self):
        state = {"workspace": str(PROJECT_ROOT), "url": "http://127.0.0.1:19433"}
        self.assertEqual(previous_port(state), 19433)
        for url in ("https://evil.test:19433", "http://user@127.0.0.1:19433", "http://127.0.0.1:bad"):
            self.assertEqual(previous_port(dict(state, url=url)), 0)
        self.assertEqual(previous_port(dict(state, workspace=str(PROJECT_ROOT / "other"))), 0)

    def test_occupied_preferred_port_falls_back_without_stopping_owner(self):
        with patch("review_server.ReviewServer", side_effect=[OSError(errno.EADDRINUSE, "occupied"), "server"]) as server:
            self.assertEqual(create_server(preferred_port=19433), "server")
            self.assertEqual([call.args for call in server.call_args_list], [(19433,), (0,)])

    def test_explicit_port_and_other_errors_do_not_silently_fallback(self):
        for port, code in ((19433, errno.EADDRINUSE), (0, errno.EACCES)):
            with patch("review_server.ReviewServer", side_effect=OSError(code, "failure")) as server:
                with self.assertRaises(OSError):
                    create_server(port=port, preferred_port=19433)
                self.assertEqual(server.call_count, 1)
