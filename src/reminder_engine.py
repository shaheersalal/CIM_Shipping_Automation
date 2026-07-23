"""
Reminder & escalation state engine.

This module only tracks state transitions over simulated time -- it does
not decide idleness (rule_engine.py), does not resolve Trade Person/Head/
Director emails (port_config_store.py), and does not generate email text
(email_templates.py). It is deliberately stateful and driven by a
simulated clock (`simulated_run_date`) so a POC reviewer can step through
multiple "days" and watch counters advance without needing real time.

v2 change: escalation is now two-tier per the client's requirement doc
(§4.3/4.4) -- after `escalation_trigger_count` unresolved reminders,
escalate to that port's Head; if still unresolved after one more cadence
period, escalate further to that port's Director. (The wait-before-Director
period reuses the same cadence -- not separately specified by the client;
flagged as an assumption in the UI.) This module only emits the stage
transition events -- the caller resolves *which* Head/Director email that
means by looking up the container's port in the Setup/Configuration table.

State per container:
    reminder_count       -- how many reminders have fired so far (0-3)
    last_reminder_date    -- date of the most recent reminder (or None)
    escalation_stage      -- "none" | "head" | "director"
    last_escalation_date  -- date the current escalation_stage was entered
    commitment_date        -- optional date.isoformat(); while run_date is
                              before this, reminders/escalation are suppressed

Events emitted per process_run() call, one per container evaluated:
    REMINDER_1 / REMINDER_2 / REMINDER_3 / ESCALATED_TO_HEAD /
    ESCALATED_TO_DIRECTOR / SUPPRESSED_BY_COMMITMENT /
    NO_ACTION (waiting on cadence, or already at Director -- final state)
"""
import json
from dataclasses import dataclass, asdict
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
    escalation_stage: str = "none"
    last_escalation_date: str | None = None
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
        # a fresh commitment reopens even already-escalated cases
        state.escalation_stage = "none"
        state.last_escalation_date = None

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

        if state.escalation_stage == "director":
            events.append({
                "container_no": container_no,
                "run_date": run_date,
                "event": "NO_ACTION",
                "detail": "Already escalated to Director, awaiting manual resolution",
            })
            continue

        if state.escalation_stage == "head":
            days_since_escalation = (run_dt - _to_date(state.last_escalation_date)).days
            if days_since_escalation >= cadence:
                state.escalation_stage = "director"
                state.last_escalation_date = run_date
                events.append({
                    "container_no": container_no,
                    "run_date": run_date,
                    "event": "ESCALATED_TO_DIRECTOR",
                    "detail": f"No resolution {days_since_escalation}d after Head escalation -- escalated to Director",
                })
            else:
                events.append({
                    "container_no": container_no,
                    "run_date": run_date,
                    "event": "NO_ACTION",
                    "detail": f"Only {days_since_escalation}d since Head escalation, cadence is {cadence}d",
                })
            continue

        # escalation_stage == "none" -- normal reminder flow
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
            state.escalation_stage = "head"
            state.last_escalation_date = run_date
            events.append({
                "container_no": container_no,
                "run_date": run_date,
                "event": "ESCALATED_TO_HEAD",
                "detail": f"No resolution after {escalation_trigger} reminders -- escalated to Head",
            })

    return events
