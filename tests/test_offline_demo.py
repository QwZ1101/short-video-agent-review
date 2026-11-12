import tempfile
import unittest
from pathlib import Path
from offline_demo import prepare
from human_review import ReviewStore, digest


class OfflineDemoTests(unittest.TestCase):
    def test_portable_isolated_replay_and_reopening_preserves_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = prepare(folder)
            store = ReviewStore(Path(folder))
            detail = store.detail('run-demo')
            self.assertEqual(detail['task']['tutorial_goal'], '点赞')
            self.assertEqual(detail['status'], 'awaiting_human_review')
            before = digest(root / 'workflow.json')
            self.assertEqual(prepare(folder), root)
            self.assertEqual(digest(root / 'workflow.json'), before)
            self.assertIn('合成', str(detail))
