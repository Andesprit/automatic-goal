"""Exercise durable transitions in real linked worktrees and through Atelier's ACP runner.

No real agents or usage endpoints: subprocess fixtures write stage artifacts and make commits.
"""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONDUITS = ROOT / '.atelier/conduits'
GIT_ENV = {**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_SYSTEM': '/dev/null',
           'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t',
           'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS': '1'}
CHECK = {'command': 'test -f b.txt', 'exit_code': 0, 'summary': 'user path works', 'duration_seconds': .1}
BRIEF = {
    'goal_interpretation': 'Make the first workflow useful', 'outcome': 'A useful first result unaided',
    'baseline': ['Two manual steps; second currently fails'], 'success_criteria': ['First run succeeds'],
    'preserve': ['Existing login'], 'assumptions': [],
    'opportunities': [dict(id=n, title=n, benefit='fewer steps', evidence='observed failure',
                           effort='one short milestone', uncertainty='needs integration check')
                      for n in ('onboarding', 'diagnostics', 'docs')],
    'primary': 'onboarding', 'fallback': 'diagnostics',
    'selection_reason': 'It removes the first-run blocker', 'research': 'Actual failure reproduced; no external research needed'}
MILESTONE = {'title': 'First useful workflow', 'opportunity': 'onboarding', 'benefit': 'Unblocks new users',
             'ambition': 'bold', 'upside': 'New users succeed alone', 'risk': 'The runner may not allow it',
             'acceptance': ['First run succeeds'], 'scope': ['b.txt'], 'checks': ['test -f b.txt'],
             'estimated_seconds': 10}
SUPERVISE = {'action': 'BUILD', 'brief': BRIEF, 'reason': 'Highest observed benefit',
             'learning': 'Reproduced the initial failure', 'priority': 'First run', 'milestone': MILESTONE}
REVIEW = {'verdict': 'ACCEPT', 'ready_percent': 90, 'correctness': 'Checks pass', 'value': 'First run no longer fails',
          'reason': 'Demonstrated useful contribution', 'evidence': ['test -f b.txt'], 'checks': [CHECK], 'findings': []}
FINAL = {'status': 'ACHIEVED', 'before': 'First run failed', 'after': 'First run succeeds',
         'evidence': ['Run test -f b.txt'], 'checks': [CHECK],
         'criteria': [{'criterion': 'First run succeeds', 'met': True, 'evidence': 'First-run command succeeds'}],
         'limitations': [], 'remaining': [], 'reason': 'Baseline comparison demonstrates the outcome'}
USAGE_STUB = '''import json, os
from pathlib import Path
p = Path(__file__).with_name('meters.json')
values = json.loads(p.read_text()) if p.exists() else [99,99,99]
Path(os.environ['GOAL_USAGE_FILE']).write_text(json.dumps({'remaining':values}))
print('fixture usage: ' + str(values))
'''


def git(cwd, *args):
    return subprocess.check_output(['git', *args], cwd=cwd, env=GIT_ENV, text=True, stderr=subprocess.PIPE).strip()


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.main, self.wt = self.tmp / 'main', self.tmp / 'wt'
        git(self.tmp, 'init', '-q', '-b', 'main', 'main')
        (self.main / 'a.txt').write_text('a\n')
        git(self.main, 'add', 'a.txt')
        git(self.main, 'commit', '-qm', 'initial')
        git(self.main, 'worktree', 'add', '-q', str(self.wt), '-b', 'work')
        self.base = git(self.wt, 'rev-parse', 'HEAD')
        shutil.copytree(CONDUITS, self.wt / '.atelier/conduits', ignore=shutil.ignore_patterns('__pycache__'))
        self.scripts = self.wt / '.atelier/conduits/goal_iteration/scripts'
        (self.scripts / 'usage.py').write_text(USAGE_STUB)
        self.deadline = int(time.time()) + 3600
        self.run_id = None

    def cmd(self, *args, cwd=None, env=None):
        return subprocess.run([sys.executable, str(self.scripts / 'run.py'), *map(str, args)],
                              cwd=cwd or self.wt, env=env or GIT_ENV, capture_output=True, text=True)

    def init(self, reserve=20, revisions=2, buffer=5):
        result = self.cmd('init', self.deadline, reserve, revisions, buffer)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.run_id = result.stdout
        return result

    def state(self):
        return json.loads((self.wt / '.atelier/goal/state.json').read_text())

    def set_state(self, **updates):
        state = self.state()
        state.update(updates)
        (self.wt / '.atelier/goal/state.json').write_text(json.dumps(state))

    def time_up(self):
        self.set_state(finish_at=time.time() - 1)

    def route(self, expected=None):
        r = self.cmd('route', self.run_id or 'auto', self.deadline, 50, 50, 30, 1)
        self.assertEqual(r.returncode, 0, r.stderr)
        if expected:
            self.assertEqual(r.stdout, expected)
        return r.stdout

    def result(self, value):
        value = copy.deepcopy(value)
        value.setdefault('request_id', self.state()['request']['id'])
        if self.state()['phase'] in {'IMPLEMENT', 'REVIEW', 'FINALIZE'}:
            value.setdefault('head', git(self.wt, 'rev-parse', 'HEAD'))
        (self.wt / '.atelier/goal/result.json').write_text(json.dumps(value))
        r = self.cmd('apply')
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def commit(self, name='b.txt'):
        (self.wt / name).write_text(name + '\n')
        git(self.wt, 'add', name)
        git(self.wt, 'commit', '-qm', 'add ' + name)
        return git(self.wt, 'rev-parse', 'HEAD')

    def build(self):
        self.init()
        self.route('SUPERVISE')
        self.result(SUPERVISE)
        self.route('IMPLEMENT')
        self.commit()
        self.result({'status': 'READY', 'summary': 'Working path', 'checks': [CHECK], 'evidence': ['b.txt']})
        self.route('REVIEW')


class Guards(Repo):
    def test_setup_hours_and_worktree_contract(self):
        setup = yaml.safe_load((CONDUITS / 'goal_loop/conduit.yaml').read_text())['tasks'][0]['setup']['task']
        for hours, valid in [('1.5', True), ('0', False), ('721', False), ('abc', False), ('1.00001', False)]:
            r = subprocess.run(['bash', '-c', setup.replace('{{inputs.hours}}', hours)],
                               cwd=self.wt, env=GIT_ENV, capture_output=True, text=True)
            self.assertEqual(r.returncode == 0, valid, r.stderr)
            if valid:
                self.assertAlmostEqual(int(r.stdout), time.time() + 5400, delta=3)
        for cwd in [self.main, self.wt / '.atelier']:
            self.assertNotEqual(self.cmd('init', self.deadline, 20, 2, 5, cwd=cwd).returncode, 0)
        git(self.wt, 'checkout', '-q', '--detach')
        self.assertNotEqual(self.cmd('init', self.deadline, 20, 2, 5).returncode, 0)

    def test_refuses_dirty_tracked_journals_invalid_branch_and_missing_environment(self):
        env = {**GIT_ENV, 'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS': '0'}
        self.assertNotEqual(self.cmd('init', self.deadline, 20, 2, 5, env=env).returncode, 0)
        (self.wt / 'junk').write_text('keep me')
        self.assertIn('not clean', self.cmd('init', self.deadline, 20, 2, 5).stderr)
        (self.wt / 'junk').unlink()
        git(self.wt, 'add', '.atelier')
        self.assertIn('tracked', self.cmd('init', self.deadline, 20, 2, 5).stderr)
        git(self.wt, 'reset', '-q')
        git(self.wt, 'checkout', '-qb', "bad'branch")
        self.assertIn('branch name', self.cmd('init', self.deadline, 20, 2, 5).stderr)

    def test_refuses_submodules(self):
        git(self.tmp, 'init', '-q', '-b', 'main', 'sub')
        sub = self.tmp / 'sub'
        (sub / 'file').write_text('x')
        git(sub, 'add', '.')
        git(sub, 'commit', '-qm', 'init')
        git(self.wt, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(sub), 'sub')
        git(self.wt, 'commit', '-qam', 'submodule')
        self.assertIn('submodule', self.cmd('init', self.deadline, 20, 2, 5).stderr)

    def test_run_options_and_second_run_are_guarded(self):
        for values in [(4,2,5), (51,2,5), (20,6,5), (20,2,26)]:
            self.assertNotEqual(self.cmd('init', self.deadline, *values).returncode, 0)
        self.init()
        index = (self.wt / '.atelier/implementations/index.md').read_text()
        self.assertIn(self.run_id, index)
        self.assertIn('unfinished goal', self.cmd('init', self.deadline, 20, 2, 5).stderr)
        self.assertNotEqual(self.cmd('route', 'f' * 32, self.deadline, 50, 50, 30, 1).returncode, 0)
        self.assertNotEqual(self.cmd('route', self.run_id, self.deadline + 1, 50, 50, 30, 1).returncode, 0)

    def test_read_only_supervisor_cannot_change_code(self):
        self.init()
        self.route()
        (self.wt / 'a.txt').write_text('changed')
        self.assertEqual(self.result(SUPERVISE), 'GOAL_DONE')
        self.assertEqual(self.state()['status'], 'OPERATIONAL_FAILURE')
        self.assertEqual((self.wt / 'a.txt').read_text(), 'changed')


class Outcomes(Repo):
    def test_replayed_application_does_not_duplicate_the_milestone(self):
        self.init()
        self.route()
        self.result(SUPERVISE)
        self.assertEqual(self.cmd('apply').stdout, 'CONTINUE')
        self.assertEqual(len(self.state()['milestones']), 1)

    def test_first_implementation_can_include_several_commits(self):
        self.init()
        self.route()
        self.result(SUPERVISE)
        self.route('IMPLEMENT')
        self.commit()
        self.commit('c.txt')
        self.result({'status': 'READY', 'summary': 'Complete experience', 'checks': [CHECK], 'evidence': ['b.txt and c.txt']})
        self.route('REVIEW')
        self.assertEqual(git(self.wt, 'rev-list', '--count', self.base + '..HEAD'), '2')

    def test_repair_cannot_rewrite_the_reviewed_commit(self):
        self.build()
        self.result({**REVIEW, 'verdict': 'REVISE', 'findings': ['Fix recovery'], 'repair_estimate_seconds': 5})
        self.route('IMPLEMENT')
        git(self.wt, 'reset', '--hard', self.base)
        self.commit('replacement.txt')
        self.result({'status': 'READY', 'summary': 'Replaced history', 'checks': [CHECK], 'evidence': ['replacement.txt']})
        self.assertEqual(self.state()['status'], 'OPERATIONAL_FAILURE')
        self.assertTrue((self.wt / 'replacement.txt').exists())

    def test_reviewer_changes_are_preserved_and_never_accepted(self):
        self.build()
        (self.wt / 'b.txt').write_text('reviewer changed code')
        self.result(REVIEW)
        self.assertEqual(self.state()['status'], 'OPERATIONAL_FAILURE')
        self.assertEqual((self.wt / 'b.txt').read_text(), 'reviewer changed code')

    def test_failed_checks_cannot_be_reported_ready(self):
        self.init()
        self.route()
        self.result(SUPERVISE)
        self.route()
        self.commit()
        self.assertEqual(self.result({'status':'READY','summary':'Bug','checks':[{**CHECK,'exit_code':1}],'evidence':['b.txt']}), 'RECOVER')
        self.assertEqual(self.state()['phase'], 'IMPLEMENT')

    def test_multiple_commits_review_repair_and_final_demonstration(self):
        self.build()
        self.result({**REVIEW, 'verdict': 'REVISE', 'findings': ['Add recovery path; verify c.txt'], 'repair_estimate_seconds': 5})
        self.route('IMPLEMENT')
        self.commit('c.txt')
        self.result({'status': 'READY', 'summary': 'Recovery works', 'checks': [CHECK], 'evidence': ['b.txt and c.txt']})
        self.route('REVIEW')
        self.result(REVIEW)
        self.time_up()
        self.route('FINALIZE')
        self.assertEqual(self.result(FINAL), 'GOAL_DONE')
        state = self.state()
        self.assertEqual(state['status'], 'ACHIEVED')
        self.assertEqual(state['milestones'][0]['revisions'], 1)
        self.assertEqual(git(self.wt, 'rev-list', '--count', self.base + '..HEAD'), '2')
        self.assertLess(state['finished'], state['deadline'])
        handoff = (self.wt / '.atelier/goal/handoff.md').read_text()
        self.assertIn('First run failed', handoff)
        self.assertIn('First run succeeds', handoff)
        self.assertIn('Correctness:', handoff)

    def test_acceptance_is_not_overall_achievement_and_priorities_persist(self):
        self.build()
        self.result(REVIEW)
        self.assertEqual(self.state()['status'], 'ACTIVE')
        self.route('SUPERVISE')
        second = {**SUPERVISE, 'milestone': {**MILESTONE, 'title': 'Readable results'}}
        second.pop('brief')
        self.result(second)
        self.assertEqual(self.state()['brief'], BRIEF)
        self.assertEqual(len(self.state()['milestones']), 2)
        self.assertEqual(self.state()['milestones'][1]['base'], git(self.wt, 'rev-parse', 'HEAD'))

    def test_abandon_preserves_ref_and_restores_only_current_milestone(self):
        self.build()
        head = git(self.wt, 'rev-parse', 'HEAD')
        self.result({**REVIEW, 'verdict': 'ABANDON', 'reason': 'Hypothesis disproven'})
        state = self.state()
        self.assertEqual(git(self.wt, 'rev-parse', 'HEAD'), self.base)
        self.assertEqual(git(self.wt, 'rev-parse', state['milestones'][0]['checkpoint_ref']), head)
        self.assertEqual(state['phase'], 'SUPERVISE')
        self.assertIn('ABANDON', (self.wt / '.atelier/implementations/index.md').read_text())

    def test_exhausted_repair_budget_is_deferred_not_abandoned(self):
        self.build()
        self.set_state(max_revisions=0)
        self.result({**REVIEW, 'verdict': 'REVISE', 'findings': ['Fix recovery'], 'repair_estimate_seconds': 5})
        state = self.state()
        self.assertEqual(state['milestones'][0]['status'], 'DEFERRED')
        self.assertEqual(state['phase'], 'SUPERVISE')
        self.assertEqual(state['status'], 'ACTIVE')
        self.assertEqual(git(self.wt, 'rev-parse', 'HEAD'), self.base)

    def test_protocol_recovery_does_not_repeat_committed_work(self):
        self.init()
        self.route()
        self.result(SUPERVISE)
        self.route('IMPLEMENT')
        head = self.commit()
        self.assertEqual(self.result({'status': 'READY'}), 'RECOVER')
        request_id = self.state()['request']['id']
        self.route('IMPLEMENT')
        self.assertEqual(self.state()['request']['id'], request_id)
        self.result({'status': 'READY', 'summary': 'Already committed', 'checks': [CHECK], 'evidence': ['b.txt']})
        self.assertEqual(self.state()['phase'], 'REVIEW')
        self.assertEqual(git(self.wt, 'rev-parse', 'HEAD'), head)
        self.assertEqual(git(self.wt, 'rev-list', '--count', self.base + '..HEAD'), '1')

    def test_missing_stale_and_malformed_results_recover_then_stop_preserving_work(self):
        self.init()
        self.route()
        self.assertEqual(self.cmd('apply').stdout, 'RECOVER')
        self.assertEqual(self.result({**SUPERVISE, 'request_id': 'old'}), 'RECOVER')
        self.assertEqual(self.result({}), 'GOAL_DONE')
        self.assertEqual(self.state()['status'], 'OPERATIONAL_FAILURE')
        self.assertEqual(git(self.wt, 'rev-parse', 'HEAD'), self.base)

    def test_implementation_blocker_saves_the_attempt_and_the_run_continues(self):
        self.init()
        self.route()
        self.result(SUPERVISE)
        self.route('IMPLEMENT')
        self.commit('c.txt')
        (self.wt / 'b.txt').write_text('unfinished')
        self.result({'status': 'BLOCKED', 'reason': 'The runner rejects it: observed error X'})
        state = self.state()
        item = state['milestones'][0]
        self.assertEqual((state['status'], state['phase'], item['status']), ('ACTIVE', 'SUPERVISE', 'BLOCKED'))
        self.assertEqual(git(self.wt, 'rev-parse', 'HEAD'), self.base)
        self.assertFalse((self.wt / 'b.txt').exists())
        self.assertEqual(git(self.wt, 'show', item['checkpoint_ref'] + ':b.txt'), 'unfinished')
        self.assertEqual(git(self.wt, 'show', item['checkpoint_ref'] + ':c.txt'), 'c.txt')
        self.assertIn('observed error X', (self.wt / '.atelier/implementations/index.md').read_text())
        self.assertIn('Expected risk: The runner may not allow it. Not kept: The runner rejects it',
                      (self.wt / '.atelier/goal/handoff.md').read_text())
        self.route('SUPERVISE')

    def test_blocker_without_changes_needs_no_ref(self):
        self.init()
        self.route()
        self.result(SUPERVISE)
        self.route('IMPLEMENT')
        self.result({'status': 'BLOCKED', 'reason': 'Needs a product decision'})
        item = self.state()['milestones'][0]
        self.assertEqual(item['status'], 'BLOCKED')
        self.assertNotIn('checkpoint_ref', item)
        self.route('SUPERVISE')

    def test_supervisor_cannot_finish_early(self):
        self.build()
        self.result(REVIEW)
        self.route('SUPERVISE')
        self.assertEqual(self.result({**SUPERVISE, 'action': 'FINISH'}), 'RECOVER')
        self.assertIn('FINISH is not available', self.state()['reason'])
        self.route('SUPERVISE')

    def test_ideas_grow_at_every_checkpoint(self):
        self.build()
        self.result(REVIEW)
        self.route('SUPERVISE')
        idea = {**BRIEF['opportunities'][0], 'id': 'bolder'}
        second = {**SUPERVISE, 'new_opportunities': [idea],
                  'milestone': {**MILESTONE, 'opportunity': 'bolder', 'depends_on': [self.state()['milestones'][0]['id']]}}
        second.pop('brief')
        self.result(second)
        state = self.state()
        self.assertEqual([o['id'] for o in state['brief']['opportunities']][-1], 'bolder')
        self.assertEqual(state['phase'], 'IMPLEMENT')
        self.route('IMPLEMENT')

    def test_milestone_must_state_ambition_and_valid_dependencies(self):
        self.init()
        self.route()
        self.assertEqual(self.result({**SUPERVISE, 'milestone': {**MILESTONE, 'ambition': 'huge'}}), 'RECOVER')
        self.assertEqual(self.result({**SUPERVISE, 'milestone': {**MILESTONE, 'depends_on': ['9999']}}), 'RECOVER')
        self.result(SUPERVISE)
        self.assertEqual(self.state()['phase'], 'IMPLEMENT')

    def test_accept_with_notes_needs_seventy_percent_ready(self):
        self.build()
        self.assertEqual(self.result({**REVIEW, 'ready_percent': 60}), 'RECOVER')
        self.assertEqual(self.result({**REVIEW, 'ready_percent': 'most'}), 'RECOVER')
        self.result({**REVIEW, 'ready_percent': 70, 'notes': ['Label wraps at 320px']})
        state = self.state()
        self.assertEqual(state['milestones'][0]['status'], 'ACCEPT')
        handoff = (self.wt / '.atelier/goal/handoff.md').read_text()
        self.assertIn('Reviewer notes: Label wraps at 320px', handoff)
        self.assertIn(f"git cherry-pick {self.base[:12]}..{state['accepted_head'][:12]}", handoff)

    def test_final_achievement_requires_every_success_criterion(self):
        self.build()
        self.result(REVIEW)
        self.time_up()
        self.route('FINALIZE')
        self.assertEqual(self.result({**FINAL, 'criteria': []}), 'RECOVER')
        self.assertEqual(self.state()['status'], 'ACTIVE')
        self.assertEqual(self.result({**FINAL, 'status': 'PARTIAL', 'criteria': 'malformed'}), 'RECOVER')
        self.result({**FINAL, 'status': 'PARTIAL', 'remaining': ['First run not demonstrated']})
        self.assertEqual(self.state()['status'], 'PARTIAL')

    def test_integration_findings_get_bounded_repair_in_reserve(self):
        self.build()
        self.result(REVIEW)
        self.time_up()
        self.route('FINALIZE')
        self.result({**FINAL, 'status': 'REVISE', 'milestone': {**MILESTONE, 'title': 'Integration repair'}})
        self.route('IMPLEMENT')
        self.commit('c.txt')
        self.result({'status': 'READY', 'summary': 'Integrated repair', 'checks': [CHECK], 'evidence': ['c.txt']})
        self.route('REVIEW')
        self.result(REVIEW)
        self.route('FINALIZE')
        self.result(FINAL)
        self.assertEqual(self.state()['status'], 'ACHIEVED')


class Budgets(Repo):
    def test_failed_usage_reader_pauses_instead_of_failing_the_goal(self):
        self.init()
        (self.scripts / 'usage.py').write_text('raise RuntimeError("unavailable")')
        self.route('USAGE_PAUSED')
        self.assertEqual(self.state()['status'], 'ACTIVE')
        self.assertFalse(self.state()['usage']['ready'])

    def test_time_reserve_stops_a_selected_but_unstarted_feature(self):
        self.init()
        self.route()
        self.result(SUPERVISE)
        self.time_up()
        self.route('FINALIZE')
        self.assertEqual(self.state()['milestones'][0]['status'], 'DEFERRED')
        self.assertEqual(git(self.wt, 'rev-parse', 'HEAD'), self.base)

    def test_usage_buffer_waits_for_a_reset_instead_of_finishing(self):
        self.init()
        self.route()
        self.result(SUPERVISE)
        (self.scripts / 'meters.json').write_text('[52, 99, 99]')
        self.route('USAGE_PAUSED')
        self.assertEqual(self.state()['status'], 'ACTIVE')
        self.assertEqual(self.state()['milestones'][0]['status'], 'BUILDING')
        (self.scripts / 'meters.json').write_text('[90, 99, 99]')
        self.route('IMPLEMENT')

    def test_usage_buffer_still_allows_review_and_repairs(self):
        self.build()
        (self.scripts / 'meters.json').write_text('[52, 99, 99]')
        self.route('REVIEW')
        self.result({**REVIEW, 'verdict': 'REVISE', 'findings': ['Fix c'], 'repair_estimate_seconds': 5})
        self.route('IMPLEMENT')

    def test_selected_milestone_that_no_longer_fits_returns_to_supervisor(self):
        self.init()
        self.route()
        self.result(SUPERVISE)
        self.set_state(finish_at=time.time() + 5)
        self.route('SUPERVISE')
        self.assertEqual(self.state()['milestones'][0]['status'], 'DEFERRED')
        self.assertIn('smaller', self.state()['reason'])

    def test_estimate_must_fit_feature_budget(self):
        self.init()
        self.route()
        self.result({**SUPERVISE, 'milestone': {**MILESTONE, 'estimated_seconds': 4000}})
        self.assertEqual(self.state()['phase'], 'SUPERVISE')
        self.assertEqual(self.state()['milestones'], [])
        self.assertIn('choose a smaller one', self.state()['reason'])
        self.route('SUPERVISE')

    def test_missing_usage_pauses_and_does_not_launch_a_stage(self):
        self.init()
        (self.scripts / 'meters.json').write_text('[null, 99, 99]')
        self.route('USAGE_PAUSED')
        self.assertIsNone(self.state()['request'])
        (self.scripts / 'meters.json').write_text('[50, 50, 30]')
        self.route('USAGE_PAUSED')  # exactly floors permits finishing, but new work waits for the buffer
        self.time_up()
        self.route('FINALIZE')

    def test_deadline_writes_partial_handoff_without_launching_agents(self):
        self.init()
        self.deadline = int(time.time()) - 1
        self.set_state(deadline=self.deadline)
        self.route('GOAL_DONE')
        self.assertEqual(self.state()['status'], 'PARTIAL')
        self.assertTrue((self.wt / '.atelier/goal/handoff.md').exists())

    def test_repair_wait_cannot_consume_handoff_reserve(self):
        self.build()
        self.result({**REVIEW, 'verdict': 'REVISE', 'findings': ['Fix c'], 'repair_estimate_seconds': 30})
        self.set_state(handoff_seconds=3599)
        self.route('SUPERVISE')  # the supervisor may still pick a smaller idea before the reserve
        self.assertEqual(self.state()['milestones'][0]['status'], 'DEFERRED')
        self.assertEqual(git(self.wt, 'rev-parse', 'HEAD'), self.base)


class SharedHistory(Repo):
    def test_legacy_decisions_and_outcomes_survive_worktree_removal(self):
        docs = self.wt / '.atelier/implementations'
        docs.mkdir()
        (docs / '0001.md').write_text('# Idea\nLegacy experiment\n## Verdict\nVERDICT: DISCARD\n')
        self.init()
        first_id = self.run_id
        self.route()
        self.result(SUPERVISE)
        index = (docs / 'index.md').read_text()
        self.assertIn('Legacy experiment', index)
        self.assertIn('DISCARD', index)
        self.assertIn(first_id, index)
        git(self.main, 'worktree', 'remove', '--force', str(self.wt))
        shared = self.main / '.atelier/implementations'
        self.assertTrue((shared / 'runs' / first_id / 'state.json').exists())
        self.assertTrue((shared / '0001.md').exists())
        git(self.main, 'worktree', 'add', '-q', str(self.wt), '-b', 'fresh')
        shutil.copytree(CONDUITS, self.wt / '.atelier/conduits', ignore=shutil.ignore_patterns('__pycache__'))
        self.init()
        self.assertNotEqual(self.run_id, first_id)
        self.assertIn('Legacy experiment', (shared / 'index.md').read_text())


class NativeLoop(Repo):
    def setUp(self):
        super().setUp()
        if not shutil.which('atelier'):
            self.skipTest('atelier not installed')
        try:
            import acp  # noqa: F401
        except ImportError:
            self.skipTest('use Atelier Python to run ACP integration tests')
        self.agent_state = self.tmp / 'agents'
        self.agent_state.mkdir()

    # Fixture shortcut: expire the feature budget so the controller starts the final demonstration.
    EXPIRE = ("python3 -c \"import json; from pathlib import Path; p=Path('.atelier/goal/state.json'); "
              "s=json.loads(p.read_text()); s['finish_at']=0; p.write_text(json.dumps(s))\"")

    def script(self, role, turns):
        # Fixture agent writes result JSON from current request/Git state, with arbitrary chat output.
        scripted = []
        for command, result in turns:
            payload = json.dumps(result)
            code = "import json,subprocess; from pathlib import Path; p=Path('.atelier/goal'); r=json.loads(" + repr(payload) + "); r['request_id']=json.loads((p/'request.json').read_text())['id']; r.setdefault('head',subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()); (p/'result.json').write_text(json.dumps(r))"
            import shlex
            scripted.append({'run': command + '\n' + shlex.quote(sys.executable) + ' -c ' + shlex.quote(code),
                             'reply': 'Finished this stage. No magic completion marker.'})
        (self.agent_state / (role + '.json')).write_text(json.dumps({'turns': scripted}))

    def flow(self, resume=None):
        agent = [sys.executable, str(ROOT / 'tests/fake_agent.py'), str(self.agent_state)]
        command = ['atelier', 'run', '--resume', resume, '--hide-steps'] if resume else [
            'atelier', 'run', 'goal_loop', '--hide-steps', '-i', 'goal=First useful workflow', '-i', 'hours=1']
        return subprocess.run(command,
                              cwd=self.wt, env={**GIT_ENV, 'ATELIER_CLAUDE_LAUNCH_CMD': json.dumps(agent + ['claude']),
                                               'ATELIER_CODEX_LAUNCH_CMD': json.dumps(agent + ['codex'])},
                              capture_output=True, text=True, timeout=90)

    def test_complete_native_run_with_repair_multiple_commits_and_no_markers(self):
        self.script('supervisor', [('true', SUPERVISE), (self.EXPIRE, SUPERVISE)])
        self.script('claude', [
            ("printf b > b.txt\ngit add b.txt\ngit commit -qm first", {'status':'READY','summary':'First run','checks':[CHECK],'evidence':['b.txt']}),
            ("printf c > c.txt\ngit add c.txt\ngit commit -qm repair", {'status':'READY','summary':'Recovery','checks':[CHECK],'evidence':['c.txt']})])
        self.script('codex', [('test -f b.txt', {**REVIEW, 'verdict':'REVISE','findings':['Add c.txt'],'repair_estimate_seconds':10}), ('test -f c.txt', REVIEW)])
        self.script('finalizer', [('test -f b.txt && test -f c.txt', FINAL)])
        r = self.flow()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.state()['status'], 'ACHIEVED')
        self.assertEqual(git(self.wt, 'rev-list', '--count', self.base + '..HEAD'), '2')
        flows = list((self.wt / '.atelier/flows').glob('*_goal_loop'))
        self.assertEqual(len(flows), 1)
        self.assertEqual(json.loads((flows[0] / 'progress.json').read_text())['status'], 'completed')
        self.assertEqual(len(list((flows[0] / 'flows').glob('*_goal_iteration'))), 7)
        self.assertEqual((self.agent_state / 'claude.calls').read_text(), '2')
        self.assertIn('concrete findings', (self.agent_state / 'claude.prompt.2').read_text())

    def test_native_protocol_recovery_continues_same_stage(self):
        self.script('supervisor', [('true', {}), (self.EXPIRE, SUPERVISE)])
        self.script('finalizer', [('true', {**FINAL, 'status':'PARTIAL', 'remaining':['No implementation selected']})])
        r = self.flow()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.state()['status'], 'PARTIAL')
        self.assertEqual((self.agent_state / 'supervisor.calls').read_text(), '2')
        self.assertFalse((self.agent_state / 'claude.calls').exists())
        self.assertTrue(any(e['kind'] == 'PROTOCOL_ERROR' for e in self.state()['events']))

    def test_native_transport_failure_preserves_draft_and_resumes_same_run(self):
        self.script('supervisor', [('true', SUPERVISE), (self.EXPIRE, SUPERVISE)])
        self.script('claude', [("printf draft > b.txt\nexit 1", {})])
        self.script('codex', [('test -f b.txt', REVIEW)])
        self.script('finalizer', [('test -f b.txt', FINAL)])
        failed = self.flow()
        self.assertNotEqual(failed.returncode, 0)
        original = self.state()
        self.assertEqual(original['phase'], 'IMPLEMENT')
        self.assertEqual((self.wt / 'b.txt').read_text(), 'draft')
        self.assertEqual((self.agent_state / 'claude.calls').read_text(), '2')
        # Both automatic transport retries were used. Repair the fixture, then resume the parent.
        ready = {'status':'READY','summary':'Resumed draft','checks':[CHECK],'evidence':['b.txt']}
        self.script('claude', [('git add b.txt\ngit commit -qm resumed', ready)])
        flow = next((self.wt / '.atelier/flows').glob('*_goal_loop'))
        resumed = self.flow(resume=flow.name)
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertEqual(self.state()['id'], original['id'])
        self.assertEqual(self.state()['deadline'], original['deadline'])
        self.assertEqual(self.state()['status'], 'ACHIEVED')
        self.assertEqual(git(self.wt, 'rev-list', '--count', self.base + '..HEAD'), '1')


if __name__ == '__main__':
    unittest.main()
