"""
Rule engine: idle detection and severity tiering.

v2 change: free days are resolved PER CONTAINER via its Port (optionally
Port + Activity Type) against the Setup/Configuration table
(port_config_store.py) -- there is no per-row synthetic value anymore, and
still no fixed global day threshold anywhere below.

Containers whose port has no usable configuration row at all are NOT
silently dropped -- they are returned separately as `unconfigured_df` so
the coverage gap is visible rather than hidden.
"""
import pandas as pd

from src.port_config_store import resolve_config

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

SAMPLE_CONFIG_NOTICE = (
    "SAMPLE / INCOMPLETE DATA -- the Port Setup & Configuration table is "
    "seeded from a sample Storage_slab.xlsx covering a fraction of the "
    "client's real ports. Trade Person / Head / Director emails are POC "
    "placeholders, not real contacts."
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


def classify_idle(df: pd.DataFrame, port_config_df: pd.DataFrame, config: dict):
    """
    Returns (idle_df, unconfigured_df):
      idle_df         -- one row per idle container at a CONFIGURED port,
                         with free_days/days_overdue/severity_tier/
                         trade_person_email/head_email/director_email/
                         config_match_type columns added.
      unconfigured_df -- containers that would have been in scope for idle
                         detection (right Mode, not excluded) but whose
                         port has no usable Free Days rule at all.
    """
    working = df.copy()

    working["idle_category"] = working["Mode"].map(MODE_TO_CATEGORY)
    eligible = working[
        working["idle_category"].notna() & ~working["Mode"].isin(EXCLUDED_MODES)
    ].copy()

    resolved = eligible.apply(
        lambda r: resolve_config(port_config_df, r["PORT"], r["ACTIVITY"]), axis=1
    )
    eligible["_resolved"] = resolved
    eligible["config_match_type"] = eligible["_resolved"].apply(
        lambda r: r["match_type"] if r else "unconfigured"
    )

    unconfigured_df = eligible[eligible["config_match_type"] == "unconfigured"].drop(columns=["_resolved"]).copy()
    configured = eligible[eligible["config_match_type"] != "unconfigured"].copy()

    configured["free_days"] = configured["_resolved"].apply(lambda r: r["free_days"])
    configured["trade_person_email"] = configured["_resolved"].apply(lambda r: r["trade_person_email"])
    configured["head_email"] = configured["_resolved"].apply(lambda r: r["head_email"])
    configured["director_email"] = configured["_resolved"].apply(lambda r: r["director_email"])
    configured = configured.drop(columns=["_resolved"])

    configured["days_overdue"] = configured["days"] - configured["free_days"]
    idle_df = configured[configured["days_overdue"] > 0].copy()

    tiers_cfg = config["severity_tiers"]
    idle_df["severity_tier"] = idle_df["days_overdue"].apply(_severity_tier, tiers_cfg=tiers_cfg)

    idle_df["depo_missing"] = idle_df["Depo"].isna() | (idle_df["Depo"].astype(str).str.strip() == "")
    idle_df["region_missing"] = idle_df["REGION_NAME"].isna()
    idle_df["country_missing"] = idle_df["Country"].isna()
    idle_df["trade_email_missing"] = idle_df["trade_person_email"].isna() | (idle_df["trade_person_email"].astype(str).str.strip() == "")

    idle_df = idle_df.sort_values("days_overdue", ascending=False).reset_index(drop=True)
    return idle_df, unconfigured_df.reset_index(drop=True)


def build_port_summary(activities_df: pd.DataFrame, idle_df: pd.DataFrame, unconfigured_df: pd.DataFrame, port_config_df: pd.DataFrame) -> pd.DataFrame:
    """
    Port-first rollup for the Dashboard's primary view: one row per port
    that appears anywhere in the activity data, with idle counts,
    configuration status, and the resolved Trade Person for that port.
    """
    from src.port_config_store import is_port_configured, resolve_config

    all_ports = sorted(activities_df["PORT"].dropna().unique())
    rows = []
    for port in all_ports:
        port_idle = idle_df[idle_df["PORT"] == port]
        port_unconfigured = unconfigured_df[unconfigured_df["PORT"] == port]
        total_containers = len(activities_df[activities_df["PORT"] == port])
        configured = is_port_configured(port_config_df, port)

        trade_person = None
        if configured and not port_idle.empty:
            trade_person = port_idle["trade_person_email"].iloc[0]
        elif configured:
            resolved = resolve_config(port_config_df, port, None)
            trade_person = resolved["trade_person_email"] if resolved else None

        rows.append({
            "PORT": port,
            "Configured": configured,
            "Total Containers": total_containers,
            "Idle Containers": len(port_idle),
            "Eligible But Unconfigured": len(port_unconfigured),
            "Trade Person Email": trade_person or "",
        })

    summary = pd.DataFrame(rows)
    return summary.sort_values(["Idle Containers", "Total Containers"], ascending=False).reset_index(drop=True)
