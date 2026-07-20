"""
Loads the POC configuration layer (config/config.json).

Kept isolated so the rule engine, email/confidence module, and reminder
engine never hardcode thresholds, cadences, or contact placeholders --
all of that comes from this one file, which the client can swap out
later without touching logic code.
"""
import json
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.json"


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
