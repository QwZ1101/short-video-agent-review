import copy
import json
from pathlib import Path
import tempfile
import unittest

from feedback_policy import route_feedback
from workflow_definition import load_template, template_identity
from workflow_plan import build_plan
from run_workflow import run_script


class DefinitionTests(unittest.TestCase):
    def test_plan_carries_real_definition_and_prompt_fingerprints(self):
        plan = build_plan({'sample_id': 'synthetic', 'tutorial_goal': '点赞'}, 4)
        self.assertEqual(plan['template_identity'], template_identity(load_template()))
        self.assertEqual(len(plan['template_identity']['prompt_sha256']), 4)
        self.assertEqual(plan['tasks'][3]['role'], 'content_reviewer')

    def test_reject_unsafe_tools_broken_dependencies_and_missing_roles(self):
        original = load_template()
        variants = []
        changed = copy.deepcopy(original)
        changed['tasks'][0]['tool'] = 'arbitrary.py'
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed['tasks'][3]['depends_on'] = []
        variants.append(changed)
        changed = copy.deepcopy(original)
        del changed['agents']['evidence_verifier']
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed['feedback_policy']['max_rounds'] = 99
        variants.append(changed)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'template.json'
            for value in variants:
                path.write_text(json.dumps(value), encoding='utf-8')
                with self.subTest(value=value), self.assertRaises(ValueError):
                    load_template(path)

    def test_template_change_stops_before_subprocess(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'plan.json').write_text(json.dumps({'template_identity': {'sha256': 'changed'}}))
            with self.assertRaisesRegex(ValueError, '发生变化'):
                run_script(root, 'analysis', 'does-not-exist.py', [])
            self.assertFalse((root / 'analysis.log').exists())

    def test_feedback_routing_and_no_false_success(self):
        tasks = [{'target': 'summary', 'status': 'supported'}, {'target': 'finding-1', 'status': 'revise'}]
        result = route_feedback(tasks, 0, 1, 2)
        self.assertEqual(result['action'], 'repair')
        self.assertEqual(result['repair_targets'], ['finding-1'])
        self.assertEqual(route_feedback(tasks, 0, 1, 1)['action'], 'budget_limit')
        self.assertEqual(route_feedback(tasks, 1, 1, 2)['action'], 'round_limit')
        tasks.append({'target': 'finding-2', 'status': 'insufficient_evidence'})
        self.assertEqual(route_feedback(tasks, 0, 1, 2)['action'], 'awaiting_evidence')
        for invalid in ([], [{'status': 'unknown'}]):
            with self.assertRaises(ValueError):
                route_feedback(invalid, 0, 1, 2)
