"""Explainable deployment screening. Heuristics are not spacecraft health telemetry."""
from datetime import datetime, timezone
from statistics import median
import math

VERSION='deployment-screening-v1'
DEFAULTS={
 'minimum_updates':3,'minimum_span_hours':24,'window_hours':72,
 'stale_hours':24,'concern_span_hours':48,'concern_updates':4,
 'rate_threshold_km_day':2,'minimum_change_km':2,
 'peer_altitude_band_km':10,'peer_inclination_band_deg':1,'minimum_peers':3,
 'peer_window_tolerance_hours':6,'jump_km':10,'jump_interval_hours':6,
 'inclination_jump_deg':0.2,'source_disagreement_km':10,
 'candidate_shell_min_km':330,'candidate_shell_max_km':360,
}
REFERENCE='https://space-safety.starlink.com/docs/space-safety-articles/constellation_altitudes/'

def date(s):
 t=datetime.fromisoformat(s.replace('Z','+00:00'))
 return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
def hours(a,b):return (date(a)-date(b)).total_seconds()/3600

def settings(config):
 rules=dict(DEFAULTS)
 for key,value in config.get('assessment_rules',{}).items():
  if key not in rules or isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or value<=0:
   raise ValueError('Invalid assessment rule '+key)
  rules[key]=value
 if rules['minimum_updates']<3 or rules['minimum_span_hours']<24 or rules['window_hours']<rules['minimum_span_hours'] or rules['concern_span_hours']<rules['minimum_span_hours'] or rules['concern_updates']<rules['minimum_updates'] or rules['candidate_shell_max_km']<=rules['candidate_shell_min_km']:
  raise ValueError('Assessment thresholds cannot weaken minimum evidence requirements')
 return rules

def stream_rows(s):
 rows=[]
 for r in s.get('history',[]):rows.append(dict(r,stream='GP',source_label='GP estimate'))
 for r in s.get('supplemental_history',[]):
  rows.append(dict(r,stream='SupGP '+str(r.get('data_source') or r.get('source','unknown')),source_label='SupGP estimate'))
 return rows

def independent(rows,current,rules):
 """One near-current solution per receipt, unique increasing epochs, no forecasts."""
 by_receipt={}
 for r in sorted(rows,key=lambda x:(x['observed'],x['epoch'])):
  if not all(math.isfinite(r[k]) for k in ('altitude','perigee','apogee')):continue
  # A record first received before its epoch remains a forecast, even after that epoch passes.
  if not 0<=hours(r['observed'],r['epoch'])<=rules['stale_hours']:continue
  if not 0<=hours(current,r['epoch'])<=rules['window_hours'] or hours(current,r['observed'])<0:continue
  prev=by_receipt.get(r['observed'])
  if prev is None or r['epoch']>prev['epoch']:by_receipt[r['observed']]=r
 result=[]
 for r in sorted(by_receipt.values(),key=lambda x:x['observed']):
  if result and date(r['epoch'])<=date(result[-1]['epoch']):continue
  result.append(r)
 return result

def metrics(rows,rules):
 if len(rows)<rules['minimum_updates']:return None
 epoch_span=hours(rows[-1]['epoch'],rows[0]['epoch']);receipt_span=hours(rows[-1]['observed'],rows[0]['observed'])
 if min(epoch_span,receipt_span)<rules['minimum_span_hours']:return None
 def slope(key):
  values=[(b[key]-a[key])*24/hours(b['epoch'],a['epoch']) for i,a in enumerate(rows) for b in rows[i+1:] if hours(b['epoch'],a['epoch'])>0]
  return median(values)
 return {'count':len(rows),'span_hours':epoch_span,'receipt_span_hours':receipt_span,'rate':slope('altitude'),'perigee_rate':slope('perigee'),
         'delta':rows[-1]['altitude']-rows[0]['altitude'],'perigee_delta':rows[-1]['perigee']-rows[0]['perigee'],
         'descending_fraction':sum(b['perigee']<a['perigee'] for a,b in zip(rows,rows[1:]))/(len(rows)-1),
         'start_epoch':rows[0]['epoch'],'end_epoch':rows[-1]['epoch'],'start_altitude':rows[0]['altitude'],'inclination':rows[0].get('inclination')}

def assess(snapshot):
 rules=settings(snapshot['mission']);current=snapshot['generated'];items=snapshot['satellites'];internal={}
 for sat in items:
  rows=stream_rows(sat);groups={}
  for row in rows:groups.setdefault(row['stream'],[]).append(row)
  candidates=[]
  for stream,rr in groups.items():
   points=independent(rr,current,rules)
   candidates.append((stream,points,metrics(points,rules)))
  # Select a recent sufficient stream; otherwise the most recent eligible stream.
  eligible=[x for x in candidates if x[1] and hours(current,x[1][-1]['epoch'])<=rules['stale_hours']]
  chosen=max(eligible or [x for x in candidates if x[1]],key=lambda x:(x[2] is not None,date(x[1][-1]['epoch'])),default=(None,[],None))
  stream,points,m=chosen
  valid=[r for r in rows if date(r['observed'])<=date(current)]
  near=[r for r in valid if date(r['epoch'])<=date(current)]
  # Prefer newest element epoch that is not in the future; forecasts remain explicitly labeled.
  nonforecast=[r for r in near if date(r['epoch'])<=date(r['observed'])]
  latest=max(nonforecast or near,key=lambda r:(date(r['epoch']),date(r['observed'])),default=None)
  if latest is None and valid:latest=min(valid,key=lambda r:abs(hours(r['epoch'],current)))
  if latest:
   latest=dict(latest);latest['forecast']=hours(latest['epoch'],latest['observed'])>0
   latest['age_hours']=hours(current,latest['epoch']);latest['receipt_age_hours']=hours(current,latest['observed'])
   if latest['forecast']:latest['source_label']='SupGP forecast' if latest['stream']!='GP' else 'GP future elements'
  sat['display_orbit']=latest
  sat['analysis_history']=points
  findings=[];level='insufficient';label='Insufficient history';phase='Initial orbit' if latest else 'Awaiting orbital data'
  evidence=f'{len(points)} independent near-current update'+('' if len(points)==1 else 's')+f'; need at least {rules["minimum_updates"]} spanning {rules["minimum_span_hours"]} hours.'
  if latest and rules['candidate_shell_min_km']<=latest['altitude']<=rules['candidate_shell_max_km']:phase='Near candidate shell'
  def flag(severity,code,message):findings.append({'severity':severity,'code':code,'message':message})
  if not latest:flag('data','missing','No orbital solutions have been collected.')
  elif latest['forecast']:
   flag('data','forecast','Available solution was published for a future epoch; it is not evidence of completed raising.')
   forecast=sorted([r for r in rows if r['stream']==latest['stream'] and r['observed']==latest['observed'] and hours(r['epoch'],r['observed'])>0],key=lambda r:r['epoch'])
   if len(forecast)>1 and forecast[-1]['altitude']-forecast[0]['altitude']>=rules['minimum_change_km']:phase='Raising predicted'
  elif latest['age_hours']>rules['stale_hours'] or latest['receipt_age_hours']>rules['stale_hours']:
   flag('data','stale',f'Orbital solution is {latest["age_hours"]:.1f} hours old; last distinct solution was received {latest["receipt_age_hours"]:.1f} hours ago.')
  for a,b in zip(points,points[1:]):
   gap=hours(b['epoch'],a['epoch']);altjump=abs(b['altitude']-a['altitude'])
   incjump=abs(b.get('inclination',0)-a.get('inclination',0)) if a.get('inclination') is not None and b.get('inclination') is not None else 0
   if (gap<=rules['jump_interval_hours'] and altjump>=rules['jump_km']) or incjump>=rules['inclination_jump_deg']:
    flag('data','discontinuity',f'Solution discontinuity between {a["epoch"]} and {b["epoch"]}: altitude {altjump:.1f} km, inclination {incjump:.3f} deg. Check identity, maneuver and fit before interpreting.');break
  # Compare sources only at nearby epochs, and keep this a data warning.
  others=[x[1][-1] for x in eligible if x[1] and x[0]!=stream]
  if points:
   for other in others:
    if abs(hours(other['epoch'],points[-1]['epoch']))<=1 and abs(other['altitude']-points[-1]['altitude'])>=rules['source_disagreement_km']:
     flag('data','source_disagreement','Independent source solutions at nearby epochs disagree in altitude; do not interpret the source change as a maneuver.');break
  if m and not findings:
   level='nominal';label='Consistent with expected behavior'
   evidence=f'{m["count"]} independent {stream} solutions over {m["span_hours"]:.1f} hours: mean altitude {m["delta"]:+.1f} km, trend {m["rate"]:+.1f} km/day; perigee {m["perigee_delta"]:+.1f} km.'
   if m['rate']>=rules['rate_threshold_km_day'] and m['delta']>=rules['minimum_change_km']:phase='Raising indicated'
   elif abs(m['rate'])<rules['rate_threshold_km_day']:phase='Possible waypoint or hold' if phase!='Near candidate shell' else phase
   else:phase='Lowering indicated'
   if m['perigee_rate']<=-rules['rate_threshold_km_day'] and m['perigee_delta']<=-rules['minimum_change_km'] and m['descending_fraction']>=0.75:
    concern=min(m['span_hours'],m['receipt_span_hours'])>=rules['concern_span_hours'] and m['count']>=rules['concern_updates']
    flag('concern' if concern else 'watch','descent','Persistent falling perigee across successive solutions. Intentional lowering, drag, or a deployment problem are possible; health and intent are unknown.')
  statuses=sat.get('statuses',[])
  if statuses and hours(current,statuses[-1]['observed'])<=rules['stale_hours']:
   if statuses[-1]['code']=='+':phase='Catalog reported operational'
   elif any(x['code']=='+' for x in statuses[:-1]):
    flag('data' if statuses[-1]['code']=='?' else 'watch','status_change','Previously operational catalog status is no longer reported. This is a catalog change, not a confirmed service outage.')
  internal[str(sat['id'])]=(stream,points,m)
  sat['assessment']={'level':level,'label':label,'phase':phase,'reason':evidence,'findings':findings,'metrics':m,'stream':stream,'peers':None,
    'limitations':'Screening heuristics, not SpaceX health telemetry. SupGP trends are changes in operator-derived solutions; completed thrust and customer-service activation are unconfirmed.'}
 # Peer comparisons are limited to similar starting orbits, source and time window.
 for sat in items:
  a=sat['assessment'];stream,points,m=internal[str(sat['id'])]
  if m and not any(f['severity']=='data' for f in a['findings']):
   peers=[]
   for other in items:
    if other is sat:continue
    os,op,om=internal[str(other['id'])]
    if os!=stream or not om or any(f['severity']=='data' for f in other['assessment']['findings']):continue
    if m['inclination'] is None or om['inclination'] is None:continue
    if abs(m['start_altitude']-om['start_altitude'])>rules['peer_altitude_band_km'] or abs(m['inclination']-om['inclination'])>rules['peer_inclination_band_deg']:continue
    if max(abs(hours(m[k],om[k])) for k in ('start_epoch','end_epoch'))>rules['peer_window_tolerance_hours']:continue
    peers.append(om['rate'])
   if len(peers)>=rules['minimum_peers']:
    rate=median(peers);a['peers']={'count':len(peers),'median_rate':rate}
    if rate>=rules['rate_threshold_km_day'] and m['rate']<0.5:
     a['findings'].append({'severity':'watch','code':'peer_divergence','message':f'Trend {m["rate"]:+.1f} km/day versus {rate:+.1f} km/day for {len(peers)} comparable peers. A planned hold or different destination remains plausible.'})
   if not peers:a['reason']+=' No comparable peer history; fleet-relative behavior is not assessed.'
  priorities={'concern':4,'watch':3,'data':2}
  if a['findings']:
   level=max((x['severity'] for x in a['findings']),key=lambda x:priorities[x]);a['level']=level
   a['label']={'concern':'Concern','watch':'Watch','data':'Data quality issue'}[level]
  # Never call a stationary early-orbit satellite healthy solely because no anomaly fired.
  if a['level']=='nominal' and a['phase']=='Possible waypoint or hold':
   a['label']='No divergence detected';a['reason']+=' A planned waypoint or delayed raising cannot be distinguished from public orbital data alone.'
 counts={key:sum(s['assessment']['level']==key for s in items) for key in ['nominal','insufficient','data','watch','concern']}
 stale=sum(any(f['code']=='stale' for f in s['assessment']['findings']) for s in items)
 fleet_issue=len(items)>0 and stale>=max(2,len(items)*.5)
 if fleet_issue:
  for s in items:
   for f in s['assessment']['findings']:
    if f['code']=='stale':f['message']+=' Many tracked satellites are stale together; a feed or collector problem is more likely than independent satellite failures.'
 snapshot['assessment_summary']={'version':VERSION,'counts':counts,'fleet_data_issue':fleet_issue,'rules':rules,
  'conclusion':('No satellite records available.' if not items else 'Potential departures require review; none confirms spacecraft failure.' if counts['watch']+counts['concern'] else 'Data quality prevents a reliable fleet assessment.' if counts['data'] else 'Insufficient history to judge deployment progress.' if counts['insufficient'] else 'No screening departures detected in the available orbital history.'),
  'reference':REFERENCE}
 snapshot['assessment_summary']['plain_language']=plain_language(snapshot,rules)
 return snapshot


def plain_language(snapshot,rules):
 """Readable facts from the same qualified evidence used by assessments."""
 sats=snapshot['satellites'];bullets=[]
 fresh=[s for s in sats if s.get('display_orbit') and not s['display_orbit']['forecast'] and max(s['display_orbit']['age_hours'],s['display_orbit']['receipt_age_hours'])<=rules['stale_hours']]
 if fresh:
  alts=[s['display_orbit']['altitude'] for s in fresh];low=rules['candidate_shell_min_km'];high=rules['candidate_shell_max_km']
  below=sum(x<low for x in alts);inside=sum(low<=x<=high for x in alts)
  bullets.append(f"{len(fresh)} of {len(sats)} satellites have recent estimated mean altitudes of {min(alts):.1f}–{max(alts):.1f} km. {below} are below the {low:g}–{high:g} km candidate band; {inside} are inside it. This band is a reference, not a confirmed destination for each satellite.")
 if len(fresh)<len(sats):bullets.append(f"{len(sats)-len(fresh)} satellites lack a fresh, nonforecast solution; their current progress cannot be judged reliably.")
 usable=[s for s in fresh if s['assessment']['metrics'] and not any(f['severity']=='data' for f in s['assessment']['findings'])]
 if not usable:
  bullets.append('There is not enough reliable history to summarize climbing, circularization or satellites falling behind.');return bullets
 ms=[s['assessment']['metrics'] for s in usable];rising=[s for s in usable if s['assessment']['metrics']['rate']>0.05]
 spans=[m['span_hours'] for m in ms]
 bullets.append(f"{len(rising)} of {len(usable)} satellites with usable trends are climbing. Mean-altitude rates range from {min(m['rate'] for m in ms):+.1f} to {max(m['rate'] for m in ms):+.1f} km/day over {min(spans):.0f}–{max(spans):.0f} hours of history. These are changes in orbital solutions, not confirmed thrust.")
 leaders=sorted(usable,key=lambda s:s['assessment']['metrics']['rate'],reverse=True)[:2]
 if leaders and leaders[0]['assessment']['metrics']['rate']>0.05:bullets.append('Fastest estimated climbs: '+', '.join(f"{s.get('name',str(s['id']))} ({s['assessment']['metrics']['rate']:+.1f} km/day)" for s in leaders if s['assessment']['metrics']['rate']>0.05)+'.')
 circular=[s for s in usable if s['assessment']['metrics']['perigee_rate']>max(0,s['assessment']['metrics']['rate'])+0.05 and (s['analysis_history'][-1]['apogee']-s['analysis_history'][-1]['perigee'])<(s['analysis_history'][0]['apogee']-s['analysis_history'][0]['perigee'])]
 bullets.append(f"For {len(circular)} of {len(usable)} satellites, perigee (the lowest point of the orbit) is rising faster than mean altitude and the gap between high and low points is shrinking. That is consistent with the orbit becoming more circular; it does not by itself establish normal spacecraft operation.")
 slow=[s for s in usable if s['assessment']['metrics']['rate']<0.5]
 if slow:bullets.append('Slow or downward trends to watch (below +0.5 km/day): '+', '.join(f"{s.get('name',str(s['id']))} ({s['assessment']['metrics']['rate']:+.1f} km/day)" for s in slow)+'. These are descriptive watch candidates; a planned hold remains possible.')
 descent=[s.get('name',str(s['id'])) for s in usable if any(f['code']=='descent' for f in s['assessment']['findings'])]
 bullets.append(('Persistent falling-perigee flags: '+', '.join(descent)+'.') if descent else 'None of the satellites with usable trends meets the persistent falling-perigee rule.')
 bullets.append(f"The falling rule requires perigee to decline at least {rules['rate_threshold_km_day']:g} km/day, fall at least {rules['minimum_change_km']:g} km overall, and decline in at least 75% of successive intervals. Service activation remains unconfirmed.")
 return bullets
