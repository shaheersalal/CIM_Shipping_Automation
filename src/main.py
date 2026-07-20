"""
Orchestrator for the Container Aging & Export Reminder Automation POC.

Wires together the three independent systems (kept separate on purpose,
since they become three real services later):
    1. rule_engine       -- idle detection + severity tiering
    2. email_templates    -- confidence-scored email template filling
    3. reminder_engine   -- reminder/escalation state machine, driven by
                            a simulated clock (`simulated_run_date`)

Run with:  python -m src.main
(from the emkay/ project root, with the xlsx alongside it)

IMPORTANT: Agreed_Free_Days in the source workbook is ASSUMED SYNTHETIC
DATA for this POC (see Agreed_Days_Source column). Nothing here should be
read as the client's real business rule -- that flag is carried through
to every output file.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config
from src.rule_engine import classify_idle, SYNTHETIC_DATA_NOTICE
from src.email_templates import build_email_report
from src.reminder_engine import ReminderStateStore, process_run

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_XLSX = PROJECT_ROOT / "Activities_Demo_data_with_agreed_days.xlsx"
OUTPUT_DIR = PROJECT_ROOT / "output"
STATE_FILE = OUTPUT_DIR / "reminder_state.json"

# Arbitrary fixed anchor date for the POC's simulated clock. Real detection
# dates in the source data vary wildly (some `days` values exceed 3000),
# so the reminder demo uses its own clock purely to show the state
# machine advancing -- it is not derived from Activity Date.
SIM_START_DATE = date(2026, 7, 20)


def build_idle_and_email_report(config: dict) -> pd.DataFrame:
    df = pd.read_excel(INPUT_XLSX)
    idle_df = classify_idle(df, config)
    report_df = build_email_report(idle_df, config)
    return report_df


def write_report_outputs(report_df: pd.DataFrame):
    cols = [
        "CONTAINERNO", "SIZE", "TYPE", "PORT", "REGION_NAME", "Country",
        "Depo", "idle_category", "days", "Agreed_Free_Days", "days_overdue",
        "severity_tier", "depo_missing", "region_missing", "country_missing",
        "confidence_score", "review_reasons", "email_status",
        "agreed_days_is_synthetic", "agreed_days_source_note", "email_text",
    ]
    out = report_df[cols]
    out.to_excel(OUTPUT_DIR / "idle_container_report.xlsx", index=False)
    out.to_csv(OUTPUT_DIR / "idle_container_report.csv", index=False)


def build_summary(report_df: pd.DataFrame) -> str:
    by_category = report_df["idle_category"].value_counts()
    by_severity = report_df["severity_tier"].value_counts()
    by_status = report_df["email_status"].value_counts()
    depo_gaps = int(report_df["depo_missing"].sum())

    lines = [
        "# Idle Container Detection -- Summary",
        "",
        f"> **NOTE:** {SYNTHETIC_DATA_NOTICE}",
        "",
        f"**Total idle containers detected:** {len(report_df)}",
        "",
        "## By idle category",
        "",
        "| Category | Count |",
        "|---|---|",
    ]
    for cat, count in by_category.items():
        lines.append(f"| {cat} | {count} |")

    lines += ["", "## By severity tier (reporting only -- not the detection trigger)", "", "| Tier | Count |", "|---|---|"]
    for tier, count in by_severity.items():
        lines.append(f"| {tier} | {count} |")

    lines += ["", "## By email status", "", "| Status | Count |", "|---|---|"]
    for status, count in by_status.items():
        lines.append(f"| {status} | {count} |")

    lines += [
        "",
        "## Data gaps",
        "",
        f"- Containers with missing `Depo` (flagged, not blocked): **{depo_gaps}**",
        "",
    ]
    return "\n".join(lines)


def run_reminder_lifecycle_demo(report_df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Steps the reminder engine through 6 simulated fortnight-spanning dates
    (cadence=3d, escalation at 3 reminders => day9) so reviewers can watch
    two example containers travel the full lifecycle:
      - container A: no commitment -> R1 -> R2 -> R3 -> ESCALATED
      - container B: commitment logged after R1, suppresses R2/R3 until
        the commitment date passes, then resumes and eventually escalates
    All other idle containers are processed in the same runs (realistic
    batch behaviour) but are not singled out in the narrative.
    """
    store = ReminderStateStore(STATE_FILE)
    store.reset()  # POC re-run should start the simulated clock fresh each time

    all_idle = report_df["CONTAINERNO"].tolist()
    if len(all_idle) < 2:
        raise RuntimeError("Need at least 2 idle containers to run the lifecycle demo")

    container_a = all_idle[0]
    container_b = all_idle[1]

    run_offsets = [0, 3, 6, 9, 12, 15]
    all_events = []

    for offset in run_offsets:
        run_date = (SIM_START_DATE + timedelta(days=offset)).isoformat()
        events = process_run(all_idle, run_date, config, store)
        all_events.extend(events)

        # After the first run, container B's ops team logs a commitment
        # to resolve by day9 -- this should suppress its next two runs.
        if offset == 0:
            store.register_commitment(container_b, (SIM_START_DATE + timedelta(days=9)).isoformat())

        store.save()

    log_df = pd.DataFrame(all_events)
    log_df["demo_role"] = log_df["container_no"].apply(
        lambda c: "DEMO: no-commitment lifecycle" if c == container_a
        else ("DEMO: commitment lifecycle" if c == container_b else "")
    )
    return log_df


def write_reminder_log_outputs(log_df: pd.DataFrame):
    log_df.to_csv(OUTPUT_DIR / "reminder_escalation_log.csv", index=False)

    demo_rows = log_df[log_df["demo_role"] != ""]
    lines = [
        "# Reminder & Escalation Simulation Log",
        "",
        "Simulated via `simulated_run_date` stepping -- no real time passed, "
        "no real emails were sent.",
        "",
        "## Highlighted lifecycles",
        "",
    ]
    for role in demo_rows["demo_role"].unique():
        sub = demo_rows[demo_rows["demo_role"] == role]
        container_no = sub["container_no"].iloc[0]
        lines.append(f"### {role} -- container `{container_no}`")
        lines.append("")
        lines.append("| Run Date | Event | Detail |")
        lines.append("|---|---|---|")
        for _, r in sub.iterrows():
            lines.append(f"| {r['run_date']} | {r['event']} | {r['detail']} |")
        lines.append("")

    (OUTPUT_DIR / "reminder_escalation_log.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    config = load_config()

    report_df = build_idle_and_email_report(config)
    write_report_outputs(report_df)

    summary_md = build_summary(report_df)
    (OUTPUT_DIR / "summary.md").write_text(summary_md, encoding="utf-8")

    log_df = run_reminder_lifecycle_demo(report_df, config)
    write_reminder_log_outputs(log_df)

    print(summary_md)
    print()
    print(f"Wrote {len(report_df)} idle-container rows to output/idle_container_report.xlsx (+csv)")
    print(f"Wrote {len(log_df)} reminder-engine events to output/reminder_escalation_log.csv (+md)")


if __name__ == "__main__":
    main()
