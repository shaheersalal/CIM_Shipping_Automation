"""
Email template filling + confidence scoring.

v2 change: the recipient is no longer a global placeholder -- it's the
Trade Person Email resolved (in rule_engine.classify_idle) from the
container's Port via the Setup/Configuration table. If that port's config
row has no Trade Person Email set, this shows "NO RECIPIENT CONFIGURED"
rather than silently falling back to a placeholder, and confidence is
forced low so it can never read as auto-send-ready.

This module still does NOT decide who is idle (rule_engine.py) and does
NOT track reminder counts or escalation (reminder_engine.py). Emails are
NEVER sent from here -- this only produces text + a status tag.
"""
import pandas as pd

TEMPLATES = {
    "empty_at_depot": (
        "To: {recipient}\n"
        "Subject: Empty Container Aging Beyond Free Days -- {container_no}\n\n"
        "Dear Trade Team,\n\n"
        "Container {container_no} has been sitting EMPTY at {depot} "
        "(Port: {port}) for {days_overdue} day(s) beyond the agreed free "
        "period of {agreed_days} day(s).\n"
        "Please arrange return/re-positioning or advise on next steps.\n\n"
        "Regards,\nContainer Control Team"
    ),
    "import_not_picked": (
        "To: {recipient}\n"
        "Subject: Import Container Not Picked Up -- {container_no}\n\n"
        "Dear Trade Team,\n\n"
        "Container {container_no} was discharged at {port} and has not "
        "been picked up by the consignee for {days_overdue} day(s) beyond "
        "the agreed free period of {agreed_days} day(s). Current location: "
        "{depot}.\n"
        "Please follow up with the consignee for immediate collection.\n\n"
        "Regards,\nContainer Control Team"
    ),
    "export_not_shipped": (
        "To: {recipient}\n"
        "Subject: Export Container Awaiting Shipment -- {container_no}\n\n"
        "Dear Trade Team,\n\n"
        "Container {container_no} at {port} ({depot}) has not been shipped "
        "out for {days_overdue} day(s) beyond the agreed free period of "
        "{agreed_days} day(s).\n"
        "Please confirm the booking/vessel plan or advise on delays.\n\n"
        "Regards,\nContainer Control Team"
    ),
}

NO_RECIPIENT_TEXT = "NO RECIPIENT CONFIGURED (Trade Person Email missing for this port)"


def _missing_reasons(row: pd.Series) -> list[str]:
    reasons = []
    if not row.get("PORT") or pd.isna(row.get("PORT")):
        reasons.append("PORT missing")
    if row.get("depo_missing"):
        reasons.append("Depo missing (data gap)")
    if row.get("region_missing"):
        reasons.append("REGION_NAME missing")
    if row.get("country_missing"):
        reasons.append("Country missing")
    if row.get("trade_email_missing"):
        reasons.append("Trade Person Email not configured for this port")
    return reasons


def compute_confidence(row: pd.Series, config: dict) -> tuple[int, list[str]]:
    penalties = config["confidence_penalties"]
    score = 100
    reasons = _missing_reasons(row)

    if "PORT missing" in reasons:
        score -= penalties["missing_port"]
    if "Depo missing (data gap)" in reasons:
        score -= penalties["missing_depot"]
    if "REGION_NAME missing" in reasons:
        score -= penalties["missing_region"]
    if "Country missing" in reasons:
        score -= penalties["missing_country"]
    if "Trade Person Email not configured for this port" in reasons:
        score -= penalties["missing_trade_person_email"]

    return max(score, 0), reasons


def fill_email(row: pd.Series, config: dict) -> str:
    template = TEMPLATES[row["idle_category"]]
    recipient = row["trade_person_email"] if pd.notna(row["trade_person_email"]) and str(row["trade_person_email"]).strip() else NO_RECIPIENT_TEXT
    return template.format(
        recipient=recipient,
        container_no=row["CONTAINERNO"],
        port=row["PORT"] if pd.notna(row["PORT"]) else "Unknown Port",
        depot=row["Depo"] if pd.notna(row["Depo"]) and str(row["Depo"]).strip() else "Unknown Depot",
        days_overdue=int(row["days_overdue"]),
        agreed_days=int(row["free_days"]),
    )


def build_email_report(idle_df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Adds confidence_score, review_reasons, email_status ('READY' /
    'NEEDS REVIEW'), and email_text columns to a copy of idle_df.
    """
    df = idle_df.copy()
    threshold = config["confidence_threshold"]

    scores, reasons_list, statuses, texts = [], [], [], []
    for _, row in df.iterrows():
        score, reasons = compute_confidence(row, config)
        status = "READY" if score >= threshold else "NEEDS REVIEW"
        text = fill_email(row, config)

        scores.append(score)
        reasons_list.append("; ".join(reasons) if reasons else "none")
        statuses.append(status)
        texts.append(text)

    df["confidence_score"] = scores
    df["review_reasons"] = reasons_list
    df["email_status"] = statuses
    df["email_text"] = texts
    return df
