"""Local observation-based Starlink tracker. No inferred service activation."""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import requests
import supplemental
import assessment
from skyfield.api import EarthSatellite, load, wgs84

ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('STARLINK_DATA_DIR',str(Path.home()/'.local/share/starlink-tracker')))
CAT_URL='https://celestrak.org/satcat/records.php?NAME=STARLINK&FORMAT=JSON'
STATUSES={'+':'Operational','-':'Nonoperational','P':'Partially operational','B':'Backup','S':'Standby','X':'Extended mission','D':'Decayed','?':'Unknown'}
TS=load.timescale(builtin=True)
def now():return datetime.now(timezone.utc)
def stamp(t=None):return (t or now()).isoformat()
def dt(s):
    t=datetime.fromisoformat(s.replace('Z','+00:00'))
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
def config():return json.loads((ROOT/'mission.json').read_text())
def connect():
    DATA.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DATA/'tracker.sqlite3',timeout=30);c.row_factory=sqlite3.Row
    c.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS sources(url TEXT PRIMARY KEY, attempted TEXT, success TEXT, error TEXT, blocked INTEGER DEFAULT 0, body TEXT);
    CREATE TABLE IF NOT EXISTS satellites(id INTEGER PRIMARY KEY, name TEXT, object_id TEXT, first_seen TEXT, last_seen TEXT, status TEXT, catalog TEXT);
    CREATE TABLE IF NOT EXISTS observations(id INTEGER, epoch TEXT, observed TEXT, altitude REAL, perigee REAL, apogee REAL, raw TEXT, PRIMARY KEY(id,epoch));
    CREATE TABLE IF NOT EXISTS statuses(id INTEGER, observed TEXT, code TEXT, PRIMARY KEY(id,observed));
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
    ''');supplemental.schema(c);return c

def fetch(c,url,text=False):
    prev=c.execute('SELECT * FROM sources WHERE url=?',(url,)).fetchone();when=stamp()
    if prev and prev['blocked']:raise RuntimeError('Source paused: '+str(prev['error'])+'; see README for recovery.')
    if prev and prev['attempted'] and (now()-dt(prev['attempted'])).total_seconds()<7200:
        if prev['error']:raise RuntimeError(prev['error'])
        if prev['body']:return (prev['body'] if text else json.loads(prev['body'])),prev['success']
        raise RuntimeError('Previous request interrupted; waiting for cache interval.')
    c.execute('INSERT INTO sources(url,attempted) VALUES(?,?) ON CONFLICT(url) DO UPDATE SET attempted=excluded.attempted',(url,when));c.commit()
    r=None
    try:
        r=requests.get(url,timeout=(10,45),headers={'User-Agent':'Local-Starlink-Tracker/1.0'},allow_redirects=False)
        if r.status_code!=200:
            recovery='retry after two-hour cache interval' if 500<=r.status_code<600 else 'automatic requests paused'
            raise ValueError(f'HTTP {r.status_code}; {recovery}')
        rows=r.text if text else r.json()
        if not text and (not isinstance(rows,list) or any(not isinstance(x,dict) or 'NORAD_CAT_ID' not in x for x in rows)):raise ValueError('Unexpected data schema')
        c.execute('UPDATE sources SET success=?,error=NULL,body=? WHERE url=?',(when,rows if text else json.dumps(rows),url));c.commit();return rows,when
    except Exception as e:
        msg=f'{type(e).__name__}: {e}'
        c.execute('UPDATE sources SET error=?,blocked=? WHERE url=?',(msg,int(r is not None and r.status_code!=200 and not 500<=r.status_code<600),url));c.commit();raise RuntimeError(msg) from e

def select_members(rows,cfg):
    selected=[]
    for r in rows:
        oid=str(r.get('OBJECT_ID') or '')
        if not str(r.get('OBJECT_NAME','')).upper().startswith('STARLINK') or r.get('OBJECT_TYPE')!='PAY':continue
        if cfg.get('international_designator'):
            match=re.fullmatch(re.escape(cfg['international_designator'])+r'[A-Z]+',oid)
        else:match=str(r.get('LAUNCH_DATE',''))[:10]==cfg['launch_date'] and (not cfg['launch_sites'] or r.get('LAUNCH_SITE') in cfg['launch_sites'])
        if match:selected.append(r)
    launches={re.sub(r'[A-Z]+$','',str(r['OBJECT_ID'])) for r in selected}
    if len(launches)>1:raise ValueError('Multiple matching launches; set international_designator in mission.json.')
    return selected

def orbital_values(raw,allow_prediction=False):
    n=float(raw['MEAN_MOTION']);e=float(raw['ECCENTRICITY']);epoch=dt(raw['EPOCH'])
    if not math.isfinite(n) or not 0<n<20 or not math.isfinite(e) or not 0<=e<1:raise ValueError('Invalid orbital elements')
    a=(398600.4418/(n*2*math.pi/86400)**2)**(1/3)
    if not allow_prediction and epoch>now()+timedelta(hours=24):raise ValueError('Element epoch is too far in the future')
    return stamp(epoch),a-6378.137,a*(1-e)-6378.137,a*(1+e)-6378.137

def ingest(c,members,observed):
    for r in members:
        sid=int(r['NORAD_CAT_ID']);code=r.get('OPS_STATUS_CODE') or '?'
        c.execute('INSERT INTO satellites VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,object_id=excluded.object_id,last_seen=excluded.last_seen,status=excluded.status,catalog=excluded.catalog',(sid,r['OBJECT_NAME'],r['OBJECT_ID'],observed,observed,code,json.dumps(r)))
        c.execute('INSERT OR IGNORE INTO statuses VALUES(?,?,?)',(sid,observed,code))
    c.commit()

def collect():
    DATA.mkdir(parents=True,exist_ok=True)
    with (DATA/'collector.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return True
        with connect() as c:
            errors=[]
            try:
                rows,seen=fetch(c,CAT_URL);ingest(c,select_members(rows,config()),seen)
            except Exception as e:errors.append(str(e))
            # Continue collecting known objects even if catalog retrieval fails.
            launches={re.sub(r'[A-Z]+$','',r['object_id']) for r in c.execute('SELECT object_id FROM satellites')}
            for launch in launches:
                if not re.fullmatch(r'\d{4}-\d{3,4}',launch):continue
                url=f'https://celestrak.org/NORAD/elements/gp.php?INTDES={launch}&FORMAT=JSON'
                try:
                    elements,seen=fetch(c,url)
                    ids={r['id']:r['object_id'] for r in c.execute('SELECT id,object_id FROM satellites')}
                    for raw in elements:
                        sid=int(raw['NORAD_CAT_ID'])
                        if sid not in ids or raw.get('OBJECT_ID')!=ids[sid]:continue
                        try:
                            epoch,alt,peri,apo=orbital_values(raw)
                            c.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?)',(sid,epoch,seen,alt,peri,apo,json.dumps(raw)))
                        except (ValueError,KeyError,TypeError) as e:errors.append(f'{sid}: {e}')
                except Exception as e:errors.append(str(e))
            try:
                sup=supplemental.collect(c,config(),fetch,orbital_values)
                errors.extend(sup['errors'])
                c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('supplemental',json.dumps(sup)))
            except Exception as e:errors.append('Supplemental: '+str(e))
            c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('collection',json.dumps({'at':stamp(),'errors':errors})));c.commit()
            print(json.dumps({'at':stamp(),'satellites':c.execute('SELECT count(*) FROM satellites').fetchone()[0],'errors':errors}));return not errors

def trend(history,current=None):
    current=current or now()
    if not history:return {'label':'Awaiting elements','delta':None,'rate':None}
    last=history[-1];age=(current-dt(last['epoch'])).total_seconds()/3600
    if age>72 or age < -1:return {'label':'Stale / future elements','delta':None,'rate':None}
    candidates=[x for x in history[:-1] if 12<=(dt(last['epoch'])-dt(x['epoch'])).total_seconds()/3600<=72]
    if not candidates:return {'label':'Building history','delta':None,'rate':None}
    first=min(candidates,key=lambda x:abs((dt(last['epoch'])-dt(x['epoch'])).total_seconds()/3600-24))
    hours=(dt(last['epoch'])-dt(first['epoch'])).total_seconds()/3600
    delta=last['altitude']-first['altitude']
    return {'label':'Raising (estimated)' if delta>=2 else 'Lowering (estimated)' if delta<=-2 else 'Stable (estimated)','delta':delta,'rate':delta*24/hours,'hours':hours}

def activation(history):
    for i,r in enumerate(history):
        if r['code']=='+':return {'first_observed':r['observed'],'after':history[i-1]['observed'] if i else None,'already_operational':i==0}
    return None

def snapshot(sid=None):
    with connect() as c:
        if isinstance(sid,str) and sid.startswith('sup-'):
            linked=c.execute('SELECT catalog_id FROM sup_objects WHERE id=?',(int(sid[4:]),)).fetchone()
            if linked and linked[0] is not None:sid=linked[0]
        c.execute('BEGIN')
        result={'mission':config(),'generated':stamp(),'satellites':[],'sources':[dict(r) for r in c.execute('SELECT url,attempted,success,error,blocked FROM sources')],'collection':None}
        m=c.execute("SELECT value FROM meta WHERE key='collection'").fetchone()
        if m:result['collection']=json.loads(m[0])
        for r in c.execute('SELECT * FROM satellites ORDER BY id'):
            s=dict(r);s.pop('catalog')
            history=[dict(x) for x in c.execute('SELECT epoch,observed,altitude,perigee,apogee,raw FROM observations WHERE id=? ORDER BY epoch',(r['id'],))]
            for point in history:
                raw=json.loads(point.pop('raw'));point.update({'inclination':raw.get('INCLINATION'),'eccentricity':raw.get('ECCENTRICITY'),'rms':None})
            statuses=[dict(x) for x in c.execute('SELECT observed,code FROM statuses WHERE id=? ORDER BY observed',(r['id'],))]
            s.update({'trend':trend(history),'latest':history[-1] if history else None,'activation':activation(statuses),'status_label':STATUSES.get(s['status'],'Unknown'),'catalog_stale':(now()-dt(s['last_seen'])).total_seconds()>86400,'service_activation':'Unconfirmed'})
            s['history']=history;s['statuses']=statuses;s['position']=None
            if sid is not None and str(r['id'])==str(sid):
                raw=c.execute('SELECT raw FROM observations WHERE id=? ORDER BY epoch DESC LIMIT 1',(sid,)).fetchone()
                if raw and s['latest'] and abs((now()-dt(s['latest']['epoch'])).total_seconds())<=72*3600:
                    try:
                        sat=EarthSatellite.from_omm(TS,json.loads(raw[0]));p=sat.at(TS.from_datetime(now()))
                        if p.message:raise ValueError(p.message)
                        sub=wgs84.subpoint(p);vals=[float(sub.latitude.degrees),float(sub.longitude.degrees),float(sub.elevation.km)]
                        if all(math.isfinite(v) for v in vals):s['position']={'latitude':vals[0],'longitude':vals[1],'altitude':vals[2],'at':stamp()}
                    except Exception as e:s['position_error']=str(e)
            keys=[x[0] for x in c.execute('SELECT id FROM sup_objects WHERE catalog_id=?',(r['id'],))]
            s['supplemental_history']=supplemental.history(c,keys)
            s['identity_label']='Catalog ID'
            result['satellites'].append(s)
        supmeta=c.execute("SELECT value FROM meta WHERE key='supplemental'").fetchone()
        result['supplemental']=json.loads(supmeta[0]) if supmeta else None
        for obj in c.execute('SELECT * FROM sup_objects WHERE catalog_id IS NULL ORDER BY id'):
            key='sup-'+str(obj['id'])
            predictions=supplemental.history(c,[obj['id']])
            result['satellites'].append({'id':key,'name':obj['name'],'object_id':obj['object_id'] or 'Unassigned','identity_label':'Provisional source ID '+str(obj['source_id']),
                'trend':{'label':'Prediction only','delta':None,'rate':None},'latest':None,'activation':None,'status':'?','status_label':'Unknown','catalog_stale':False,
                'service_activation':'Unconfirmed','history':[],'statuses':[],'position':None,'supplemental_history':predictions})
        assessment.assess(result)
        if sid is not None:result['satellites']=[x for x in result['satellites'] if str(x['id'])==str(sid)]
        return result

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path=urlparse(self.path).path
        try:
            attachment=None
            if path=='/api/report.docx':
                import reporting
                data=snapshot();body=reporting.build_report(data);typ='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                attachment='starlink-deployment-'+data['generated'][:10]+'.docx'
            elif path=='/api/status' or re.fullmatch(r'/api/satellites/(?:\d+|sup-\d+)',path):
                sid=path.rsplit('/',1)[1] if path.startswith('/api/satellites/') else None
                body=json.dumps(snapshot(sid),allow_nan=False).encode();typ='application/json'
            elif path in ('/','/app.js','/style.css','/favicon.svg'):
                p=ROOT/'static'/('index.html' if path=='/' else path[1:]);body=p.read_bytes();typ={'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'}[p.suffix]
            else:self.send_error(404);return
            self.send_response(200)
            if attachment:self.send_header('Content-Disposition','attachment; filename="'+attachment+'"')
            self.send_header('Content-Type',typ if attachment else typ+'; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'");self.end_headers();self.wfile.write(body)
        except Exception as e:self.send_error(500,'Unable to read tracker data');print(type(e).__name__,e)
def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['serve','collect','resume-sources']);p.add_argument('--port',type=int,default=8765);args=p.parse_args()
    if args.command=='collect':raise SystemExit(0 if collect() else 1)
    if args.command=='resume-sources':
        with connect() as c:c.execute('UPDATE sources SET blocked=0,error=NULL,attempted=NULL WHERE blocked=1')
        return
    connect().close();server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);print(f'Dashboard: http://localhost:{args.port}',flush=True);server.serve_forever()
if __name__=='__main__':main()
