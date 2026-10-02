import json
import unittest
from unittest.mock import patch
from datetime import timedelta
import test_tracker
import tracker as t
import supplemental as s

class SupplementalTests(unittest.TestCase):
    setUp=test_tracker.TrackerTests.setUp
    tearDown=test_tracker.TrackerTests.tearDown
    row=test_tracker.TrackerTests.row
    def raw(self,**kw):
        row={'OBJECT_NAME':'STARLINK-999999','OBJECT_ID':'','NORAD_CAT_ID':799999999,'EPOCH':t.stamp(t.now()+timedelta(days=3)),'MEAN_MOTION':15.5,'ECCENTRICITY':.001}
        return dict(row,**kw)
    def add(self,row=None,launch=True,url='https://celestrak.org/NORAD/elements/supplemental/sup-gp.php?FILE=starlink-g31-1&FORMAT=JSON'):
        return s.ingest(self.c,[row or self.raw()],t.stamp(),url,launch,t.orbital_values)
    def test_discovery_only_published_exact_mission(self):
        html='<a href="sup-gp.php?FILE=starlink-g31-1&amp;FORMAT=CSV">launch</a><a href="sup-gp.php?FILE=starlink-g31-10">other</a><a href="https://evil.test/sup-gp.php?FILE=starlink-g31-1">bad</a>'
        self.assertEqual(s.discover(html,self.cfg),[s.BASE+'?FILE=starlink-g31-1&FORMAT=JSON'])
        self.assertEqual(s.discover('<a href="sup-gp.php?FILE=starlink">all</a>',self.cfg),[])
    def test_future_predictions_not_gp_trend_or_activation(self):
        self.assertEqual(self.add(),[])
        obj=t.snapshot()['satellites'][0]
        self.assertEqual(obj['trend']['label'],'Prediction only');self.assertIsNone(obj['activation']);self.assertIsNone(obj['latest'])
        self.assertEqual(len(obj['supplemental_history']),1)
        self.assertEqual(self.c.execute('SELECT count(*) FROM observations').fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT count(*) FROM statuses').fetchone()[0],0)
    def test_general_feed_cannot_assign_unknown_satellite(self):
        self.add(launch=False);self.assertEqual(t.snapshot()['satellites'],[])
    def test_reconcile_preserves_history_and_ignores_temporary_id(self):
        self.add();before=t.snapshot()['satellites'][0]['id']
        t.ingest(self.c,[self.row(OBJECT_NAME='STARLINK-999999')],t.stamp());s.reconcile(self.c)
        result=t.snapshot()['satellites'];self.assertEqual(len(result),1);self.assertEqual(result[0]['id'],100001)
        self.assertEqual(len(result[0]['supplemental_history']),1);self.assertEqual(result[0]['trend']['label'],'Awaiting elements')
        self.assertEqual(self.c.execute('SELECT count(*) FROM sup_objects').fetchone()[0],1)
    def test_revisions_and_duplicate_source_epochs_preserved(self):
        raw=self.raw();self.add(raw);self.add(raw);self.add(dict(raw,MEAN_MOTION=15.4))
        self.assertEqual(self.c.execute('SELECT count(*) FROM sup_predictions').fetchone()[0],2)
    def test_ambiguous_catalog_names_not_reconciled(self):
        self.add();t.ingest(self.c,[self.row(OBJECT_NAME='STARLINK-999999'),self.row(NORAD_CAT_ID=100002,OBJECT_NAME='STARLINK-999999')],t.stamp());s.reconcile(self.c)
        self.assertIsNone(self.c.execute('SELECT catalog_id FROM sup_objects').fetchone()[0])
    def test_discovery_runs_without_catalog_members(self):
        def fetch(c,url,text=False):
            return ('<a href="sup-gp.php?FILE=starlink-g31-1">data</a>' if text else [self.raw()]),t.stamp()
        result=s.collect(self.c,self.cfg,fetch,t.orbital_values)
        self.assertEqual(result['errors'],[]);self.assertEqual(len(t.snapshot()['satellites']),1)
    def test_general_feed_bootstraps_verified_launch_without_satcat(self):
        self.cfg['international_designator']='2026-225'
        calls=[]
        def fetch(c,url,text=False):
            calls.append(url)
            return ('<html>No launch file</html>' if text else [self.raw(OBJECT_ID='2026-225A'),self.raw(OBJECT_NAME='STARLINK-1',OBJECT_ID='2026-2250A'),self.raw(OBJECT_NAME='STARLINK-2',OBJECT_ID='2026-224A')]),t.stamp()
        result=s.collect(self.c,self.cfg,fetch,t.orbital_values)
        self.assertEqual(result['errors'],[])
        self.assertIn(s.BASE+'?FILE=starlink&FORMAT=JSON',calls)
        self.assertEqual(len(t.snapshot()['satellites']),1)
        self.assertEqual(self.c.execute('SELECT count(*) FROM satellites').fetchone()[0],0)
    def test_index_failure_does_not_block_general_feed(self):
        self.cfg['international_designator']='2026-225'
        def fetch(c,url,text=False):
            if text:raise RuntimeError('index unavailable')
            return [self.raw(OBJECT_ID='2026-225A')],t.stamp()
        result=s.collect(self.c,self.cfg,fetch,t.orbital_values)
        self.assertEqual(len(result['errors']),1)
        self.assertEqual(len(t.snapshot()['satellites']),1)
