"""
Rule engine: idle detection and severity tiering.

This module is intentionally the ONLY place that decides whether a
container is idle. It knows nothing about email templates, confidence
scoring, or reminder/escalation state -- those live in email_templates.py
and reminder_engine.py respectively.

Detection rule (per row): a container is idle when
    days > Agreed_Free_Days
evaluated per-container against that container's own agreed value.
There is deliberately NO fixed global day threshold anywhere below.

NOTE: Agreed_Free_Days is ASSUMED SYNTHETIC DATA for this POC (see the
Agreed_Days_Source column in the source workbook). Every output this
module produces carries that flag forward -- it must never be presented
as a real client business rule.
"""
import pandas as pd

# Modes explicitly excluded from idle detection, regardless of `days`.
EXCLUDED_MODES = {
    "Lease Rental Out",
    "SWAPING",
    "PROBLEM UNITS",
    "PROBLEM UNITS (DCHE)",
    "DISCHARGE FULL TRANSHIPMENT",  # in-transit, not client-controlled
}

# Mode -> idle category mapping. Any Mode not listed here and not in
# EXCLUDED_MODES is left unclassified (falls out of idle detection).
MODE_TO_CATEGORY = {
    "EMPTY": "empty_at_depot",
    "IMPORT FULL": "import_not_picked",
    "EXPORT FULL": "export_not_shipped",
}

SYNTHETIC_DATA_NOTICE = (
    "ASSUMED SYNTHETIC DATA -- Agreed_Free_Days is a POC placeholder, "
    "not the client's real per-container agreed value (pending parsing "
    "of email correspondence / ClimaxSuite)."
)


def _severity_tier(days_overdue: int, tiers_cfg: dict) -> str:
    """Cosmetic/reporting bucket only -- never used as a detection trigger."""
    just_lo, just_hi = tiers_cfg["just_over"]
    mod_lo, mod_hi = tiers_cfg["moderate"]
    sev_lo, _ = tiers_cfg["severe"]

    if just_lo <= days_overdue <= just_hi:
        return "Just Over"
    if mod_lo <= days_overdue <= mod_hi:
        return "Moderate"
    if days_overdue >= sev_lo:
        return "Severe"
    return "Unclassified"  # defensive; should not occur for idle rows


def classify_idle(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Returns a new DataFrame containing only idle containers, with columns:
        idle_category, days_overdue, severity_tier, depo_missing,
        region_missing, country_missing, agreed_days_is_synthetic
    added. Non-idle and explicitly-excluded rows are dropped.
    """
    working = df.copy()

    working["idle_category"] = working["Mode"].map(MODE_TO_CATEGORY)
    eligible = working[
        working["idle_category"].notna() & ~working["Mode"].isin(EXCLUDED_MODES)
    ].copy()

    eligible["days_overdue"] = eligible["days"] - eligible["Agreed_Free_Days"]
    idle = eligible[eligible["days_overdue"] > 0].copy()

    tiers_cfg = config["severity_tiers"]
    idle["severity_tier"] = idle["days_overdue"].apply(_severity_tier, tiers_cfg=tiers_cfg)

    idle["depo_missing"] = idle["Depo"].isna() | (idle["Depo"].astype(str).str.strip() == "")
    idle["region_missing"] = idle["REGION_NAME"].isna()
    idle["country_missing"] = idle["Country"].isna()
    idle["agreed_days_is_synthetic"] = True
    idle["agreed_days_source_note"] = SYNTHETIC_DATA_NOTICE

    idle = idle.sort_values("days_overdue", ascending=False).reset_index(drop=True)
    return idle
