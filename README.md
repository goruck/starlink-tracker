# Starlink Orbit Watch

Local dashboard for the September 28, 2026 Starship mission (Flight 14 / Starlink 31-1).

## Install on this machine

Run from your normal Ubuntu terminal:

```bash
bash install.sh
```

Then open http://localhost:8765.

The installer copies this complete app to $HOME/develop/starlink-tracker, installs pinned dependencies, runs the tests, installs three user systemd units, starts the dashboard and collector, and verifies the local HTTP endpoint. It does not require sudo. Existing database history is retained. After the first complete install, mission.json is preserved on subsequent installs.

This output directory contains the complete application and installer. The installed copy is also updated when filesystem access permits; service activation is reported separately.

## Files and services

- Project: $HOME/develop/starlink-tracker
- Database: $HOME/.local/share/starlink-tracker/tracker.sqlite3
- Dashboard: starlink-tracker.service, bound to 127.0.0.1:8765
- Collector: starlink-collector.service, runs once and exits
- Schedule: starlink-collector.timer, every two hours while the user manager runs

User services normally run during your login session. This installer does not enable lingering; collection may stop after logout and does not run while the computer sleeps or is off. When the timer starts again it schedules a collection. There is no claim of continuous monitoring before installation.

```bash
systemctl --user status starlink-tracker.service starlink-collector.timer
journalctl --user -u starlink-collector.service -n 30
systemctl --user start starlink-collector.service
systemctl --user disable --now starlink-tracker.service starlink-collector.timer
```

The dashboard Refresh button rereads the local database; it never bypasses the source cache or triggers extra public requests.

## What the app measures

- Catalog candidates and NORAD IDs, including six-digit IDs.
- Mean altitude, perigee and apogee estimates from OMM-compatible JSON elements.
- History of distinct orbital-element epochs, deduplicated by satellite and epoch.
- Estimated raising, stable or lowering trend: compare observations 12–72 hours apart, favoring 24 hours; require a 2 km change for raising/lowering.
- Orbital position propagated by Skyfield, only with elements within 72 hours of now.
- Each CelesTrak catalog-status observation and a status-change timeline.
- First observed operational (+), distinguishing an already-operational first observation from a later transition.

Mean altitude uses the Keplerian mean-motion relation with Earth gravitational parameter 398600.4418 km³/s² and a reference radius of 6378.137 km. It is an approximate comparison metric rather than a precise geodetic altitude. Raising is an inference, not direct thruster telemetry. The chart uses element epochs, not repeated propagated positions, so orbital eccentricity is not mistaken for orbit raising.

There is no publicly verified per-satellite service switch-on feed configured. Catalog operational status is not a confirmed customer-service activation event. Actual activation remains unconfirmed, and the app does not infer it from an altitude threshold.

History begins when observations are collected. There is no invented historical backfill. Catalog observation timestamps are receipt times, not source event times. The first operational observation gives an upper bound on when that status was observed; a preceding observation brackets the catalog change only.

## Mission association

At the initial check, CelesTrak returned 12,892 Starlink catalog records and none with launch date 2026-09-28. Its launch-site table did not yet list a Starbase code. Therefore the app uses explicitly **provisional** matching of Starlink payloads on that date, only when all matching payloads share one international launch designator. It stops selection and reports an error if several launches match.

V3 identity is not present in the GP elements. Confirm the launch association from a mission source and set international_designator in mission.json once published (format YYYY-NNN, without the satellite piece suffix). Do not guess the identifier. An optional launch_sites list restricts selection to verified catalog site codes. The app does not hardcode a deployment count or fabricated NORAD IDs.

Changing to an entirely different mission requires a separate data directory, because known membership and history are intentionally retained when feed entries temporarily disappear.

## Data sources and recovery

CelesTrak HTTPS endpoints are used:
- SATCAT: https://celestrak.org/satcat/records.php?NAME=STARLINK&FORMAT=JSON
- GP elements: https://celestrak.org/NORAD/elements/gp.php?INTDES=VERIFIED_OR_PROVISIONAL_LAUNCH&FORMAT=JSON

The app caches successful data and request attempts for at least two hours. HTTP 5xx server errors, temporary transport errors, and schema errors retry after the two-hour cache interval. Other HTTP errors pause that source for inspection instead of repeatedly retrying. Inspect the error first. After resolving an HTTP problem, resume paused sources:

```bash
cd $HOME/develop/starlink-tracker
.venv/bin/python tracker.py resume-sources
systemctl --user start starlink-collector.service
```

No account, location or personal data is sent. Public requests reveal the host's IP and requested satellite/launch query to CelesTrak. The browser serves all assets locally, with no CDNs or third-party scripts.

## Manual development

```bash
cd $HOME/develop/starlink-tracker
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tracker.py collect
.venv/bin/python tracker.py serve
```

STARLINK_DATA_DIR can isolate test data from real observations. The automated tests use synthetic records in temporary databases, not the live catalog.

## References

- https://celestrak.org/NORAD/documentation/gp-data-formats.php
- https://celestrak.org/satcat/satcat-format.php
- https://celestrak.org/satcat/status.php
- https://celestrak.org/satcat/launchsites.php
- https://rhodesmill.org/skyfield/api-satellites.html
- https://www.spacex.com/launches


## Supplemental data

The collector independently checks the CelesTrak supplemental index every two hours, even when SATCAT has no mission members. It requests only published mission-specific FILE links matching the configured supplemental_files list (default: starlink-g31-1); it never guesses an unpublished URL. Previously discovered links remain monitored. The published general FILE=starlink feed is fetched independently of catalog membership and index availability. Records are accepted only when they match a catalog member or the configured exact launch designator (including a valid satellite-piece suffix).

SupGP predictions are stored in separate sup_objects and sup_predictions tables, including source URL, receipt time, epoch, raw data and a content digest. Future epochs and revised predictions for the same epoch are preserved. They never enter the GP trend classifier or operational-status timeline. The detail panel shows the most recent 100 prediction records; the database retains all records.

Launch-feed objects have provisional source IDs. Unique exact STARLINK-number operator names, or consistent catalog ID plus full designator, can reconcile them to catalog members; ambiguous matches remain separate. Reconciliation retains the supplemental object record and prediction history, and old detail links resolve to the matched catalog object. No matching by orbital proximity, row order or epoch is performed.

The launch-feed naming convention is configurable in mission.json with supplemental_files, for example ["starlink-g31-1"]. This is an exact allowlist for published links, not a statement that a feed currently exists. Initial inspection of the published index on September 28 found the general Starlink feed but no matching launch-specific link.


### September 29 discovery fix

The original supplemental collector gated the general feed on existing SATCAT membership and therefore missed uncataloged payloads. The corrected collector independently checks that feed. Launch 2026-225 was verified against Jonathan McDowell's GCAT (https://planet4589.com/space/gcat/data/cat/satcat700M.html), which associates the records with Starlink Group 31-1, Starlink V3, September 28. Configuration now pins that designator. The live general feed yielded 26 matching temporary-ID records, which were ingested and verified through the running dashboard API. A 799 prefix alone is never used to assign mission membership.

## Deployment assessments and Word reports

The dashboard now displays the best available numeric orbital estimate, its source, epoch, age, deployment phase, assessment, and reason. Detail pages include perigee/apogee, inclination, eccentricity, source RMS, source-isolated history, peer comparisons where available, and findings. RMS is fit metadata, not a complete uncertainty bound.

Screening is implemented in assessment.py. At least three independently received near-current solutions spanning 24 hours are required. Multiple forecast epochs in one receipt and revisions at the same epoch cannot manufacture history. A forecast first received before its epoch never becomes completed-progress evidence simply because time passes. GP and each SupGP data source are evaluated separately. SupGP progress means changes in operator-derived orbital solutions, not direct thruster telemetry.

Findings distinguish insufficient history, data quality issues, watches, concerns, and no detected divergence. They screen for persistent falling perigee, failure to keep pace with comparable raising peers, abrupt element discontinuities, disagreement between source solutions, stale records, and withdrawal of operational catalog status. Widespread staleness is explicitly identified as a possible feed or collector problem. Planned holds and destination differences remain plausible alternatives; no health or service-activation claim is made.

Defaults are in assessment.DEFAULTS and are shown in the dashboard and each report. Override named values in mission.json under assessment_rules. For example: "assessment_rules": {"stale_hours": 36, "rate_threshold_km_day": 3}. The validator prevents reducing the minimum evidence below three updates or 24 hours. These thresholds are screening heuristics, not manufacturer limits. No exact target shell or deployment deadline is assumed.

Use **Download Word report** to generate a .docx for the full fleet from a single consistent database snapshot. The report includes the conclusion, counts, source freshness, per-satellite altitude and assessment roster, grouped reasons and findings, limitations, thresholds, and source URLs. Generation uses only the local database; it does not fetch public data, send data elsewhere, or change observations. The download endpoint is GET /api/report.docx.

After installing this update, restart the dashboard to load the new backend:

```bash
systemctl --user restart starlink-tracker.service
```

The collection timer automatically uses the updated Python modules on its next invocation. Existing history is retained.
