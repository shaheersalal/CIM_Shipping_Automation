# Container Aging & Export Reminder Automation -- POC

Proof-of-concept for detecting idle containers (empty at depot, import not
picked up, export not shipped) against a **per-container agreed free-days
value** and simulating a reminder/escalation email lifecycle. Built to
demonstrate the *logic*, not to connect to any live system.

## ⚠️ Synthetic data disclaimer

`Agreed_Free_Days` in `Activities_Demo_data_with_agreed_days.xlsx` is
**assumed/synthetic data added for this POC only**. The real per-container
agreed value will eventually come from parsing the client's email
correspondence and their ClimaxSuite system -- both out of scope here. Every
output file (report, summary, logs) carries this flag forward; do not treat
any number in this repo as the client's real business rule.

## Architecture (kept as 3 separate systems on purpose)

```
src/
  config_loader.py     load config/config.json
  rule_engine.py       idle detection + severity tiering ONLY
  email_templates.py   template filling + confidence scoring ONLY
  reminder_engine.py   reminder/escalation state machine ONLY
  main.py              orchestrates the three above, writes output/
config/
  config.json          thresholds, cadence, escalation count, contacts
output/                generated -- report, summary, reminder log
```

These three modules never call into each other's internals -- `main.py` is
the only place that wires them together. This mirrors how they'll likely
become three separate real services later (detection job, notification
service, escalation tracker).

## Core logic

1. **Idle detection** (`rule_engine.classify_idle`): a container is idle
   when `days > Agreed_Free_Days`, compared **per container**, per `Mode`:
   - `EMPTY` → `empty_at_depot`
   - `IMPORT FULL` → `import_not_picked`
   - `EXPORT FULL` → `export_not_shipped`
   - Excluded entirely: `Lease Rental Out`, `SWAPING`, `PROBLEM UNITS`,
     `PROBLEM UNITS (DCHE)`, `DISCHARGE FULL TRANSHIPMENT`.
   - There is **no fixed global day threshold** anywhere in this module.

2. **Severity tiering** (cosmetic/reporting only, never the trigger):
   Just Over (1-15 days overdue) / Moderate (16-45) / Severe (46+).

3. **Email confidence scoring** (`email_templates.build_email_report`):
   one fixed template per category, confidence starts at 100 and is
   penalized for missing `PORT` / `Depo` / `REGION_NAME` / `Country`.
   `email_status` is `READY` (score ≥ `confidence_threshold`, default 80)
   or `NEEDS REVIEW` with the specific missing field(s) listed.

4. **Reminder/escalation simulation** (`reminder_engine`): a JSON-backed
   state store tracks `reminder_count`, `last_reminder_date`, `escalated`,
   and an optional `commitment_date` per container. `process_run()` is
   driven by a `simulated_run_date` string so a reviewer can step through
   multiple "days" without real time passing:
   - 1st touch → `REMINDER_1`
   - every `reminder_cadence_days` (default 3) with no commitment →
     `REMINDER_2`, then `REMINDER_3`
   - after `escalation_trigger_count` (default 3) reminders →
     `ESCALATED_TO_DIRECTOR` instead of another reminder
   - a logged commitment (`register_commitment`) suppresses reminders
     until that date passes, then normal logic resumes

## Running it

```bash
cd emkay
python -m src.main
```

This regenerates everything in `output/`:

| File | Contents |
|---|---|
| `idle_container_report.xlsx` / `.csv` | One row per idle container: category, days overdue, severity tier, confidence score, email status, filled email text |
| `summary.md` | Counts by category / severity / confidence status, printed to console too |
| `reminder_escalation_log.csv` / `.md` | Every simulated reminder-engine event; the `.md` highlights two containers end-to-end: one with no commitment (reminder 1→2→3→escalation) and one with a logged commitment (reminder 1 → suppressed → resumes → 2→3→escalation) |
| `reminder_state.json` | The reminder engine's persisted state (reset at the start of each `main.py` run so the demo is reproducible) |

## Configuration (`config/config.json`)

All thresholds/cadences/contacts live here, not hardcoded in logic:
`confidence_threshold`, `reminder_cadence_days`, `escalation_trigger_count`,
`director_email` / `trade_team_email` placeholders, `severity_tiers`,
`confidence_penalties`. Swap this file once the client's real per-port
setup-form data is available.

## Explicitly out of scope for this POC

- No live ClimaxSuite connection
- No live email/inbox parsing
- No real email sending, no external API calls
- No "CRO-stage" pre-depot idle category (that's the shipping line's
  internal responsibility, not alerted on here)
- No fixed day-count thresholds anywhere in the detection logic
