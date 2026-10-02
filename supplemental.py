"""SupGP discovery and prediction storage, isolated from GP trend evidence."""
import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, parse_qs

INDEX='https://celestrak.org/NORAD/elements/supplemental/'
BASE=INDEX+'sup-gp.php'

class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            href=dict(attrs).get('href')
            if href:self.links.append(href)

def discover(html,cfg):
    """Only request launch files actually advertised by the publisher."""
    p=Links();p.feed(html);found=set()
    allowed=set(cfg.get('supplemental_files',['starlink-g31-1']))
    for href in p.links:
        u=urlparse(urljoin(INDEX,href));q=parse_qs(u.query)
        if u.hostname!='celestrak.org' or u.path not in ('/NORAD/elements/supplemental/sup-gp.php','/NORAD/elements/supplemental/table.php'):continue
        file=q.get('FILE',[''])[0]
        if file in allowed and re.fullmatch(r'[a-z0-9-]+',file):found.add(BASE+'?FILE='+file+'&FORMAT=JSON')
    return sorted(found)

def schema(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS sup_objects(id INTEGER PRIMARY KEY, identity TEXT UNIQUE, name TEXT, source_id INTEGER, object_id TEXT, catalog_id INTEGER, first_seen TEXT, last_seen TEXT, association TEXT);
    CREATE TABLE IF NOT EXISTS sup_predictions(object_id INTEGER, source TEXT, epoch TEXT, observed TEXT, altitude REAL, perigee REAL, apogee REAL, digest TEXT, raw TEXT, PRIMARY KEY(object_id,source,epoch,digest));
    ''')

def normalized(name):
    name=str(name).strip().upper()
    return name if re.fullmatch(r'STARLINK-\d+',name) else None

def reconcile(c):
    """Match only unique exact operator names, or consistent catalog ID/designator."""
    cats=[dict(x) for x in c.execute('SELECT * FROM satellites')]
    for obj in c.execute('SELECT * FROM sup_objects WHERE catalog_id IS NULL').fetchall():
        matches=[x for x in cats if (normalized(obj['name']) and normalized(obj['name'])==normalized(x['name'])) or (obj['source_id']==x['id'] and obj['object_id'] and obj['object_id']==x['object_id'])]
        if len(matches)==1:
            # Never infer identity from proximity, row ordering or the launch date alone.
            c.execute('UPDATE sup_objects SET catalog_id=? WHERE id=?',(matches[0]['id'],obj['id']))
    c.commit()

def ingest(c,rows,seen,url,mission_feed,orbital_values,launch_designator=None):
    errors=[];cats=[dict(x) for x in c.execute('SELECT * FROM satellites')]
    for raw in rows:
        name=str(raw.get('OBJECT_NAME') or '').strip();oid=str(raw.get('OBJECT_ID') or '')
        if not name.upper().startswith('STARLINK'):continue
        sid=int(raw['NORAD_CAT_ID'])
        matches=[x for x in cats if (normalized(name) and normalized(name)==normalized(x['name'])) or (sid==x['id'] and oid and oid==x['object_id'])]
        if len(matches)>1:errors.append('Ambiguous supplemental identity: '+name);continue
        # A constellation-wide feed is never assigned to this launch from epoch/date alone.
        launch_match=bool(launch_designator and re.fullmatch(re.escape(launch_designator)+r'[A-Z]+',oid))
        if not mission_feed and not matches and not launch_match:continue
        if mission_feed and cats and not matches and oid and any(x['object_id'] for x in cats):
            prefixes={re.sub(r'[A-Z]+$','',x['object_id']) for x in cats}
            if re.fullmatch(r'\d{4}-\d{3,4}[A-Z]+',oid) and re.sub(r'[A-Z]+$','',oid) not in prefixes:continue
        try:epoch,alt,peri,apo=orbital_values(raw,allow_prediction=True)
        except (KeyError,ValueError,TypeError) as e:errors.append(name+': '+str(e));continue
        # Operator satellite names survive replacement of temporary 799... identifiers.
        identity=normalized(name) or url+'|'+str(sid)+'|'+name
        association='Launch-specific feed (provisional)' if mission_feed else 'Verified launch designator; provisional ID' if launch_match and not matches else 'Matched to catalog member'
        c.execute('INSERT INTO sup_objects(identity,name,source_id,object_id,catalog_id,first_seen,last_seen,association) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(identity) DO UPDATE SET source_id=excluded.source_id,object_id=excluded.object_id,last_seen=excluded.last_seen',
                  (identity,name,sid,oid,matches[0]['id'] if matches else None,seen,seen,association))
        key=c.execute('SELECT id FROM sup_objects WHERE identity=?',(identity,)).fetchone()[0]
        payload=json.dumps(raw,sort_keys=True);digest=hashlib.sha256(payload.encode()).hexdigest()
        c.execute('INSERT OR IGNORE INTO sup_predictions VALUES(?,?,?,?,?,?,?,?,?)',(key,url,epoch,seen,alt,peri,apo,digest,payload))
    c.commit();reconcile(c);return errors

def history(c,object_ids):
    rows=[]
    for key in object_ids:
        rows.extend(dict(r) for r in c.execute('SELECT source,epoch,observed,altitude,perigee,apogee,raw FROM sup_predictions WHERE object_id=? ORDER BY epoch,observed',(key,)))
    for row in rows:
        raw=json.loads(row.pop('raw'))
        row.update({'inclination':raw.get('INCLINATION'),'eccentricity':raw.get('ECCENTRICITY'),'rms':raw.get('RMS'),'data_source':raw.get('DATA_SOURCE')})
    return sorted(rows,key=lambda r:(r['epoch'],r['observed']))

def collect(c,cfg,fetch,orbital_values):
    errors=[];seen=None;launch_urls=[]
    try:
        html,seen=fetch(c,INDEX,text=True)
        launch_urls=discover(html,cfg)
    except Exception as e:errors.append('Supplemental index: '+str(e))
    old=c.execute("SELECT value FROM meta WHERE key='supplemental_feeds'").fetchone()
    urls=sorted(set(launch_urls+(json.loads(old[0]) if old else [])))
    c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('supplemental_feeds',json.dumps(urls)));c.commit()
    for url in urls:
        try:
            rows,seen=fetch(c,url);errors.extend(ingest(c,rows,seen,url,True,orbital_values))
        except Exception as e:errors.append(str(e))
    # The general feed contains uncataloged launches too. Never gate it on SATCAT.
    url=BASE+'?FILE=starlink&FORMAT=JSON'
    matched=0
    try:
        rows,seen=fetch(c,url)
        errors.extend(ingest(c,rows,seen,url,False,orbital_values,cfg.get('international_designator')))
        matched=c.execute('SELECT count(*) FROM sup_objects').fetchone()[0]
    except Exception as e:errors.append('Starlink supplemental feed: '+str(e))
    reconcile(c)
    message=(str(matched)+' supplemental satellites tracked' if matched else
             'Supplemental feed checked; no records match the configured launch yet' if cfg.get('international_designator') else
             'Supplemental feed checked; a verified launch designator or mission file is needed to identify uncataloged payloads')
    return {'discovered_feeds':urls,'checked':seen,'message':message,'errors':errors}
