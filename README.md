# Container Aging & Export Reminder Automation -- POC (v2, port-centric)

Proof-of-concept for detecting idle containers (empty at depot, import not
picked up, export not shipped), routing confidence-scored reminder emails
to the right person, and simulating a reminder → Head → Director escalation
lifecycle. Built to demonstrate the *logic*, not to connect to any live
system.

**v2 revision:** after demoing v1 (which used a synthetic per-container
free-days value) to the client, requirements were corrected: free days are
looked up **by Port** from a Setup/Configuration table the client
maintains, everything is viewed **Port-first**, and each port has its own
Trade Person / Head / Director for routing and escalation. See "What
changed from v1" below.

## ⚠️ Sample data disclaimer

The Port/Activity-Type **Setup & Configuration table** is seeded from
`Storage_slab.xlsx`, a sample covering **13 of the 144 real ports** that
appear in the activity data (`Activities_Demo_data.xlsx`). Trade Person /
Head / Director emails are POC placeholders (`trade.<port>@emkay-shipping.example`
etc.), not real contacts. Every output (dashboard, report, logs) surfaces
this -- ports without a configured row are shown as **unconfigured**, never
silently dropped.

## Architecture (kept as separate systems on purpose)

```
src/
  config_loader.py      load config/config.json (app-level thresholds only)
  port_config_store.py  Setup/Configuration table: seed parsing, CRUD
                         schema, Port[+Activity Type] -> Free Days/contacts lookup
  rule_engine.py         idle detection + severity tiering ONLY
  email_templates.py    template filling + confidence scoring ONLY
  reminder_engine.py    reminder/escalation state machine ONLY
  main.py               CLI orchestrator, writes output/
config/
  config.json           confidence threshold, cadence, escalation trigger count
streamlit_app.py         interactive demo UI (Setup CRUD, port dashboard, etc.)
Activities_Demo_data.xlsx   container-level activity data (unchanged from v1)
Storage_slab.xlsx           sample Port -> Free Days reference (seeds Setup table)
```

These modules never call into each other's internals -- `main.py` /
`streamlit_app.py` are the only places that wire them together. This
mirrors how they'll likely become separate real services later (detection
job, notification service, escalation tracker, a real setup-form API).

## Core logic

1. **Setup & Configuration** (`port_config_store.py`, client requirement
   doc §4.1) -- 6 fields per row: `Port`, `Activity Type`, `Free Days`,
   `Trade Person Email`, `Head Email`, `Director Email`. A blank Activity
   Type is a port-level default. Lookup order: exact Port+Activity Type →
   Port-level default → **unconfigured** (never a hidden fallback).

2. **Idle detection** (`rule_engine.classify_idle`): `days > Free_Days`
   (resolved per-container via its Port), compared per container, by
   `Mode`:
   - `EMPTY` → `empty_at_depot`
   - `IMPORT FULL` → `import_not_picked`
   - `EXPORT FULL` → `export_not_shipped`
   - Excluded entirely: `Lease Rental Out`, `SWAPING`, `PROBLEM UNITS`,
     `PROBLEM UNITS (DCHE)`, `DISCHARGE FULL TRANSHIPMENT`.
   - There is **no fixed global day threshold** anywhere in this module.
   - Containers at a port with no usable Free Days rule are returned
     separately as `unconfigured_df`, not dropped.

3. **Severity tiering** (cosmetic/reporting only, never the trigger):
   Just Over (1-15 days overdue) / Moderate (16-45) / Severe (46+).

4. **Email confidence scoring** (`email_templates.build_email_report`):
   one fixed template per category, confidence starts at 100 and is
   penalized for missing `PORT` / `Depo` / `REGION_NAME` / `Country` /
   **Trade Person Email**. Recipient is the resolved Trade Person Email for
   that container's port; if missing, the email shows "NO RECIPIENT
   CONFIGURED" instead of a placeholder, and confidence is forced low.

5. **Reminder/escalation simulation** (`reminder_engine`, two-tier per
   client doc §4.3/4.4): state store tracks `reminder_count`,
   `escalation_stage` (`none`/`head`/`director`), and an optional
   `commitment_date` per container. `process_run()` is driven by a
   `simulated_run_date` string:
   - 1st touch → `REMINDER_1`
   - every `reminder_cadence_days` (default 3) with no commitment →
     `REMINDER_2`, then `REMINDER_3`
   - after `escalation_trigger_count` (default 3) reminders →
     `ESCALATED_TO_HEAD` (that port's Head Email)
   - one more cadence period unresolved → `ESCALATED_TO_DIRECTOR` (that
     port's Director Email) -- this second wait isn't specified exactly by
     the client's doc, so it's an assumed default, flagged in the UI
   - a logged commitment suppresses reminders/escalation until that date
     passes, then normal logic resumes

## Running it

### CLI
```bash
cd emkay
python -m src.main
```
Regenerates `output/idle_container_report.xlsx`/`.csv`,
`unconfigured_ports_report.csv`, `port_summary.csv`, `summary.md`, and
`reminder_escalation_log.csv`/`.md` (the `.md` highlights two containers
end-to-end through the full lifecycle, including per-port resolved
recipients).

### Streamlit app
```bash
streamlit run streamlit_app.py
```
Tabs: **Dashboard** (Port-first: idle counts, configured/unconfigured
status, Trade Person, drill into any port for Size/Type/CKind breakdown
and container detail), **Idle Containers** (filterable detail table, with
a toggle to view unconfigured-port containers instead), **Email Preview**,
**Reminder Simulator** (interactive lifecycle demo), **Setup &
Configuration** (live CRUD table -- add/edit/delete a port's config
during a demo and everything recomputes instantly, plus the open
questions/assumptions below).

## Explicit open questions (surfaced in the Setup & Configuration tab)

- Whether `AGENT NAME` (ClimaxSuite) and `Trade Person Email` (Setup form)
  are the same person or different roles -- currently treated as
  **different and unconfirmed**.
- Whether Free Days genuinely varies by Activity Type within a port, or is
  usually uniform -- built to support both, defaulting to the port-level
  row when Activity Type isn't specified.
- The seeded Setup data covers a small fraction of real ports --
  demonstration only.
- The Head→Director wait period reuses the reminder cadence as an assumed
  default (not specified exactly in the client's doc).

## What NOT to build in this POC

- No live ClimaxSuite connection
- No live email/inbox parsing or extraction
- No real email sending, no external API calls
- No "CRO-stage" pre-depot idle category
- No fixed global day-count thresholds anywhere -- always resolved via the
  Setup/Configuration table

## What changed from v1

| | v1 | v2 |
|---|---|---|
| Free days source | Synthetic `Agreed_Free_Days` per container row | Looked up by Port (+ Activity Type) from a Setup table |
| Primary view | Container-first | Port-first, with container drill-down |
| Email recipient | Global placeholder ("Trade Team") | Resolved Trade Person Email per port |
| Escalation target | Global dummy director email | That port's Head, then Director |
| Escalation tiers | Single: reminders → escalate | Two-tier: reminders → Head → Director |
| Config screen | Sidebar assumptions only | First-class CRUD tab (add/edit/delete live) |
| Unconfigured containers | N/A | Explicitly surfaced, never dropped |

`Activities_Demo_data_with_agreed_days.xlsx` (the v1 input) is left in the
repo for reference but is no longer used by any code path.
