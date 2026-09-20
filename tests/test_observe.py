import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / '.atelier/conduits/goal_loop/scripts/observe.py'
spec = importlib.util.spec_from_file_location('observe', SCRIPT)
observe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observe)


class ObserveTests(unittest.TestCase):
    def test_outcome_is_loaded_by_run_id_not_latest_worktree_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            flow = project / '.atelier/flows/old_goal_loop'
            flow.mkdir(parents=True)
            identifier = 'a' * 32
            (flow / 'progress.json').write_text('{"status":"completed"}')
            (flow / 'input.yaml').write_text('goal: first useful result')
            (flow / 'outputs.yaml').write_text('initialize: ' + identifier)
            record = project / '.atelier/implementations/runs' / identifier
            record.mkdir(parents=True)
            state = {'status':'PARTIAL','started':100,'finished':160,'priority':'onboarding',
                     'usage_text':'recorded usage','events':[{'stage':'REVIEW','duration_seconds':20}],
                     'milestones':[{'status':'ACCEPT'}]}
            (record / 'state.json').write_text(json.dumps(state))
            (record / 'handoff.md').write_text('Before: failed\nAfter: one milestone accepted; outcome unproven')
            run = observe.run_snapshot(flow, project)
            self.assertEqual(run['outcome']['status'], 'PARTIAL')
            self.assertEqual(run['elapsed_seconds'], 60)
            self.assertEqual(run['stage_seconds'], {'REVIEW':20})
            report = observe.report({'observed_at':'now','runs':[run]})
            self.assertIn('outcome unproven', report)
            self.assertIn('Current priority: onboarding', report)

    def test_partial_records_do_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'progress.json').write_text('{')
            run = observe.run_snapshot(root, root)
            self.assertEqual(run['status'], 'unknown')
            self.assertTrue(run['warnings'])

    def test_pauses_are_not_ideas_and_review_is_not_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'progress.json').write_text('{"status":"running"}')
            (root / 'input.yaml').write_text('goal: test')
            for name, output in [('a', 'usage: USAGE_PAUSE'),
                                 ('b', 'next_id: "0001"\njudge: "VERDICT: KEEP"')]:
                tick = root / 'flows' / (name + '_goal_iteration')
                tick.mkdir(parents=True)
                (tick / 'progress.json').write_text('{}')
                (tick / 'outputs.yaml').write_text(output)
            run = observe.run_snapshot(root, root)
            self.assertEqual(run['counts'], dict(ticks=2, ideas=1, kept=0, discarded=0, unreviewed=1, pauses=1))
            self.assertIn('UNREVIEWED', observe.report({'observed_at':'now', 'runs':[run]}))

    def test_legacy_namespace_precedes_shared_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            key = project.name + '-' + observe.hashlib.sha256(str(project).encode()).hexdigest()[:8]
            root = project / '.atelier/implementations'
            imported = root / 'imported' / key
            imported.mkdir(parents=True)
            (root / '0001.md').write_text('new shared idea')
            (imported / '0001.md').write_text('old worktree idea')
            self.assertEqual(observe.decision(project, '0001', False), 'old worktree idea')
            self.assertEqual(observe.decision(project, '0001', True), 'new shared idea')
            self.assertEqual(observe.decision(project, '../secret', True), '')


if __name__ == '__main__':
    unittest.main()
