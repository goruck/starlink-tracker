import json
import tempfile
import unittest
from unittest.mock import patch, Mock
from datetime import timedelta
import tracker as t

class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=t.DATA;t.DATA=t.Path(self.tmp.name)
        self.c=t.connect();self.cfg={'launch_date':'2026-09-28','launch_sites':[],'international_designator':None}
    def tearDown(self):
        self.c.close();t.DATA=self.old;self.tmp.cleanup()
    def row(self,**kw):
        r={'OBJECT_NAME':'STARLINK-TEST','NORAD_CAT_ID':100001,'OBJECT_ID':'2026-999A','LAUNCH_DATE':'2026-09-28','LAUNCH_SITE':'UNK','OBJECT_TYPE':'PAY','OPS_STATUS_CODE':'P'}
        return dict(r,**kw)
    def test_membership_excludes_wrong_date_and_debris(self):
        self.assertEqual(len(t.select_members([self.row(),self.row(LAUNCH_DATE='2026-09-27'),self.row(OBJECT_TYPE='DEB')],self.cfg)),1)
    def test_ambiguous_launch_fails_closed(self):
        with self.assertRaises(ValueError):t.select_members([self.row(),self.row(OBJECT_ID='2026-998A')],self.cfg)
    def test_exact_designator(self):
        self.cfg['international_designator']='2026-999'
        self.assertEqual(len(t.select_members([self.row(),self.row(OBJECT_ID='2026-9999A')],self.cfg)),1)
    def test_status_dedup_and_activation_interval(self):
        start=t.stamp(t.now()-timedelta(hours=2));end=t.stamp()
        t.ingest(self.c,[self.row()],start);t.ingest(self.c,[self.row()],start);t.ingest(self.c,[self.row(OPS_STATUS_CODE='+')],end)
        rows=[dict(r) for r in self.c.execute('SELECT * FROM statuses ORDER BY observed')]
        self.assertEqual(len(rows),2);self.assertEqual(t.activation(rows)['after'],start)
    def test_already_operational_not_exact_activation(self):
        self.assertTrue(t.activation([{'code':'+','observed':t.stamp()}])['already_operational'])
        self.assertIsNone(t.activation([{'code':'P','observed':t.stamp()}]))
    def test_trends_and_staleness(self):
        current=t.now()
        h=[{'epoch':t.stamp(current-timedelta(hours=24)),'altitude':300},{'epoch':t.stamp(current),'altitude':310}]
        self.assertEqual(t.trend(h,current)['label'],'Raising (estimated)')
        self.assertEqual(t.trend(h,current)['rate'],10)
        self.assertEqual(t.trend(h,current+timedelta(days=4))['label'],'Stale / future elements')
        h[-1]['altitude']=290;self.assertEqual(t.trend(h,current)['label'],'Lowering (estimated)')
        h[-1]['altitude']=301;self.assertEqual(t.trend(h,current)['label'],'Stable (estimated)')
        self.assertEqual(t.trend(h[:1],current)['label'],'Building history')
    def test_cache_prevents_duplicate_fetch(self):
        r=Mock(status_code=200);r.json.return_value=[self.row()]
        with patch.object(t.requests,'get',return_value=r) as get:
            first=t.fetch(self.c,t.CAT_URL);second=t.fetch(self.c,t.CAT_URL)
            self.assertEqual(first,second);self.assertEqual(get.call_count,1)
    def test_http_error_pauses_source(self):
        with patch.object(t.requests,'get',return_value=Mock(status_code=403)) as get:
            for _ in range(2):
                with self.assertRaises(RuntimeError):t.fetch(self.c,t.CAT_URL)
            self.assertEqual(get.call_count,1)
    def test_server_error_retries_only_after_cache_interval(self):
        good=Mock(status_code=200);good.json.return_value=[self.row()]
        with patch.object(t.requests,'get',side_effect=[Mock(status_code=503),good]) as get:
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError,'HTTP 503'):t.fetch(self.c,t.CAT_URL)
            self.assertEqual(get.call_count,1)
            self.assertEqual(self.c.execute('SELECT blocked FROM sources').fetchone()[0],0)
            self.c.execute('UPDATE sources SET attempted=?',(t.stamp(t.now()-timedelta(hours=3)),));self.c.commit()
            self.assertEqual(t.fetch(self.c,t.CAT_URL)[0],[self.row()])
            self.assertEqual(get.call_count,2)
            self.assertIsNone(self.c.execute('SELECT error FROM sources').fetchone()[0])

    def test_malformed_response_and_interrupted_fetch(self):
        r=Mock(status_code=200);r.json.return_value={'error':'bad'}
        with patch.object(t.requests,'get',return_value=r) as get:
            for _ in range(2):
                with self.assertRaises(RuntimeError):t.fetch(self.c,t.CAT_URL)
            self.assertEqual(get.call_count,1)
    def test_omm_six_digit_id_and_position(self):
        raw={'OBJECT_NAME':'STARLINK-TEST','OBJECT_ID':'2026-999A','NORAD_CAT_ID':100001,'EPOCH':t.now().strftime('%Y-%m-%dT%H:%M:%S.%f'),'MEAN_MOTION':15.5,'ECCENTRICITY':0.0001,'INCLINATION':53,'RA_OF_ASC_NODE':20,'ARG_OF_PERICENTER':30,'MEAN_ANOMALY':40,'EPHEMERIS_TYPE':0,'CLASSIFICATION_TYPE':'U','ELEMENT_SET_NO':1,'REV_AT_EPOCH':10,'BSTAR':0.00001,'MEAN_MOTION_DOT':0,'MEAN_MOTION_DDOT':0}
        epoch,alt,peri,apo=t.orbital_values(raw)
        self.assertTrue(350<alt<450);self.assertLess(peri,apo)
        t.ingest(self.c,[self.row()],t.stamp())
        values=(100001,epoch,t.stamp(),alt,peri,apo,json.dumps(raw))
        for _ in range(2):self.c.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?)',values)
        self.c.commit()
        self.assertEqual(self.c.execute('SELECT count(*) FROM observations').fetchone()[0],1)
        result=t.snapshot(100001)['satellites'][0]
        self.assertIsNotNone(result['position']);self.assertEqual(result['service_activation'],'Unconfirmed')
    def test_invalid_orbit_rejected(self):
        for n in [0,float('nan'),float('inf'),-5]:
            with self.assertRaises(ValueError):t.orbital_values({'MEAN_MOTION':n,'ECCENTRICITY':0,'EPOCH':t.stamp()})

    def test_http_dashboard_and_api_without_binding(self):
        import io
        class Socket:
            def __init__(self,path):self.request=io.BytesIO(('GET '+path+' HTTP/1.1\r\nHost: localhost\r\n\r\n').encode());self.output=b''
            def makefile(self,*a,**kw):return self.request
            def sendall(self,data):self.output+=data
        for path in ['/', '/style.css', '/app.js', '/favicon.svg', '/api/status', '/api/satellites/100001']:
            sock=Socket(path);t.Handler(sock,('127.0.0.1',1234),None)
            self.assertIn(b'200 OK',sock.output)
            self.assertIn(b'Content-Security-Policy:',sock.output)
            if path.startswith('/api/'):
                payload=json.loads(sock.output.split(b'\r\n\r\n',1)[1]);self.assertEqual(payload['satellites'],[])
        sock=Socket('/../tracker.py');t.Handler(sock,('127.0.0.1',1234),None);self.assertIn(b'404',sock.output)

if __name__=='__main__':unittest.main()

