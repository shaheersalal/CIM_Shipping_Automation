"""
Port / Activity-Type Setup & Configuration store (client requirement doc §4.1).

This is the v2 replacement for v1's synthetic per-container Agreed_Free_Days
column. Free days are now looked up **by Port** (optionally Port + Activity
Type), from a small reference table the client maintains -- seeded here from
a sample Storage_slab.xlsx covering a fraction of the real ~144 ports.

Schema (exactly the 6 fields from the client's requirement doc):
    Port, Activity Type, Free Days, Trade Person Email, Head Email,
    Director Email

A blank Activity Type means "port-level default" -- it matches any
container at that port whose specific Activity Type has no dedicated row.
A port with NO row at all (default or specific) is "unconfigured" and must
be surfaced, never silently skipped or defaulted.
"""
import re
from typing import Optional

import pandas as pd

CONFIG_COLUMNS = [
    "Port", "Activity Type", "Free Days",
    "Trade Person Email", "Head Email", "Director Email",
]

# Context-only columns carried from the seed source, shown for transparency
# but not part of the client's official 6-field schema.
SEED_CONTEXT_COLUMNS = ["Region", "Seed Source Note"]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower()) or "unknown"


def _normalize(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip().upper()


def parse_free_days(raw) -> Optional[int]:
    """
    Storage_slab free-days values are messy real-world text: '120', 'NO',
    '2-5 days', '90 days', '-', '80 Days Hakika', '100 + days', '45/60'.
    Extracts the first integer found; returns None (unusable/unconfigured)
    when there is no digit to extract at all (e.g. 'NO', '-', blank).
    """
    if pd.isna(raw):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    match = re.search(r"\d+", str(raw))
    return int(match.group()) if match else None


def seed_from_storage_slab(path: str) -> pd.DataFrame:
    """
    Parses the sample Storage_slab.xlsx (Region, Agent/Country, Port, Free
    Days, 20', 40' -- header on the 2nd row) into the client's 6-field
    schema. Trade Person / Head / Director emails are NOT in the source
    file -- these are POC placeholders, clearly not real contacts.
    Rows where free days can't be parsed to a number are kept (for
    transparency) but will not resolve during idle detection.
    """
    raw = pd.read_excel(path, header=2)
    raw = raw.iloc[:, 1:]  # drop the leading blank/unnamed column
    raw.columns = ["Region", "Agent_Country", "Port", "Free_Days_Raw", "Rate20", "Rate40", "Notes"]
    raw = raw.dropna(subset=["Port"]).reset_index(drop=True)

    rows = []
    for _, r in raw.iterrows():
        port = str(r["Port"]).strip()
        region = str(r["Region"]).strip() if pd.notna(r["Region"]) else ""
        free_days = parse_free_days(r["Free_Days_Raw"])
        rows.append({
            "Port": port,
            "Activity Type": "",  # blank = port-level default; slab has no per-activity granularity
            "Free Days": free_days,
            "Trade Person Email": f"trade.{_slug(port)}@emkay-shipping.example",
            "Head Email": f"head.{_slug(region)}@emkay-shipping.example",
            "Director Email": "director@emkay-shipping.example",
            "Region": region,
            "Seed Source Note": (
                f"Seeded from Storage_slab.xlsx (raw value: {r['Free_Days_Raw']!r})"
                if free_days is not None else
                f"UNPARSEABLE free-days text in source ({r['Free_Days_Raw']!r}) -- treated as unconfigured until corrected"
            ),
        })

    return pd.DataFrame(rows, columns=CONFIG_COLUMNS + SEED_CONTEXT_COLUMNS)


def resolve_config(port_config_df: pd.DataFrame, port, activity_type) -> Optional[dict]:
    """
    Looks up free days + contacts for a container's (Port, Activity Type).
    Preference order:
      1. exact Port + Activity Type row with a parseable Free Days value
      2. Port-level default row (blank Activity Type) with a parseable value
      3. None -- caller must treat this container's port as unconfigured
    """
    if port_config_df.empty:
        return None

    df = port_config_df.copy()
    df["_port_norm"] = df["Port"].apply(_normalize)
    df["_activity_norm"] = df["Activity Type"].apply(_normalize)
    df["_free_days_parsed"] = df["Free Days"].apply(parse_free_days)

    port_norm = _normalize(port)
    activity_norm = _normalize(activity_type)

    usable = df[df["_free_days_parsed"].notna()]

    specific = usable[(usable["_port_norm"] == port_norm) & (usable["_activity_norm"] == activity_norm) & (usable["_activity_norm"] != "")]
    if not specific.empty:
        row = specific.iloc[0]
        match_type = "port+activity"
    else:
        default = usable[(usable["_port_norm"] == port_norm) & (usable["_activity_norm"] == "")]
        if default.empty:
            return None
        row = default.iloc[0]
        match_type = "port_default"

    return {
        "free_days": int(row["_free_days_parsed"]),
        "trade_person_email": row["Trade Person Email"] if pd.notna(row["Trade Person Email"]) and str(row["Trade Person Email"]).strip() else None,
        "head_email": row["Head Email"] if pd.notna(row["Head Email"]) and str(row["Head Email"]).strip() else None,
        "director_email": row["Director Email"] if pd.notna(row["Director Email"]) and str(row["Director Email"]).strip() else None,
        "match_type": match_type,
    }


def is_port_configured(port_config_df: pd.DataFrame, port) -> bool:
    """True if this port has at least one row with a usable Free Days value (default or specific)."""
    if port_config_df.empty:
        return False
    df = port_config_df.copy()
    df["_port_norm"] = df["Port"].apply(_normalize)
    df["_free_days_parsed"] = df["Free Days"].apply(parse_free_days)
    return bool(((df["_port_norm"] == _normalize(port)) & df["_free_days_parsed"].notna()).any())
