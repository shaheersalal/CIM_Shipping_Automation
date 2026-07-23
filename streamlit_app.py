"""
Container Aging & Export Reminder Automation -- interactive POC demo (v2).

Port-centric redesign: free days are resolved per container via its Port
(optionally Port + Activity Type) against a live-editable Setup &
Configuration table (client requirement doc §4.1), not a synthetic
per-container value. This is a thin UI layer only -- all business logic
lives in src/ (rule_engine.py, email_templates.py, reminder_engine.py,
port_config_store.py) and is imported unchanged from the CLI version.
"""
import io
import json
import re
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from src.config_loader import load_config
from src.port_config_store import seed_from_storage_slab, resolve_config, parse_free_days, CONFIG_COLUMNS, SEED_CONTEXT_COLUMNS
from src.rule_engine import classify_idle, build_port_summary, SAMPLE_CONFIG_NOTICE
from src.email_templates import build_email_report
from src.reminder_engine import ReminderStateStore, process_run

SIM_START_DATE = date(2026, 7, 20)
DEFAULT_XLSX = "Activities_Demo_data.xlsx"
STORAGE_SLAB_PATH = "Storage_slab.xlsx"

st.set_page_config(
    page_title="Container Aging & Reminder Automation -- POC",
    page_icon="🚢",
    layout="wide",
)

if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False


# ---------------------------------------------------------------- helpers --

def inject_theme_css(dark: bool):
    """
    Streamlit's own theme picker lives one click deep in its hamburger menu
    and only offers Light/Dark/System -- this forces the same look via a
    single visible button instead, by overriding the CSS variables the
    default themes already use.
    """
    if dark:
        bg, secondary_bg, text, border = "#0e1117", "#262730", "#fafafa", "#41434c"
    else:
        bg, secondary_bg, text, border = "#ffffff", "#f0f2f6", "#31333F", "#d5d6d8"

    st.markdown(
        f"""
        <style>
        [data-testid="stAppViewContainer"], [data-testid="stHeader"] {{
            background-color: {bg};
        }}
        [data-testid="stAppViewContainer"] {{
            color: {text};
        }}
        [data-testid="stSidebar"] {{
            background-color: {secondary_bg};
        }}
        [data-testid="stSidebar"] * {{
            color: {text} !important;
        }}
        [data-testid="stMetricValue"], [data-testid="stMetricLabel"], [data-testid="stMetricDelta"] {{
            color: {text} !important;
        }}
        .stTabs [data-baseweb="tab-list"] {{
            background-color: {secondary_bg};
            border-radius: 6px;
        }}
        .stTabs [data-baseweb="tab"] p {{
            color: {text};
        }}
        div[data-testid="stExpander"] {{
            background-color: {secondary_bg};
            border: 1px solid {border};
            border-radius: 6px;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# Native st.warning/error/info/success color themselves from Streamlit's
# real active theme, which our forced toggle above doesn't touch -- causing
# unreadable text in whichever mode diverges from the real theme. These
# self-styled versions use our own explicit colors, guaranteeing contrast.
ALERT_PALETTE = {
    "warning": {"light": ("#fff3cd", "#664d03", "#ffe69c"), "dark": ("#3d3212", "#ffe69c", "#6b5615")},
    "error":   {"light": ("#f8d7da", "#58151c", "#f1aeb5"), "dark": ("#2c0b0e", "#f1aeb5", "#842029")},
    "success": {"light": ("#d1e7dd", "#0a3622", "#a3cfbb"), "dark": ("#051b11", "#a3cfbb", "#146c43")},
    "info":    {"light": ("#cff4fc", "#055160", "#9eeaf9"), "dark": ("#032830", "#9eeaf9", "#087990")},
}
ALERT_ICONS = {"warning": "⚠️", "error": "🚫", "success": "✅", "info": "ℹ️"}


def themed_alert(kind: str, message_html: str):
    mode = "dark" if st.session_state.dark_mode else "light"
    bg, text, border = ALERT_PALETTE[kind][mode]
    st.markdown(
        f"""
        <div style="background-color:{bg}; color:{text}; border:1px solid {border};
                    border-radius:8px; padding:0.75rem 1rem; margin-bottom:1rem; font-size:0.95rem;">
            {ALERT_ICONS[kind]} {message_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner="Loading activity data...")
def load_activities(file_bytes: bytes) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(file_bytes))


@st.cache_data(show_spinner=False)
def load_seed_config(path: str) -> pd.DataFrame:
    return seed_from_storage_slab(path)


def init_session_state(report_df: pd.DataFrame):
    if "sim_store" not in st.session_state:
        st.session_state.sim_store = ReminderStateStore(path=None)
        st.session_state.sim_date = SIM_START_DATE
        st.session_state.sim_log = []
        idle_list = report_df["CONTAINERNO"].tolist()
        st.session_state.spotlight_a = idle_list[0] if idle_list else None
        st.session_state.spotlight_b = idle_list[1] if len(idle_list) > 1 else None


def reset_simulation(report_df: pd.DataFrame):
    st.session_state.sim_store = ReminderStateStore(path=None)
    st.session_state.sim_date = SIM_START_DATE
    st.session_state.sim_log = []


def run_one_step(container_numbers: list[str], config: dict, advance_days: int):
    st.session_state.sim_date = st.session_state.sim_date + timedelta(days=advance_days)
    events = process_run(
        container_numbers,
        st.session_state.sim_date.isoformat(),
        config,
        st.session_state.sim_store,
    )
    st.session_state.sim_log.extend(events)


def play_full_demo(container_numbers: list[str], config: dict):
    st.session_state.sim_store = ReminderStateStore(path=None)
    st.session_state.sim_date = SIM_START_DATE
    st.session_state.sim_log = []

    offsets = [0, 3, 6, 9, 12, 15]
    for i, offset in enumerate(offsets):
        st.session_state.sim_date = SIM_START_DATE + timedelta(days=offset)
        events = process_run(
            container_numbers,
            st.session_state.sim_date.isoformat(),
            config,
            st.session_state.sim_store,
        )
        st.session_state.sim_log.extend(events)
        if i == 0 and st.session_state.spotlight_b:
            st.session_state.sim_store.register_commitment(
                st.session_state.spotlight_b,
                (SIM_START_DATE + timedelta(days=9)).isoformat(),
            )


def counts_df(series: pd.Series, label: str) -> pd.DataFrame:
    vc = series.value_counts().reset_index()
    vc.columns = [label, "Count"]
    return vc


# "Chat box" quick-entry for the Setup form (client's requested UX: minimal
# guidance, every field skippable, fill any field in any order). Understands
# an explicit "field: value" prefix for precision, with freeform fallbacks
# for a lazier one-shot reply.
QUICKADD_PREFIXES = {
    "port": "Port", "location": "Port",
    "activity": "Activity Type", "activity type": "Activity Type",
    "free days": "Free Days", "days": "Free Days", "freedays": "Free Days",
    "trade": "Trade Person Email", "trade email": "Trade Person Email", "trade person email": "Trade Person Email",
    "head": "Head Email", "head email": "Head Email",
    "director": "Director Email", "director email": "Director Email",
}


def _quickadd_still_missing(draft: dict) -> str:
    missing = [f for f in CONFIG_COLUMNS if not draft.get(f)]
    if not missing:
        return "That's everything -- hit **Save** below whenever you like."
    return f"Still open: {', '.join(missing)} (all optional -- skip anything, or click Save as-is)."


def parse_quickadd_message(text: str, draft: dict) -> tuple[dict, str]:
    text = text.strip()
    if not text:
        return draft, "Type anything -- a port name, an email, free days -- or use the buttons below."

    if text.lower() in ("skip", "done", "save"):
        return draft, "Okay -- use the Save button below whenever you're ready."

    if ":" in text:
        prefix, _, value = text.partition(":")
        key = prefix.strip().lower()
        value = value.strip()
        if key in QUICKADD_PREFIXES and value:
            field = QUICKADD_PREFIXES[key]
            if field == "Free Days":
                parsed = parse_free_days(value)
                draft[field] = parsed
                if parsed is None:
                    return draft, f"Got '{value}' for Free Days but couldn't find a number in it -- try e.g. 'free days: 45'."
            else:
                draft[field] = value
            return draft, f"Got it -- {field}: {draft[field]}. {_quickadd_still_missing(draft)}"

    if "@" in text:
        draft["Trade Person Email"] = text
        return draft, (
            f"Assumed that's the Trade Person Email: {text}. If it was actually Head or Director, "
            f"reply 'head: {text}' or 'director: {text}' to correct it. {_quickadd_still_missing(draft)}"
        )

    if re.fullmatch(r"[\d\s\-/+]+(days?)?", text, flags=re.IGNORECASE):
        parsed = parse_free_days(text)
        if parsed is not None:
            draft["Free Days"] = parsed
            return draft, f"Got it -- Free Days: {parsed}. {_quickadd_still_missing(draft)}"

    if not draft.get("Port"):
        draft["Port"] = text
        return draft, f"Got it -- Port: {text}. {_quickadd_still_missing(draft)}"

    if not draft.get("Activity Type"):
        draft["Activity Type"] = text
        return draft, f"Got it -- Activity Type: {text}. {_quickadd_still_missing(draft)}"

    return draft, (
        "Not sure which field that's for -- try a prefix like 'port: ...', 'activity: ...', "
        "'free days: ...', 'trade: you@x.com', 'head: ...', 'director: ...', or just click Save below."
    )


def resolve_event_recipient(container_no: str, event: str, port_lookup: dict) -> str:
    contacts = port_lookup.get(container_no)
    if not contacts:
        return ""
    if event.startswith("REMINDER"):
        return contacts.get("trade_person_email") or ""
    if event == "ESCALATED_TO_HEAD":
        return contacts.get("head_email") or ""
    if event == "ESCALATED_TO_DIRECTOR":
        return contacts.get("director_email") or ""
    return ""


# ------------------------------------------------------------------ data --

top_l, top_r = st.columns([9, 1])
with top_l:
    st.title("🚢 Container Aging & Export Reminder Automation")
    st.caption("Proof of concept -- port-centric idle detection, confidence-scored email drafting, and simulated reminder/escalation lifecycle")
with top_r:
    st.write("")
    if st.button(
        "☀️ Light" if st.session_state.dark_mode else "🌙 Dark",
        help="Toggle dark/light mode",
        width="stretch",
    ):
        st.session_state.dark_mode = not st.session_state.dark_mode

inject_theme_css(st.session_state.dark_mode)
PLOT_TEMPLATE = "plotly_dark" if st.session_state.dark_mode else "plotly_white"

themed_alert(
    "warning",
    f"<strong>{SAMPLE_CONFIG_NOTICE}</strong>",
)

with st.sidebar:
    st.header("Data source")
    st.caption(f"✅ Loaded automatically: `{DEFAULT_XLSX}`")
    with open(DEFAULT_XLSX, "rb") as f:
        file_bytes = f.read()

    with st.expander("🔧 Advanced: use a different Excel file"):
        uploaded = st.file_uploader("Upload activity Excel", type=["xlsx"])
        if uploaded is not None:
            file_bytes = uploaded.getvalue()
            st.caption(f"Using uploaded file: {uploaded.name}")

    st.divider()
    st.header("⚙️ Live config")
    st.caption("Adjust and watch the report/dashboard update instantly.")
    base_config = load_config()
    confidence_threshold = st.slider(
        "Confidence threshold (READY vs NEEDS REVIEW)", 0, 100,
        base_config["confidence_threshold"],
    )
    reminder_cadence_days = st.number_input(
        "Reminder cadence (simulated days)", 1, 30, base_config["reminder_cadence_days"],
    )
    escalation_trigger_count = st.number_input(
        "Escalation trigger (# reminders before Head)", 1, 10, base_config["escalation_trigger_count"],
    )

config = dict(base_config)
config["confidence_threshold"] = confidence_threshold
config["reminder_cadence_days"] = int(reminder_cadence_days)
config["escalation_trigger_count"] = int(escalation_trigger_count)

activities_df = load_activities(file_bytes)

tab_dash, tab_report, tab_email, tab_sim, tab_setup = st.tabs(
    ["📊 Dashboard", "📋 Idle Containers", "✉️ Email Preview",
     "⏱️ Reminder Simulator", "⚙️ Setup & Configuration"]
)

# ---------------------------------------------------------- setup (first) --
# Written first in the file (even though it's the last visual tab) so the
# edited port_config_df is available below before classify_idle() runs.

with tab_setup:
    st.subheader("Port / Activity-Type Setup & Configuration")
    st.caption(
        "Per the client's requirement doc §4.1. This is a first-class, live-editable "
        "screen -- add, edit, or delete a port's configuration here and the whole "
        "app recomputes instantly. Leave **Activity Type** blank for a port-level "
        "default that applies whenever no more specific row matches."
    )

    seed_df = load_seed_config(STORAGE_SLAB_PATH)
    if "port_config_df" not in st.session_state:
        st.session_state.port_config_df = seed_df[CONFIG_COLUMNS].copy()

    if st.button("🔄 Reset to seed data (discard live edits)"):
        st.session_state.port_config_df = seed_df[CONFIG_COLUMNS].copy()

    st.markdown("#### 💬 Quick Add")
    st.caption(
        "Type it like a chat, in any order, skip anything -- e.g. 'LAEM CHABANG', then "
        "'free days: 45', then an email. Prefix with 'trade:'/'head:'/'director:' if a typed "
        "email isn't the Trade Person one."
    )

    if "quickadd_draft" not in st.session_state:
        st.session_state.quickadd_draft = {c: None for c in CONFIG_COLUMNS}
    if "quickadd_messages" not in st.session_state:
        st.session_state.quickadd_messages = []

    for msg in st.session_state.quickadd_messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    quickadd_input = st.chat_input("Type anything about this port's config...", key="quickadd_chat_input")
    if quickadd_input:
        st.session_state.quickadd_messages.append({"role": "user", "content": quickadd_input})
        updated_draft, reply = parse_quickadd_message(quickadd_input, st.session_state.quickadd_draft)
        st.session_state.quickadd_draft = updated_draft
        st.session_state.quickadd_messages.append({"role": "assistant", "content": reply})
        st.rerun()

    draft = st.session_state.quickadd_draft
    badge_cols = st.columns(6)
    for i, field in enumerate(CONFIG_COLUMNS):
        filled = bool(draft.get(field))
        with badge_cols[i]:
            st.markdown(f"{'✅' if filled else '⬜'} **{field}**")
            if filled:
                st.caption(str(draft[field]))

    qcol1, qcol2 = st.columns(2)
    if qcol1.button("💾 Save this port to the config table", width="stretch"):
        if not draft.get("Port"):
            themed_alert("error", "Need at least a Port name before saving.")
        else:
            new_row = {c: draft.get(c) for c in CONFIG_COLUMNS}
            st.session_state.port_config_df = pd.concat(
                [st.session_state.port_config_df, pd.DataFrame([new_row])], ignore_index=True
            )
            st.session_state.quickadd_draft = {c: None for c in CONFIG_COLUMNS}
            st.session_state.quickadd_messages = []
            themed_alert("success", f"Saved {new_row['Port']} to the config table below.")
    if qcol2.button("🔄 Discard and start over", width="stretch"):
        st.session_state.quickadd_draft = {c: None for c in CONFIG_COLUMNS}
        st.session_state.quickadd_messages = []

    st.divider()
    st.markdown("#### 📋 Full config table (bulk edit)")

    edited_config_df = st.data_editor(
        st.session_state.port_config_df,
        num_rows="dynamic",
        width="stretch",
        column_config={
            "Free Days": st.column_config.NumberColumn(
                min_value=0, step=1,
                help="Leave blank if not yet known -- the port will show as unconfigured until this is filled in.",
            ),
            "Activity Type": st.column_config.TextColumn(
                help="Leave blank for a port-level default applying to all activity types.",
            ),
        },
        key="port_config_editor",
    )
    st.session_state.port_config_df = edited_config_df
    port_config_df = edited_config_df

    all_ports_in_data = activities_df["PORT"].dropna().unique()
    configured_count = sum(
        resolve_config(port_config_df, p, None) is not None for p in all_ports_in_data
    )
    st.metric("Ports configured (usable Free Days rule)", f"{configured_count} / {len(all_ports_in_data)}")

    with st.expander("📄 Raw Storage_slab.xlsx seed reference (context only, not editable here)"):
        st.caption("Shows the original Region and unparsed Free-Days text this seed data came from.")
        st.dataframe(seed_df[["Port"] + SEED_CONTEXT_COLUMNS], width="stretch", hide_index=True)

    st.subheader("Open questions / assumptions in this POC")
    themed_alert(
        "info",
        "Whether <code>AGENT NAME</code> (from ClimaxSuite) and <strong>Trade Person Email</strong> "
        "(from this Setup table) refer to the same person or different roles is currently "
        "<strong>treated as different and unconfirmed</strong>.",
    )
    themed_alert(
        "info",
        "Whether Free Days genuinely varies by Activity Type within the same port, or is "
        "typically uniform, is currently <strong>built to support both</strong> -- defaulting "
        "to the port-level row when Activity Type isn't specified for a container.",
    )
    themed_alert(
        "info",
        "The seeded Setup/Configuration data (from <code>Storage_slab.xlsx</code>) covers a "
        f"small fraction ({configured_count} of {len(all_ports_in_data)}) of the real ports "
        "in the activity data, and is for <strong>demonstration only</strong>.",
    )
    themed_alert(
        "info",
        "Two-tier escalation (Head, then Director) waits one additional reminder-cadence "
        "period after the Head escalation before escalating further -- the client's document "
        "doesn't specify this wait exactly, so it currently <strong>reuses the same cadence</strong> "
        "as an assumption.",
    )
    st.subheader("Explicitly out of scope for this POC")
    st.markdown(
        "- No live ClimaxSuite connection\n"
        "- No live email/inbox parsing or extraction\n"
        "- No real email sending, no external API calls\n"
        "- No \"CRO-stage\" pre-depot idle category\n"
        "- No fixed global day-count thresholds anywhere -- always resolved via the Setup/Configuration table\n"
    )

# ------------------------------------------------------------- pipeline run --

idle_df, unconfigured_df = classify_idle(activities_df, port_config_df, config)
report_df = build_email_report(idle_df, config)
port_summary_df = build_port_summary(activities_df, idle_df, unconfigured_df, port_config_df)
init_session_state(report_df)

port_lookup = {
    row["CONTAINERNO"]: resolve_config(port_config_df, row["PORT"], row.get("ACTIVITY"))
    for _, row in report_df.iterrows()
}

# --------------------------------------------------------------- dashboard --

with tab_dash:
    total_idle = len(report_df)
    total_ports = len(port_summary_df)
    configured_ports = int(port_summary_df["Configured"].sum())
    unconfigured_eligible = int(port_summary_df["Eligible But Unconfigured"].sum())
    ready = int((report_df["email_status"] == "READY").sum()) if not report_df.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Idle containers detected", total_idle)
    c2.metric("Ports configured", f"{configured_ports} / {total_ports}")
    c3.metric("Containers at unconfigured ports", unconfigured_eligible)
    c4.metric("Emails READY to send", ready)

    st.subheader("Idle containers by Port (primary view)")
    display_summary = port_summary_df.copy()
    display_summary["Configured"] = display_summary["Configured"].map({True: "✅ Configured", False: "❌ Unconfigured"})
    st.dataframe(display_summary, width="stretch", height=350, hide_index=True)

    if not report_df.empty:
        col1, col2 = st.columns(2)
        with col1:
            by_cat = report_df["idle_category"].value_counts().reset_index()
            by_cat.columns = ["Category", "Count"]
            st.plotly_chart(px.bar(by_cat, x="Category", y="Count", title="Idle containers by category", template=PLOT_TEMPLATE), width="stretch")
        with col2:
            by_sev = report_df["severity_tier"].value_counts().reset_index()
            by_sev.columns = ["Severity", "Count"]
            st.plotly_chart(px.pie(by_sev, names="Severity", values="Count", title="By severity tier (reporting only)", template=PLOT_TEMPLATE), width="stretch")

    st.subheader("Drill into a port")
    selected_port = st.selectbox("Port", port_summary_df["PORT"].tolist())
    port_row = port_summary_df[port_summary_df["PORT"] == selected_port].iloc[0]

    if port_row["Configured"]:
        port_idle = report_df[report_df["PORT"] == selected_port]
        if port_idle.empty:
            themed_alert("success", f"No idle containers at <strong>{selected_port}</strong> right now -- all within free days.")
        else:
            themed_alert(
                "info",
                f"Trade Person for <strong>{selected_port}</strong>: {port_row['Trade Person Email']}",
            )
            bcol1, bcol2, bcol3 = st.columns(3)
            with bcol1:
                st.plotly_chart(px.bar(counts_df(port_idle["SIZE"].astype(str), "Size"), x="Size", y="Count", template=PLOT_TEMPLATE, title="By Size"), width="stretch")
            with bcol2:
                st.plotly_chart(px.bar(counts_df(port_idle["TYPE"], "Type"), x="Type", y="Count", template=PLOT_TEMPLATE, title="By Type"), width="stretch")
            with bcol3:
                st.plotly_chart(px.bar(counts_df(port_idle["CKind"], "CKind"), x="CKind", y="Count", template=PLOT_TEMPLATE, title="By CKind"), width="stretch")

            detail_cols = [
                "CONTAINERNO", "idle_category", "days", "free_days", "days_overdue",
                "severity_tier", "confidence_score", "email_status", "trade_person_email",
            ]
            st.dataframe(port_idle[detail_cols], width="stretch", height=300, hide_index=True)
    else:
        themed_alert(
            "error",
            f"<strong>{selected_port}</strong> has no free-days rule configured -- idle status cannot be "
            "determined. Add a row for this port in the Setup & Configuration tab.",
        )
        port_containers = activities_df[activities_df["PORT"] == selected_port]
        st.dataframe(
            port_containers[["CONTAINERNO", "Mode", "ACTIVITY", "days", "SIZE", "TYPE", "CKind"]],
            width="stretch", height=300, hide_index=True,
        )

# ------------------------------------------------------------------ report --

with tab_report:
    view = st.radio(
        "View",
        ["Idle containers (configured ports)", "Unconfigured ports (no free-days rule)"],
        horizontal=True,
    )

    if view == "Idle containers (configured ports)":
        fc1, fc2, fc3, fc4 = st.columns(4)
        cat_filter = fc1.multiselect("Category", sorted(report_df["idle_category"].unique()) if not report_df.empty else [])
        sev_filter = fc2.multiselect("Severity", sorted(report_df["severity_tier"].unique()) if not report_df.empty else [])
        status_filter = fc3.multiselect("Email status", sorted(report_df["email_status"].unique()) if not report_df.empty else [])
        search = fc4.text_input("Search container no.")

        filtered = report_df.copy()
        if cat_filter:
            filtered = filtered[filtered["idle_category"].isin(cat_filter)]
        if sev_filter:
            filtered = filtered[filtered["severity_tier"].isin(sev_filter)]
        if status_filter:
            filtered = filtered[filtered["email_status"].isin(status_filter)]
        if search:
            filtered = filtered[filtered["CONTAINERNO"].str.contains(search, case=False, na=False)]

        display_cols = [
            "CONTAINERNO", "PORT", "idle_category", "Depo", "days", "free_days",
            "days_overdue", "severity_tier", "confidence_score", "email_status",
            "trade_person_email", "review_reasons",
        ]
        st.dataframe(filtered[display_cols], width="stretch", height=500)
        st.caption(f"Showing {len(filtered)} of {len(report_df)} idle containers")

        csv_bytes = filtered[display_cols].to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Download filtered report (CSV)", csv_bytes, "idle_container_report.csv", "text/csv")
    else:
        themed_alert(
            "warning",
            f"{len(unconfigured_df)} containers are at ports with no usable free-days rule -- "
            "idle status is <strong>unknown</strong>, not \"not idle\".",
        )
        port_filter = st.multiselect("Port", sorted(unconfigured_df["PORT"].dropna().unique()) if not unconfigured_df.empty else [])
        uc_filtered = unconfigured_df.copy()
        if port_filter:
            uc_filtered = uc_filtered[uc_filtered["PORT"].isin(port_filter)]
        st.dataframe(
            uc_filtered[["CONTAINERNO", "PORT", "idle_category", "Mode", "ACTIVITY", "days", "REGION_NAME", "Country"]],
            width="stretch", height=500,
        )
        st.caption(f"Showing {len(uc_filtered)} of {len(unconfigured_df)} unconfigured-port containers")

# ------------------------------------------------------------------- email --

with tab_email:
    if report_df.empty:
        themed_alert("info", "No idle containers to preview.")
    else:
        selected = st.selectbox("Select container", report_df["CONTAINERNO"].tolist())
        row = report_df[report_df["CONTAINERNO"] == selected].iloc[0]

        badge = "🟢 READY" if row["email_status"] == "READY" else "🟠 NEEDS REVIEW"
        st.subheader(f"{selected} -- {badge}")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Category", row["idle_category"])
        m2.metric("Days overdue", int(row["days_overdue"]))
        m3.metric("Severity", row["severity_tier"])
        m4.metric("Confidence score", f"{row['confidence_score']}/100")

        if row["review_reasons"] != "none":
            themed_alert("error", f"Review reasons: {row['review_reasons']}")

        st.text_area("Filled email template", row["email_text"], height=240)

# ---------------------------------------------------------------- simulator --

with tab_sim:
    st.markdown(
        "Drives `src/reminder_engine.process_run` with a **simulated clock** -- "
        "no real time passes and no real emails are sent. Reminders route to each "
        "port's Trade Person; escalation routes to that port's Head, then Director."
    )

    idle_containers = report_df["CONTAINERNO"].tolist()

    bcol1, bcol2, bcol3 = st.columns(3)
    if bcol1.button("▶️ Play full 6-step demo lifecycle", width="stretch"):
        play_full_demo(idle_containers, config)
    if bcol2.button(f"⏭️ Advance {config['reminder_cadence_days']} simulated day(s)", width="stretch"):
        run_one_step(idle_containers, config, config["reminder_cadence_days"])
    if bcol3.button("🔄 Reset simulation", width="stretch"):
        reset_simulation(report_df)

    themed_alert("info", f"<strong>Simulated date:</strong> {st.session_state.sim_date.isoformat()}")

    with st.expander("📝 Log a commitment (suppresses reminders until the date passes)"):
        cc1, cc2, cc3 = st.columns([2, 2, 1])
        commit_container = cc1.selectbox("Container", idle_containers, key="commit_container")
        commit_date = cc2.date_input("Committed resolution date", st.session_state.sim_date + timedelta(days=7))
        if cc3.button("Log it"):
            st.session_state.sim_store.register_commitment(commit_container, commit_date.isoformat())
            themed_alert("success", f"Commitment logged for {commit_container} until {commit_date.isoformat()}")

    if st.session_state.spotlight_a or st.session_state.spotlight_b:
        st.subheader("Spotlight containers")
        spot_cols = st.columns(2)
        for i, container_no in enumerate([st.session_state.spotlight_a, st.session_state.spotlight_b]):
            if not container_no:
                continue
            with spot_cols[i]:
                st.markdown(f"**`{container_no}`**")
                sub_log = [e for e in st.session_state.sim_log if e["container_no"] == container_no]
                if sub_log:
                    sub_df = pd.DataFrame(sub_log)[["run_date", "event", "detail"]].copy()
                    sub_df["recipient"] = [
                        resolve_event_recipient(container_no, e["event"], port_lookup) for e in sub_log
                    ]
                    st.dataframe(sub_df, width="stretch", hide_index=True)
                else:
                    st.caption("No events yet -- click a button above to start the simulation.")

    st.subheader("Full event log")
    if st.session_state.sim_log:
        log_df = pd.DataFrame(st.session_state.sim_log)
        log_df["recipient"] = [
            resolve_event_recipient(r["container_no"], r["event"], port_lookup) for r in st.session_state.sim_log
        ]
        st.dataframe(log_df, width="stretch", height=350)
        st.download_button(
            "⬇️ Download event log (CSV)",
            log_df.to_csv(index=False).encode("utf-8"),
            "reminder_escalation_log.csv",
            "text/csv",
        )
    else:
        st.caption("No simulated runs yet.")
