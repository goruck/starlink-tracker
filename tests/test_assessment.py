import copy
import unittest
from datetime import datetime,timezone,timedelta
import assessment as a

class AssessmentTests(unittest.TestCase):
 def setUp(self):self.end=datetime(2026,9,29,12,tzinfo=timezone.utc)
 def rows(self,heights=(270,272,274),spacing=12,stream='GP'):
  rows=[]
  for i,h in enumerate(heights):
   t=self.end-timedelta(hours=spacing*(len(heights)-1-i))
   rows.append({'epoch':t.isoformat(),'observed':(t+timedelta(minutes=1)).isoformat(),'altitude':h,'perigee':h-5,'apogee':h+5,'inclination':30.5,'eccentricity':.001,'rms':.4,'source':'test','data_source':'SpaceX-E'})
  return rows
 def sat(self,rows=None,sid=1,sup=False):
  return {'id':sid,'history':[] if sup else (rows or []),'supplemental_history':(rows or []) if sup else [],'statuses':[]}
 def run_data(self,sats):
  return a.assess({'mission':{},'generated':(self.end+timedelta(minutes=2)).isoformat(),'satellites':sats})
 def test_plain_summary_excludes_forecasts_and_explains_circularization(self):
  rows=self.rows()
  for i,r in enumerate(rows):r['perigee']+=i
  future=self.rows()
  for r in future:r['observed']=(a.date(r['epoch'])-timedelta(hours=1)).isoformat()
  x=self.run_data([self.sat(rows),self.sat(future,sid=2,sup=True)])
  text=' '.join(x['assessment_summary']['plain_language'])
  self.assertIn('1 of 2 satellites have recent',text)
  self.assertIn('1 of 1 satellites with usable trends are climbing',text)
  self.assertIn('For 1 of 1 satellites, perigee',text)
  self.assertIn('lack a fresh, nonforecast solution',text)
 def test_plain_summary_does_not_claim_circularization_from_altitude_alone(self):
  x=self.run_data([self.sat(self.rows())])
  self.assertIn('For 0 of 1 satellites, perigee',' '.join(x['assessment_summary']['plain_language']))

 def test_single_sup_solution_visible_but_insufficient(self):
  s=self.run_data([self.sat(self.rows((270,)),sup=True)])['satellites'][0]
  self.assertEqual(s['display_orbit']['altitude'],270);self.assertEqual(s['assessment']['level'],'insufficient')
 def test_three_updates_24_hours_raising(self):
  s=self.run_data([self.sat(self.rows())])['satellites'][0]
  self.assertEqual(s['assessment']['phase'],'Raising indicated');self.assertAlmostEqual(s['assessment']['metrics']['rate'],4)
 def test_future_forecast_not_reclassified_as_observation_later(self):
  rows=self.rows()
  for r in rows:r['observed']=(a.date(r['epoch'])-timedelta(hours=1)).isoformat()
  s=self.run_data([self.sat(rows,sup=True)])['satellites'][0]
  self.assertIsNone(s['assessment']['metrics']);self.assertTrue(s['display_orbit']['forecast'])
 def test_same_receipt_multiple_epochs_not_history(self):
  rows=self.rows()
  for r in rows:r['observed']=(self.end+timedelta(minutes=1)).isoformat()
  s=self.run_data([self.sat(rows,sup=True)])['satellites'][0]
  self.assertIsNone(s['assessment']['metrics'])
 def test_same_epoch_revisions_not_independent(self):
  rows=self.rows()
  for r in rows:r['epoch']=rows[0]['epoch']
  s=self.run_data([self.sat(rows,sup=True)])['satellites'][0]
  self.assertIsNone(s['assessment']['metrics'])
 def test_source_switch_cannot_generate_trend(self):
  rows=self.rows();s=self.sat(rows[:1]);s['supplemental_history']=rows[1:]
  self.assertIsNone(self.run_data([s])['satellites'][0]['assessment']['metrics'])
 def test_stable_is_not_failure_or_activation(self):
  s=self.run_data([self.sat(self.rows((270,270,270)))])['satellites'][0]
  self.assertEqual(s['assessment']['label'],'No divergence detected');self.assertEqual(s['assessment']['phase'],'Possible waypoint or hold')
 def test_descent_watch_then_concern(self):
  short=self.run_data([self.sat(self.rows((280,277,274)))])['satellites'][0]
  self.assertEqual(short['assessment']['level'],'watch')
  long=self.run_data([self.sat(self.rows((280,278,276,274,272)))])['satellites'][0]
  self.assertEqual(long['assessment']['level'],'concern')
 def test_jump_flag_suppresses_spacecraft_warning(self):
  s=self.run_data([self.sat(self.rows((280,260,240,220,200),6))])['satellites'][0]
  self.assertEqual(s['assessment']['level'],'data');self.assertTrue(any(f['code']=='discontinuity' for f in s['assessment']['findings']))
 def test_peer_divergence_same_stage_only(self):
  sats=[self.sat(self.rows((270,270,270)),1)]+[self.sat(self.rows(),i) for i in range(2,5)]
  s=self.run_data(sats)['satellites'][0]
  self.assertEqual(s['assessment']['level'],'watch');self.assertEqual(s['assessment']['peers']['count'],3)
  sats=[self.sat(self.rows((320,320,320)),1)]+[self.sat(self.rows(),i) for i in range(2,5)]
  self.assertEqual(self.run_data(sats)['satellites'][0]['assessment']['level'],'nominal')
 def test_stale_fleet_data_not_failure(self):
  sats=[self.sat(self.rows((270,)),i) for i in range(4)]
  self.end+=timedelta(days=2)
  data=self.run_data(sats);self.assertTrue(data['assessment_summary']['fleet_data_issue'])
  self.assertEqual(data['assessment_summary']['counts']['data'],4)
 def test_catalog_withdrawal_and_shell_do_not_mean_activation(self):
  s=self.sat(self.rows((340,340,340)));s['statuses']=[{'code':'+','observed':(self.end-timedelta(hours=2)).isoformat()},{'code':'P','observed':self.end.isoformat()}]
  result=self.run_data([s])['satellites'][0]
  self.assertEqual(result['assessment']['level'],'watch');self.assertEqual(result['assessment']['phase'],'Near candidate shell')
 def test_custom_rules_enforce_minimum_evidence(self):
  with self.assertRaises(ValueError):a.settings({'assessment_rules':{'minimum_updates':1}})
