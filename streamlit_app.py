"""
Container Aging & Export Reminder Automation -- interactive POC demo.

This is a thin UI layer only. All business logic lives in src/ and is
imported unchanged from the CLI version (src/rule_engine.py,
src/email_templates.py, src/reminder_engine.py) -- the web app never
re-implements detection, scoring, or reminder logic, it just visualizes it.
"""
import io
import json
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from src.config_loader import load_config
from src.rule_engine import classify_idle, SYNTHETIC_DATA_NOTICE
from src.email_templates import build_email_report
from src.reminder_engine import ReminderStateStore, process_run

SIM_START_DATE = date(2026, 7, 20)
DEFAULT_XLSX = "Activities_Demo_data_with_agreed_days.xlsx"

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

@st.cache_data(show_spinner="Running detection + confidence scoring...")
def compute_report(file_bytes: bytes, config_json: str) -> pd.DataFrame:
    config = json.loads(config_json)
    df = pd.read_excel(io.BytesIO(file_bytes))
    idle_df = classify_idle(df, config)
    return build_email_report(idle_df, config)


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


# ------------------------------------------------------------------ data --

top_l, top_r = st.columns([9, 1])
with top_l:
    st.title("🚢 Container Aging & Export Reminder Automation")
    st.caption("Proof of concept -- detection logic, confidence-scored email drafting, and simulated reminder/escalation lifecycle")
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

st.warning(
    f"⚠️ **{SYNTHETIC_DATA_NOTICE}** All Agreed_Free_Days values in this demo are "
    "assumed for POC purposes only -- not the client's real business rule.",
    icon="⚠️",
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
        "Escalation trigger (# reminders)", 1, 10, base_config["escalation_trigger_count"],
    )

config = dict(base_config)
config["confidence_threshold"] = confidence_threshold
config["reminder_cadence_days"] = int(reminder_cadence_days)
config["escalation_trigger_count"] = int(escalation_trigger_count)

report_df = compute_report(file_bytes, json.dumps(config, sort_keys=True))
init_session_state(report_df)

tab_dash, tab_report, tab_email, tab_sim, tab_config = st.tabs(
    ["📊 Dashboard", "📋 Idle Container Report", "✉️ Email Preview",
     "⏱️ Reminder Simulator", "⚙️ Config & Assumptions"]
)

# --------------------------------------------------------------- dashboard --

with tab_dash:
    total = len(report_df)
    ready = int((report_df["email_status"] == "READY").sum())
    needs_review = total - ready
    depo_gaps = int(report_df["depo_missing"].sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Idle containers detected", total)
    c2.metric("Emails READY to send", ready)
    c3.metric("Emails NEEDS REVIEW", needs_review)
    c4.metric("Missing Depo (data gap)", depo_gaps)

    col1, col2 = st.columns(2)
    with col1:
        by_cat = report_df["idle_category"].value_counts().reset_index()
        by_cat.columns = ["Category", "Count"]
        st.plotly_chart(px.bar(by_cat, x="Category", y="Count", title="Idle containers by category", template=PLOT_TEMPLATE), width='stretch')
    with col2:
        by_sev = report_df["severity_tier"].value_counts().reset_index()
        by_sev.columns = ["Severity", "Count"]
        st.plotly_chart(px.pie(by_sev, names="Severity", values="Count", title="By severity tier (reporting only)", template=PLOT_TEMPLATE), width='stretch')

    by_status = report_df["email_status"].value_counts().reset_index()
    by_status.columns = ["Status", "Count"]
    st.plotly_chart(px.bar(by_status, x="Status", y="Count", color="Status", title="Email readiness", template=PLOT_TEMPLATE), width='stretch')

# ------------------------------------------------------------------ report --

with tab_report:
    fc1, fc2, fc3, fc4 = st.columns(4)
    cat_filter = fc1.multiselect("Category", sorted(report_df["idle_category"].unique()))
    sev_filter = fc2.multiselect("Severity", sorted(report_df["severity_tier"].unique()))
    status_filter = fc3.multiselect("Email status", sorted(report_df["email_status"].unique()))
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
        "CONTAINERNO", "idle_category", "PORT", "Depo", "days", "Agreed_Free_Days",
        "days_overdue", "severity_tier", "confidence_score", "email_status", "review_reasons",
    ]
    st.dataframe(filtered[display_cols], width='stretch', height=500)
    st.caption(f"Showing {len(filtered)} of {len(report_df)} idle containers")

    csv_bytes = filtered[display_cols].to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download filtered report (CSV)", csv_bytes, "idle_container_report.csv", "text/csv")

# ------------------------------------------------------------------- email --

with tab_email:
    if report_df.empty:
        st.info("No idle containers to preview.")
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
            st.error(f"Review reasons: {row['review_reasons']}")

        st.text_area("Filled email template", row["email_text"], height=220)

# ---------------------------------------------------------------- simulator --

with tab_sim:
    st.markdown(
        "Drives `src/reminder_engine.process_run` with a **simulated clock** -- "
        "no real time passes and no real emails are sent. Cadence and escalation "
        "trigger come from the sidebar config."
    )

    idle_containers = report_df["CONTAINERNO"].tolist()

    bcol1, bcol2, bcol3 = st.columns(3)
    if bcol1.button("▶️ Play full 6-step demo lifecycle", width='stretch'):
        play_full_demo(idle_containers, config)
    if bcol2.button(f"⏭️ Advance {config['reminder_cadence_days']} simulated day(s)", width='stretch'):
        run_one_step(idle_containers, config, config["reminder_cadence_days"])
    if bcol3.button("🔄 Reset simulation", width='stretch'):
        reset_simulation(report_df)

    st.info(f"**Simulated date:** {st.session_state.sim_date.isoformat()}")

    with st.expander("📝 Log a commitment (suppresses reminders until the date passes)"):
        cc1, cc2, cc3 = st.columns([2, 2, 1])
        commit_container = cc1.selectbox("Container", idle_containers, key="commit_container")
        commit_date = cc2.date_input("Committed resolution date", st.session_state.sim_date + timedelta(days=7))
        if cc3.button("Log it"):
            st.session_state.sim_store.register_commitment(commit_container, commit_date.isoformat())
            st.success(f"Commitment logged for {commit_container} until {commit_date.isoformat()}")

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
                    st.dataframe(pd.DataFrame(sub_log)[["run_date", "event", "detail"]], width='stretch', hide_index=True)
                else:
                    st.caption("No events yet -- click a button above to start the simulation.")

    st.subheader("Full event log")
    if st.session_state.sim_log:
        log_df = pd.DataFrame(st.session_state.sim_log)
        st.dataframe(log_df, width='stretch', height=350)
        st.download_button(
            "⬇️ Download event log (CSV)",
            log_df.to_csv(index=False).encode("utf-8"),
            "reminder_escalation_log.csv",
            "text/csv",
        )
    else:
        st.caption("No simulated runs yet.")

# ------------------------------------------------------------------ config --

with tab_config:
    st.subheader("Current effective config")
    st.json(config)

    st.subheader("⚠️ Data assumptions")
    st.markdown(
        f"- `Agreed_Free_Days` is **{SYNTHETIC_DATA_NOTICE.lower()}**\n"
        "- Real values will come from parsing client email correspondence + ClimaxSuite (out of scope for this POC)\n"
        "- Missing `Depo` is treated as \"still at yard\" and flagged as a data gap, not excluded\n"
    )

    st.subheader("Explicitly out of scope for this POC")
    st.markdown(
        "- No live ClimaxSuite connection\n"
        "- No live email/inbox parsing\n"
        "- No real email sending, no external API calls\n"
        "- No \"CRO-stage\" pre-depot idle category\n"
        "- No fixed day-count thresholds anywhere in the detection logic -- always per-container `days > Agreed_Free_Days`\n"
    )
