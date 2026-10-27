import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_storage import atomic_json


class StorageTests(unittest.TestCase):
    def test_temporary_windows_lock_retries_replace_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text('{"old":true}')
            error = PermissionError("sharing violation")
            error.winerror = 32
            replace = Path.replace
            calls = []
            def locked_once(source, target):
                calls.append(source)
                if len(calls) == 1:
                    raise error
                return replace(source, target)
            with patch.object(Path, "replace", locked_once), patch("local_storage.time.sleep") as sleep:
                atomic_json(path, {"new": True})
            self.assertEqual(json.loads(path.read_text()), {"new": True})
            self.assertEqual(len(calls), 2)
            sleep.assert_called_once()

    def test_persistent_lock_stops_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text('{"old":true}')
            error = PermissionError("access denied")
            error.winerror = 5
            with patch.object(Path, "replace", side_effect=error) as replace, patch("local_storage.time.sleep"):
                with self.assertRaises(PermissionError):
                    atomic_json(path, {"new": True})
            self.assertEqual(replace.call_count, 6)
            self.assertEqual(json.loads(path.read_text()), {"old": True})
            self.assertEqual(list(Path(temp).glob('*.tmp')), [])

    def test_other_permission_error_not_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(Path, "replace", side_effect=PermissionError("denied")) as replace:
                with self.assertRaises(PermissionError):
                    atomic_json(Path(temp) / "state.json", {})
            replace.assert_called_once()
