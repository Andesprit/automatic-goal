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
        self.assertEqual(summary['abandoned'][0]['detail'], 'Duplicated an existing capability')
        text = report.markdown(summary)
        done = text.split('### Done')[1].split('### Still open')[0]
        self.assertNotIn('Failure recovery', done)
        self.assertIn('Windows not checked', text)
        self.assertIn('The overall goal has not been demonstrated', text)

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
