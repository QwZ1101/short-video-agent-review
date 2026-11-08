import threading
import unittest
from workflow_scheduler import schedule, validate_graph


class SchedulerTests(unittest.TestCase):
    def test_parallel_branches_join_and_priority(self):
        barrier = threading.Barrier(2)
        calls, events = [], []
        tasks = [{'id': 'root', 'depends_on': [], 'role': 'r'},
                 {'id': 'video', 'depends_on': ['root'], 'role': 'v'},
                 {'id': 'metrics', 'depends_on': ['root'], 'role': 'm', 'priority': 10},
                 {'id': 'join', 'depends_on': ['video', 'metrics'], 'role': 'r'}]
        def branch(name):
            barrier.wait(timeout=3)
            calls.append(name)
        result = schedule(tasks, {'root': lambda: 1, 'video': lambda: branch('video'),
            'metrics': lambda: branch('metrics'), 'join': lambda: len(calls)}, on_event=lambda **e: events.append(e))
        self.assertEqual(result['join'], 2)
        self.assertEqual([e['task'] for e in events if e['operation'] == 'dispatch'], ['root', 'metrics', 'video', 'join'])

    def test_failure_does_not_run_downstream(self):
        called = []
        def fail():
            raise ValueError('fixture failure')
        with self.assertRaisesRegex(ValueError, 'fixture failure'):
            schedule([{'id': 'a', 'depends_on': []}, {'id': 'b', 'depends_on': ['a']}],
                     {'a': fail, 'b': lambda: called.append('b')})
        self.assertEqual(called, [])

    def test_role_capacity_serializes_independent_tasks(self):
        active = threading.Lock()
        def action():
            self.assertTrue(active.acquire(blocking=False))
            active.release()
        schedule([{'id': k, 'depends_on': [], 'role': 'same'} for k in ('a', 'b')], {'a': action, 'b': action})

    def test_cycles_and_missing_dependencies_rejected(self):
        for tasks in ([{'id': 'a', 'depends_on': ['a']}], [{'id': 'a', 'depends_on': ['missing']}],
                      [{'id': 'a', 'depends_on': []}, {'id': 'a', 'depends_on': []}]):
            with self.assertRaises(ValueError):
                validate_graph(tasks)
