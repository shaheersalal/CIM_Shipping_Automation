"""
Reminder & escalation state engine.

This module only tracks state transitions over simulated time -- it does
not decide idleness (rule_engine.py) and does not generate email text
(email_templates.py). It is deliberately stateful and file-backed so a
POC reviewer can run the pipeline multiple times with different
`simulated_run_date` values and watch counters advance, without needing
real days to pass.

State per container:
    reminder_count      -- how many reminders have fired so far (0-3)
    last_reminder_date  -- date of the most recent reminder (or None)
    escalated           -- True once reminder_count has been exceeded
    commitment_date      -- optional date.isoformat(); while run_date is
                            before this, reminders are suppressed

Events emitted per process_run() call, one per container evaluated:
    REMINDER_1 / REMINDER_2 / REMINDER_3 / ESCALATED_TO_DIRECTOR /
    SUPPRESSED_BY_COMMITMENT / NO_ACTION (already escalated, nothing to do)
"""
import json
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path


def _to_date(value) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


@dataclass
class ContainerState:
    reminder_count: int = 0
    last_reminder_date: str | None = None
    escalated: bool = False
    commitment_date: str | None = None


class ReminderStateStore:
    """
    JSON-file-backed state so successive simulated runs accumulate.

    Pass `path=None` to run purely in memory (no disk I/O at all) -- used
    by the Streamlit app, where state lives in `st.session_state` for the
    duration of a browser session instead of a shared file on disk.
    """

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path is not None else None
        self._state: dict[str, ContainerState] = {}
        if self.path is not None and self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._state = {k: ContainerState(**v) for k, v in raw.items()}

    def get(self, container_no: str) -> ContainerState:
        return self._state.setdefault(container_no, ContainerState())

    def register_commitment(self, container_no: str, committed_date: str):
        state = self.get(container_no)
        state.commitment_date = committed_date
        state.escalated = False  # a fresh commitment reopens escalated cases too

    def save(self):
        if self.path is None:
            return
        raw = {k: asdict(v) for k, v in self._state.items()}
        self.path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    def reset(self):
        self._state = {}
        if self.path is not None and self.path.exists():
            self.path.unlink()


def process_run(
    container_numbers: list[str],
    run_date: str,
    config: dict,
    store: ReminderStateStore,
) -> list[dict]:
    """
    Evaluate every container in `container_numbers` (assumed still idle as
    of this simulated run) against its stored reminder state and emit one
    event per container. Mutates `store` in place; caller decides when to
    persist via store.save().
    """
    cadence = config["reminder_cadence_days"]
    escalation_trigger = config["escalation_trigger_count"]
    run_dt = _to_date(run_date)

    events = []
    for container_no in container_numbers:
        state = store.get(container_no)

        if state.commitment_date and run_dt < _to_date(state.commitment_date):
            events.append({
                "container_no": container_no,
                "run_date": run_date,
                "event": "SUPPRESSED_BY_COMMITMENT",
                "detail": f"Commitment date {state.commitment_date} not yet reached",
            })
            continue

        if state.commitment_date and run_dt >= _to_date(state.commitment_date):
            state.commitment_date = None  # commitment passed, resume normal logic

        if state.escalated:
            events.append({
                "container_no": container_no,
                "run_date": run_date,
                "event": "NO_ACTION",
                "detail": "Already escalated to director, awaiting manual resolution",
            })
            continue

        if state.last_reminder_date is None:
            state.reminder_count = 1
            state.last_reminder_date = run_date
            events.append({
                "container_no": container_no,
                "run_date": run_date,
                "event": "REMINDER_1",
                "detail": "First detection -- reminder #1 sent",
            })
            continue

        days_since_last = (run_dt - _to_date(state.last_reminder_date)).days
        if days_since_last < cadence:
            events.append({
                "container_no": container_no,
                "run_date": run_date,
                "event": "NO_ACTION",
                "detail": f"Only {days_since_last}d since last reminder, cadence is {cadence}d",
            })
            continue

        if state.reminder_count < escalation_trigger:
            state.reminder_count += 1
            state.last_reminder_date = run_date
            events.append({
                "container_no": container_no,
                "run_date": run_date,
                "event": f"REMINDER_{state.reminder_count}",
                "detail": f"Reminder #{state.reminder_count} sent",
            })
        else:
            state.escalated = True
            events.append({
                "container_no": container_no,
                "run_date": run_date,
                "event": "ESCALATED_TO_DIRECTOR",
                "detail": (
                    f"No resolution after {escalation_trigger} reminders -- "
                    f"escalated to {config['director_email']}"
                ),
            })

    return events
