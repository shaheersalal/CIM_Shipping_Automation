"""
Email template filling + confidence scoring.

This module does NOT decide who is idle (see rule_engine.py) and does NOT
track reminder counts or escalation (see reminder_engine.py). Its only job:
given an idle-container row, fill a fixed template for that row's category
and score how cleanly the available data fills it.

Emails are NEVER sent from here -- this only produces text + a status tag.
"""
import pandas as pd

TEMPLATES = {
    "empty_at_depot": (
        "Subject: Empty Container Aging Beyond Free Days -- {container_no}\n\n"
        "Dear {trade_person_name},\n\n"
        "Container {container_no} has been sitting EMPTY at {depot} "
        "(Port: {port}) for {days_overdue} day(s) beyond the agreed free "
        "period of {agreed_days} day(s).\n"
        "Please arrange return/re-positioning or advise on next steps.\n\n"
        "Regards,\nContainer Control Team"
    ),
    "import_not_picked": (
        "Subject: Import Container Not Picked Up -- {container_no}\n\n"
        "Dear {trade_person_name},\n\n"
        "Container {container_no} was discharged at {port} and has not "
        "been picked up by the consignee for {days_overdue} day(s) beyond "
        "the agreed free period of {agreed_days} day(s). Current location: "
        "{depot}.\n"
        "Please follow up with the consignee for immediate collection.\n\n"
        "Regards,\nContainer Control Team"
    ),
    "export_not_shipped": (
        "Subject: Export Container Awaiting Shipment -- {container_no}\n\n"
        "Dear {trade_person_name},\n\n"
        "Container {container_no} at {port} ({depot}) has not been shipped "
        "out for {days_overdue} day(s) beyond the agreed free period of "
        "{agreed_days} day(s).\n"
        "Please confirm the booking/vessel plan or advise on delays.\n\n"
        "Regards,\nContainer Control Team"
    ),
}


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

    return max(score, 0), reasons


def fill_email(row: pd.Series, config: dict) -> str:
    template = TEMPLATES[row["idle_category"]]
    return template.format(
        container_no=row["CONTAINERNO"],
        port=row["PORT"] if pd.notna(row["PORT"]) else "Unknown Port",
        depot=row["Depo"] if pd.notna(row["Depo"]) and str(row["Depo"]).strip() else "Unknown Depot",
        days_overdue=int(row["days_overdue"]),
        agreed_days=int(row["Agreed_Free_Days"]),
        trade_person_name=config["trade_person_name"],
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
