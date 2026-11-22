import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / '.atelier/conduits/goal_loop/scripts/observe.py'
spec = importlib.util.spec_from_file_location('observe', SCRIPT)
observe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observe)


class ObserveTests(unittest.TestCase):
    def test_interrupted_runner_does_not_look_active_or_keep_accumulating_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identifier = 'b' * 32
            (root / 'progress.json').write_text(json.dumps({'status':'failed','finished_at':'1970-01-01T00:02:40Z'}))
            (root / 'input.yaml').write_text('goal: test')
            (root / 'outputs.yaml').write_text('initialize: ' + identifier)
            record = root / '.atelier/implementations/runs' / identifier
            record.mkdir(parents=True)
            (record / 'state.json').write_text(json.dumps({'status':'ACTIVE','started':100,'milestones':[],'events':[]}))
            run = observe.run_snapshot(root, root)
            self.assertEqual(run['summary']['status'], 'Run interrupted')
            self.assertEqual(run['summary']['elapsed'], '1 min')
            self.assertEqual(run['summary']['next'], '')

    def test_missing_modern_outcome_is_reported_as_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'progress.json').write_text('{}')
            (root / 'input.yaml').write_text('goal: test')
            (root / 'outputs.yaml').write_text('initialize: ' + 'c' * 32)
            run = observe.run_snapshot(root, root)
            self.assertEqual(run['summary']['status'], 'Report unavailable')
            self.assertEqual(run['summary']['done'], [])

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
            with patch.dict('os.environ', {'ATELIER_GLOBAL_ATELIER_DIR': str(project / 'global')}):
                run, = observe.snapshot(project)['runs']
            self.assertEqual(run['outcome']['status'], 'PARTIAL')
            self.assertEqual(run['elapsed_seconds'], 60)
            self.assertEqual(run['stage_seconds'], {'REVIEW':20})
            report = observe.report({'observed_at':'now','runs':[run]})
            self.assertIn('Partly complete', report)
            self.assertIn('1 improvement completed and reviewed', report)
            self.assertNotIn('Stage seconds', report)
            self.assertIn('outcome unproven', observe.report({'observed_at':'now','runs':[run]}, detailed=True))

    def test_global_journal_discovery_keeps_worktree_and_child_runs_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            project, other = root / 'worktree', root / 'somewhere-else'
            folder = root / 'global/projects/encoded-project'
            folder.mkdir(parents=True)
            parent = '20261121_abcdef01_goal_loop'
            child = '20261121_abcdef02_goal_iteration'
            unrelated = '20261121_abcdef03_goal_loop'

            def journal(flow_id, origin, parent_id, *events):
                created = {'flow_id': flow_id, 'project': str(origin), 'parent_flow_id': parent_id,
                           'inputs': {'goal': 'initial'}}
                rows = [('created', created), *events]
                (folder / f'{flow_id}.jsonl').write_text(''.join(
                    json.dumps({'version': 1, 'type': kind, 'data': data}) + '\n' for kind, data in rows))

            journal(parent, project, None, ('input', {'goal': 'updated'}), ('progress', {'status': 'running'}))
            journal(unrelated, other, None)
            journal(child, project, parent, ('progress', {'status': 'completed'}),
                    ('outputs', {'values': {'route': 'USAGE_PAUSED'}, 'files': {'usage': 'outputs/usage/100'}}))
            journal('20261121_abcdef04_goal_iteration', other, unrelated)
            blob = folder / child / 'outputs/usage/100'
            blob.parent.mkdir(parents=True)
            blob.write_text('USAGE_PAUSE ' + 'x' * 100_000)
            with (folder / f'{parent}.jsonl').open('a') as stream:
                stream.write('{"version": 1, "type": "progress", "data": {"status": "completed"}}')

            with patch.dict('os.environ', {'ATELIER_GLOBAL_ATELIER_DIR': str(root / 'global')}):
                with patch.object(observe, 'worktrees', return_value=[root, project]):
                    run, = observe.snapshot(root)['runs']
            self.assertEqual((run['name'], run['project'], run['goal'], run['status']),
                             (parent, str(project), 'updated', 'running'))
            tick, = run['ticks']
            self.assertEqual((tick['id'], tick['status'], tick['phase']), (child, 'completed', 'USAGE_PAUSED'))
            self.assertEqual(tick['usage'], blob.read_text())
            self.assertEqual(run['warnings'], [])

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
            self.assertIn('No applied review decision', observe.report({'observed_at':'now', 'runs':[run]}))

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
