import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / '.atelier/conduits/goal_iteration/scripts/session_report.py'
spec = importlib.util.spec_from_file_location('session_report', SCRIPT)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class SessionReportTests(unittest.TestCase):
    def state(self):
        return {'status':'PARTIAL','started':100,'finished':7300,'branch':'goal/onboarding',
                'reason':'Time reserved for the final review.', 'priority':'Stale priority',
                'brief':{'outcome':'First useful workflow', 'baseline':['Several manual steps']},
                'final':{'after':'Setup is simpler; recovery remains incomplete.',
                         'evidence':['Reproduced first run'], 'remaining':['Recovery needs work'],
                         'limitations':['Windows not checked']},
                'milestones':[
                    {'id':'1','title':'Starter picker','status':'ACCEPT','revisions':2,
                     'implementation':{'summary':'Choose a useful starter in one step'},
                     'reviews':[{'evidence':['Reproduced first run']}]},
                    {'id':'2','title':'Setup diagnostics','status':'ACCEPT','benefit':'Explains missing setup'},
                    {'id':'3','title':'Failure recovery','status':'DEFERRED','benefit':'Retry failed runs'},
                    {'id':'4','title':'Extra command','status':'ABANDON',
                     'reviews':[{'reason':'Duplicated an existing capability'}]}]}

    def test_whole_session_lists_all_delivered_work_once(self):
        summary = report.summarize(self.state())
        self.assertEqual([item['title'] for item in summary['done']], ['Starter picker','Setup diagnostics'])
        self.assertEqual(summary['headline'], '2 improvements completed and reviewed.')
        self.assertEqual(summary['elapsed'], '2h 00m')
        self.assertEqual(summary['evidence'], ['Reproduced first run'])
        self.assertEqual(summary['next'], '')

    def test_deferred_and_abandoned_work_is_not_presented_as_done(self):
        summary = report.summarize(self.state())
        self.assertEqual(summary['status'], 'Partly complete')
        self.assertFalse(summary['demonstrated'])
        self.assertEqual(summary['pending'][0]['title'], 'Failure recovery')
        self.assertEqual(summary['abandoned'][0]['detail'], 'Not kept: Duplicated an existing capability')
        text = report.markdown(summary)
        done = text.split('### Done')[1].split('### Still open')[0]
        self.assertNotIn('Failure recovery', done)
        self.assertIn('Windows not checked', text)
        self.assertIn('The overall goal has not been demonstrated', text)

    def test_paragraphs_preserve_what_why_how_and_abandonment_evidence(self):
        state = self.state()
        completed = ('The starter picker adds a guided first run. '
                     'New developers previously had to choose a template without context. '
                     'It previews each workflow and reuses the existing login to run the selection.')
        attempted = ('We added a shortcut for choosing the default workflow. '
                     'The aim was to reduce setup decisions. '
                     'It wrapped the existing starter command with a fixed template.')
        reason = ('The walkthrough showed the starter picker already covered this path. '
                  'Another command added a choice without removing any setup steps.')
        state['milestones'][0]['implementation']['summary'] = completed
        state['milestones'][3]['implementation'] = {'summary': attempted}
        state['milestones'][3]['reviews'] = [{'reason': reason}]
        summary = report.summarize(state)
        self.assertEqual(summary['done'][0]['detail'], completed)
        self.assertEqual(summary['abandoned'][0]['detail'], attempted + ' Not kept: ' + reason)
        text = report.markdown(summary)
        self.assertIn('**Starter picker**\n\n' + completed, text)
        self.assertIn('**Extra command**\n\n' + attempted + ' Not kept: ' + reason, text)
        self.assertNotIn(attempted, text.split('### Done')[1].split('### Still open')[0])

    def test_abandoned_older_record_retains_available_context(self):
        state = self.state()
        state['milestones'][3]['benefit'] = 'Reduce setup decisions'
        summary = report.summarize(state)
        self.assertEqual(summary['abandoned'][0]['detail'],
                         'Reduce setup decisions. Not kept: Duplicated an existing capability')

    def test_report_leads_with_the_scorecard_and_accounts_for_the_time(self):
        state = self.state()
        state.update(deadline=11000, events=[
            {'stage':'SUPERVISE','duration_seconds':600}, {'stage':'IMPLEMENT','duration_seconds':5400},
            {'stage':'REVIEW','duration_seconds':300}, {'kind':'USAGE_PAUSE'}],
            scorecard={'baseline':{'values':{'scripts':12}}, 'latest':{'values':{'scripts':85}}},
            judged={'usable':{'state':'partial','evidence':'Two of five papers build'}})
        state['brief']['scorecard'] = [
            {'id':'scripts','outcome':'Scripts that run','kind':'measured','command':'count','unit':'%',
             'direction':'up','target':95},
            {'id':'usable','outcome':'A reader finds any file','kind':'judged','how':'Look for three files',
             'baseline':'One of three found'}]
        state['final'].update(next_run=['Repair the remaining scripts'], questions=['Keep paper 1 whole?'])
        state['milestones'][0].update(
            spent_seconds=3600, turns=3, before={'scripts':12}, readings=[{'values':{'scripts':85}}],
            hypothesis={'action':'Repairing paths','outcome':'scripts','expected':'from 12% to 90%',
                        'because':'most fail on paths','failure_mode':'Paths built from data'})
        state['milestones'][3]['cause'] = 'idea'
        text = report.markdown(report.summarize(state))
        self.assertLess(text.index('### Scorecard'), text.index('### Done'))
        self.assertIn('| Scripts that run | 12% | 85% | 95% |', text)
        self.assertIn('| A reader finds any file | One of three found | Partly met: Two of five papers build |  |', text)
        self.assertIn('- Bet: Repairing paths should move "Scripts that run" (from 12% to 90%), because most fail on paths', text)
        self.assertIn('- Measured: Scripts that run: 12% → 85%', text)
        self.assertIn('**Extra command** (Wrong idea)', text)
        self.assertIn('- Planning 10 min · Building 1h 30m · Check-ins and reviews 5 min', text)
        self.assertIn('- Starter picker: 1h 00m over 3 worker turns', text)
        self.assertIn('- Unused before the deadline: 1h 01m', text)
        self.assertIn('### Next run\n\n- Repair the remaining scripts\n\nQuestions for you:\n\n- Keep paper 1 whole?', text)
        self.assertIn('memory.py feedback <id> keep|discard', text)

    def test_live_and_interrupted_sessions_remain_honest(self):
        state = self.state()
        state.update(status='ACTIVE', finished=None, priority='Repair recovery')
        self.assertEqual(report.summarize(state, now=160)['next'], 'Repair recovery')
        state.update(status='OPERATIONAL_FAILURE', reason='Agent connection failed')
        summary = report.summarize(state, now=160)
        self.assertEqual(summary['status'], 'Run interrupted')
        self.assertIn('Agent connection failed', summary['remaining'])

    def test_empty_session_does_not_invent_work_or_evidence(self):
        summary = report.summarize({'status':'ACTIVE'})
        self.assertEqual(summary['done'], [])
        self.assertEqual(summary['evidence'], [])
        self.assertEqual(summary['elapsed'], 'Time unavailable')
        self.assertIn('No completed improvements', summary['headline'])


if __name__ == '__main__':
    unittest.main()
