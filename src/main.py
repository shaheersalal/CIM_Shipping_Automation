"""
Orchestrator for the Container Aging & Export Reminder Automation POC (v2).

Wires together four independent systems (kept separate on purpose, since
they become separate real services later):
    1. port_config_store -- Setup/Configuration table (Port -> Free Days,
                             Trade Person/Head/Director emails)
    2. rule_engine        -- idle detection + severity tiering, resolved
                             per-port via port_config_store
    3. email_templates     -- confidence-scored email template filling
    4. reminder_engine    -- reminder/escalation state machine (Head then
                             Director), driven by a simulated clock

Run with:  python -m src.main
(from the emkay/ project root, with the xlsx files alongside it)

IMPORTANT: The Setup/Configuration table is seeded from a SAMPLE
Storage_slab.xlsx covering a fraction of the real ~144 ports in the
activity data, with placeholder contact emails. Nothing here should be
read as the client's real configuration -- that flag is carried through
to every output file. See src/rule_engine.SAMPLE_CONFIG_NOTICE.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config
from src.port_config_store import seed_from_storage_slab, resolve_config, CONFIG_COLUMNS
from src.rule_engine import classify_idle, build_port_summary, SAMPLE_CONFIG_NOTICE
from src.email_templates import build_email_report
from src.reminder_engine import ReminderStateStore, process_run

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_XLSX = PROJECT_ROOT / "Activities_Demo_data.xlsx"
STORAGE_SLAB_XLSX = PROJECT_ROOT / "Storage_slab.xlsx"
OUTPUT_DIR = PROJECT_ROOT / "output"
STATE_FILE = OUTPUT_DIR / "reminder_state.json"

# Arbitrary fixed anchor date for the POC's simulated clock. Real detection
# dates in the source data vary wildly (some `days` values exceed 3000),
# so the reminder demo uses its own clock purely to show the state
# machine advancing -- it is not derived from Activity Date.
SIM_START_DATE = date(2026, 7, 20)


def build_reports(config: dict):
    activities_df = pd.read_excel(INPUT_XLSX)
    port_config_df = seed_from_storage_slab(STORAGE_SLAB_XLSX)[CONFIG_COLUMNS].copy()

    idle_df, unconfigured_df = classify_idle(activities_df, port_config_df, config)
    report_df = build_email_report(idle_df, config)
    port_summary_df = build_port_summary(activities_df, idle_df, unconfigured_df, port_config_df)
    return report_df, unconfigured_df, port_summary_df, port_config_df


def write_report_outputs(report_df: pd.DataFrame, unconfigured_df: pd.DataFrame, port_summary_df: pd.DataFrame):
    cols = [
        "CONTAINERNO", "PORT", "SIZE", "TYPE", "REGION_NAME", "Country",
        "Depo", "idle_category", "days", "free_days", "days_overdue",
        "severity_tier", "depo_missing", "region_missing", "country_missing",
        "confidence_score", "review_reasons", "email_status",
        "trade_person_email", "email_text",
    ]
    out = report_df[cols]
    out.to_excel(OUTPUT_DIR / "idle_container_report.xlsx", index=False)
    out.to_csv(OUTPUT_DIR / "idle_container_report.csv", index=False)

    uc_cols = ["CONTAINERNO", "PORT", "idle_category", "Mode", "ACTIVITY", "days", "REGION_NAME", "Country"]
    unconfigured_df[uc_cols].to_csv(OUTPUT_DIR / "unconfigured_ports_report.csv", index=False)

    port_summary_df.to_csv(OUTPUT_DIR / "port_summary.csv", index=False)


def build_summary(report_df: pd.DataFrame, unconfigured_df: pd.DataFrame, port_summary_df: pd.DataFrame) -> str:
    by_category = report_df["idle_category"].value_counts()
    by_severity = report_df["severity_tier"].value_counts()
    by_status = report_df["email_status"].value_counts()
    configured_ports = int(port_summary_df["Configured"].sum())
    total_ports = len(port_summary_df)

    lines = [
        "# Idle Container Detection -- Summary (v2, port-centric)",
        "",
        f"> **NOTE:** {SAMPLE_CONFIG_NOTICE}",
        "",
        f"**Total idle containers detected:** {len(report_df)}",
        f"**Ports configured:** {configured_ports} / {total_ports}",
        f"**Containers at unconfigured ports (idle status unknown):** {len(unconfigured_df)}",
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

    lines += ["", "## Top ports by idle count", "", "| Port | Configured | Idle Containers |", "|---|---|---|"]
    for _, row in port_summary_df.head(10).iterrows():
        lines.append(f"| {row['PORT']} | {'Yes' if row['Configured'] else 'No'} | {row['Idle Containers']} |")

    lines.append("")
    return "\n".join(lines)


def run_reminder_lifecycle_demo(report_df: pd.DataFrame, port_config_df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Steps the reminder engine through 6 simulated dates (cadence=3d,
    escalation trigger=3) so reviewers can watch two example containers
    travel the full lifecycle:
      - container A: no commitment -> R1 -> R2 -> R3 -> HEAD -> DIRECTOR
      - container B: commitment logged after R1, suppresses R2/R3 until
        the commitment date passes, then resumes and eventually escalates
    Escalation targets (Head/Director) are resolved per port -- not global
    dummy values.
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

        if offset == 0:
            store.register_commitment(container_b, (SIM_START_DATE + timedelta(days=9)).isoformat())

        store.save()

    log_df = pd.DataFrame(all_events)
    log_df["demo_role"] = log_df["container_no"].apply(
        lambda c: "DEMO: no-commitment lifecycle" if c == container_a
        else ("DEMO: commitment lifecycle" if c == container_b else "")
    )

    port_by_container = report_df.set_index("CONTAINERNO")["PORT"].to_dict()
    activity_by_container = report_df.set_index("CONTAINERNO")["ACTIVITY"].to_dict() if "ACTIVITY" in report_df.columns else {}

    def recipient_for(row):
        resolved = resolve_config(port_config_df, port_by_container.get(row["container_no"]), activity_by_container.get(row["container_no"]))
        if not resolved:
            return ""
        if row["event"].startswith("REMINDER"):
            return resolved["trade_person_email"] or ""
        if row["event"] == "ESCALATED_TO_HEAD":
            return resolved["head_email"] or ""
        if row["event"] == "ESCALATED_TO_DIRECTOR":
            return resolved["director_email"] or ""
        return ""

    log_df["recipient"] = log_df.apply(recipient_for, axis=1)
    return log_df


def write_reminder_log_outputs(log_df: pd.DataFrame):
    log_df.to_csv(OUTPUT_DIR / "reminder_escalation_log.csv", index=False)

    demo_rows = log_df[log_df["demo_role"] != ""]
    lines = [
        "# Reminder & Escalation Simulation Log (v2)",
        "",
        "Simulated via `simulated_run_date` stepping -- no real time passed, "
        "no real emails were sent. Escalation targets resolved per-port from "
        "the Setup & Configuration table.",
        "",
        "## Highlighted lifecycles",
        "",
    ]
    for role in demo_rows["demo_role"].unique():
        sub = demo_rows[demo_rows["demo_role"] == role]
        container_no = sub["container_no"].iloc[0]
        lines.append(f"### {role} -- container `{container_no}`")
        lines.append("")
        lines.append("| Run Date | Event | Recipient | Detail |")
        lines.append("|---|---|---|---|")
        for _, r in sub.iterrows():
            lines.append(f"| {r['run_date']} | {r['event']} | {r['recipient']} | {r['detail']} |")
        lines.append("")

    (OUTPUT_DIR / "reminder_escalation_log.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    config = load_config()

    report_df, unconfigured_df, port_summary_df, port_config_df = build_reports(config)
    write_report_outputs(report_df, unconfigured_df, port_summary_df)

    summary_md = build_summary(report_df, unconfigured_df, port_summary_df)
    (OUTPUT_DIR / "summary.md").write_text(summary_md, encoding="utf-8")

    log_df = run_reminder_lifecycle_demo(report_df, port_config_df, config)
    write_reminder_log_outputs(log_df)

    print(summary_md)
    print()
    print(f"Wrote {len(report_df)} idle-container rows to output/idle_container_report.xlsx (+csv)")
    print(f"Wrote {len(unconfigured_df)} unconfigured-port rows to output/unconfigured_ports_report.csv")
    print(f"Wrote {len(port_summary_df)} port summary rows to output/port_summary.csv")
    print(f"Wrote {len(log_df)} reminder-engine events to output/reminder_escalation_log.csv (+md)")


if __name__ == "__main__":
    main()
