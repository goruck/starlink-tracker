"""Word report from one immutable assessment snapshot; no network or DB access."""
from collections import defaultdict
from io import BytesIO
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT


def when(s):return s[:19].replace('T',' ')+' UTC' if s else 'Unavailable'
def number(v):return f'{v:.1f}' if v is not None else 'Unavailable'

def table(doc,headers,rows,widths):
 t=doc.add_table(rows=1,cols=len(headers));t.alignment=WD_TABLE_ALIGNMENT.CENTER;t.autofit=False
 props=t._tbl.tblPr
 borders=OxmlElement('w:tblBorders')
 for side in ['top','left','bottom','right','insideH','insideV']:
  e=OxmlElement('w:'+side);e.set(qn('w:val'),'single');e.set(qn('w:sz'),'4');e.set(qn('w:color'),'D9D9D9');borders.append(e)
 props.append(borders)
 repeat=OxmlElement('w:tblHeader');t.rows[0]._tr.get_or_add_trPr().append(repeat)
 for i,text in enumerate(headers):t.rows[0].cells[i].text=text
 for values in rows:
  cells=t.add_row().cells
  for i,text in enumerate(values):cells[i].text=str(text)
 for ri,row in enumerate(t.rows):
  keep=OxmlElement('w:cantSplit');row._tr.get_or_add_trPr().append(keep)
  for i,c in enumerate(row.cells):
   c.width=Inches(widths[i]);c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
   tc=c._tc.get_or_add_tcPr();m=OxmlElement('w:tcMar')
   for side in ['top','left','bottom','right']:
    e=OxmlElement('w:'+side);e.set(qn('w:w'),'50' if side in ('top','bottom') else '90');e.set(qn('w:type'),'dxa');m.append(e)
   tc.append(m)
   shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'DCE6F1' if ri==0 else 'FFFFFF');tc.append(shade)
   for p in c.paragraphs:
    p.paragraph_format.space_after=Pt(1);p.paragraph_format.space_before=Pt(1);p.paragraph_format.line_spacing=1
    p.alignment=WD_ALIGN_PARAGRAPH.LEFT if i in (0,3,4) else WD_ALIGN_PARAGRAPH.CENTER
    for r in p.runs:r.font.size=Pt(9);r.bold=ri==0
 for i,w in enumerate(widths):t.columns[i].width=Inches(w)
 doc.add_paragraph().paragraph_format.space_after=Pt(0)
 return t

def build_report(data):
 doc=Document();sec=doc.sections[0];sec.page_width=Inches(11);sec.page_height=Inches(8.5)
 sec.top_margin=sec.bottom_margin=Inches(.65);sec.left_margin=sec.right_margin=Inches(.65)
 normal=doc.styles['Normal'];normal.font.name='Calibri';normal.font.size=Pt(11);normal.paragraph_format.space_after=Pt(7)
 for name,size in [('Title',25),('Subtitle',12),('Heading 1',16),('Heading 2',12)]:
  style=doc.styles[name];style.font.name='Calibri';style.font.size=Pt(size);style.font.color.rgb=RGBColor(0,0,0)
 footer=sec.footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.RIGHT
 footer.add_run('Starlink deployment assessment  |  Page ')
 fld=OxmlElement('w:fldSimple');fld.set(qn('w:instr'),'PAGE');footer._p.append(fld)
 doc.add_paragraph('Starlink V3 Deployment Assessment',style='Title')
 doc.add_paragraph(data['mission']['name'],style='Subtitle')
 doc.add_paragraph('Generated '+when(data['generated'])+' from the local collector database.')
 summary=data['assessment_summary'];doc.add_paragraph(summary['conclusion'])
 doc.add_paragraph('This report screens the September 28 deployment for unusual orbital behavior. It does not establish satellite health, thruster activity or customer service activation. An absence of alerts is not confirmation that every spacecraft is healthy.')
 counts=summary['counts'];table(doc,['Tracked','No departure detected','Insufficient history','Data quality','Watch','Concern'],[[len(data['satellites']),counts['nominal'],counts['insufficient'],counts['data'],counts['watch'],counts['concern']]] ,[1.1,2,1.8,1.6,1.6,1.6])
 doc.add_heading('Data coverage',1)
 orbits=[s['display_orbit'] for s in data['satellites'] if s.get('display_orbit')]
 if orbits:doc.add_paragraph(f'{len(orbits)} satellites have orbital solutions. Displayed mean altitudes range from {min(x["altitude"] for x in orbits):.1f} to {max(x["altitude"] for x in orbits):.1f} km. Element epochs range from {when(min(x["epoch"] for x in orbits))} to {when(max(x["epoch"] for x in orbits))}.')
 doc.add_paragraph('Last collector run: '+when((data.get('collection') or {}).get('at'))+'. Analysis version: '+summary['version']+'.')
 errors=(data.get('collection') or {}).get('errors',[])
 if errors:doc.add_paragraph('Collector warnings: '+'; '.join(errors))
 if summary['fleet_data_issue']:doc.add_paragraph('Many satellites have stale data together. Investigate the source feed or collector before attributing the gap to spacecraft.')
 doc.add_paragraph('Service activation remains unconfirmed. Catalog operational status, when present, is reported separately and is not an exact switch on time.')
 doc.add_page_break();doc.add_heading('How the satellites look',1)
 for text in summary.get('plain_language',[]):doc.add_paragraph(text,style='List Bullet')
 # Deliberate page breaks keep the repeated roster easy to read and prevent split rows.
 sats=data['satellites']
 for start in range(0,len(sats),max(len(sats),1)):
  doc.add_page_break();doc.add_heading('Satellite assessment roster',1)
  doc.add_paragraph(f'Satellites {start+1} through {len(sats)} of {len(sats)}. Rates require independent updates spanning at least 24 hours. Ages refer to orbital element epochs; negative ages indicate forecasts.')
  rows=[]
  for s in sats:
   a=s['assessment'];o=s.get('display_orbit');m=a['metrics']
   rows.append([s['name']+'\n'+s['identity_label'].replace('Provisional source ID ','Temp ').replace('Catalog ID','Catalog'),(number(o['altitude'])+' km\n'+o['source_label']) if o else 'Unavailable',number(o['perigee'])+' / '+number(o['apogee']) if o else 'Unavailable',a['phase'],a['label'],number(o['age_hours'])+' h' if o else 'Unavailable',f'{m["rate"]:+.1f}' if m else 'Pending'])
  table(doc,['Satellite and identity','Mean altitude','Perigee / apogee km','Deployment phase','Assessment','Data age','km per day'],rows,[1.65,1.3,1.2,1.65,1.75,1,1.15])
 doc.add_page_break();doc.add_heading('Reasons and supporting evidence',1)
 groups=defaultdict(list)
 for s in sats:
  a=s['assessment'];key=(a['label'],a['reason'],tuple(f['message'] for f in a['findings']))
  groups[key].append(s['name'])
 if not groups:doc.add_paragraph('No satellite records have been collected.')
 for (label,reason,findings),names in groups.items():
  doc.add_heading(label,2);doc.add_paragraph('Applies to '+', '.join(names)+'.')
  doc.add_paragraph(reason)
  for f in findings:doc.add_paragraph(f)
 doc.add_heading('Interpretation limits',1)
 doc.add_paragraph('SupGP values are derived from operator ephemerides. Successive near current solutions can indicate a trend, but future trajectory points cannot demonstrate a completed maneuver. GP and SupGP streams are evaluated separately. The displayed source fit RMS is not a complete position uncertainty bound.')
 doc.add_paragraph('A hold may be intentional. Starlink uses waypoints, and atmospheric density affects raising. The published V3 mean altitude range of 330 to 360 km is context only; this mission has no configured exact target shell or commissioning deadline. Stable altitude in that band does not establish operational service.')
 doc.add_heading('Screening rules',1)
 r=summary['rules']
 doc.add_paragraph(f'Trend evidence requires {r["minimum_updates"]} distinct near current updates and at least {r["minimum_span_hours"]} hours of both epoch and receipt history, within a {r["window_hours"]} hour window. Forecasts first received before their epochs are excluded permanently from completed progress evidence. Median pairwise slopes reduce the effect of individual fluctuations.')
 doc.add_paragraph(f'Raising or falling screening uses {r["rate_threshold_km_day"]} km/day and at least {r["minimum_change_km"]} km cumulative change. Persistent perigee descent requires at least 75 percent decreasing intervals; concern requires {r["concern_updates"]} updates over {r["concern_span_hours"]} hours. Solutions older than {r["stale_hours"]} hours trigger data quality flags. These are configurable heuristics, not SpaceX engineering limits.')
 doc.add_paragraph(f'Peer checks require {r["minimum_peers"]} satellites from the same data stream, starting within {r["peer_altitude_band_km"]} km altitude and {r["peer_inclination_band_deg"]} degree inclination, with window endpoints within {r["peer_window_tolerance_hours"]} hours. Altitude changes of {r["jump_km"]} km within {r["jump_interval_hours"]} hours, inclination jumps of {r["inclination_jump_deg"]} degree, or cross source differences of {r["source_disagreement_km"]} km at nearby epochs trigger data review first.')
 doc.add_heading('Sources and provenance',1)
 for source in data.get('sources',[]):doc.add_paragraph(source['url']+'\nLast successful retrieval '+when(source.get('success'))+(('\nError '+source['error']) if source.get('error') else ''))
 doc.add_paragraph('Constellation lifecycle reference\n'+summary['reference'])
 doc.add_paragraph('SupGP format and forward fitting\nhttps://celestrak.org/NORAD/documentation/sup-gp-queries.php')
 doc.core_properties.title='Starlink V3 Deployment Assessment';doc.core_properties.subject='Orbital deployment screening';doc.core_properties.author='Local Starlink Tracker'
 # Remove inherited template rules from titles and headings.
 for element in [doc.styles.element,doc.element]:
  for border in element.xpath('.//w:pBdr'):
   border.getparent().remove(border)
 buf=BytesIO();doc.save(buf);return buf.getvalue()
