"""Conservative discovery decisions and read-only queue diagnostics; no IO."""
import re
from collections import Counter


def discovery_decision(title, *, router_ready, is_correction=False, review_hold=False):
    """Only explicitly recognized routine notices may leave the review queue."""
    name = re.sub(r"\s+", "", title)
    if review_hold:
        reason = "explicit_review_hold"
    elif is_correction:
        # Corrections require source evidence even when the base title is routine.
        reason = "correction_evidence_required"
    elif name in {
        "기업설명회(IR)개최(안내공시)",
        "임원ㆍ주요주주특정증권등소유상황보고서",
        "임원·주요주주특정증권등소유상황보고서",
        "최대주주등소유주식변동신고서",
    }:
        return {"status": "skipped_non_actionable", "is_actionable": False,
                "reason": "routine_notice", "last_error": "skipped: routine_notice"}
    elif re.fullmatch(r"분기보고서\(\d{4}\.\d{2}\)", name):
        reason = "quarterly_report_analysis_not_supported"
    elif name == "동일인등출자계열회사와의상품ㆍ용역거래변경":
        reason = "affiliate_transaction_evidence_required"
    elif router_ready:
        return {"status": "pending", "is_actionable": True,
                "reason": "router_ready", "last_error": None}
    else:
        reason = "unsupported_or_insufficient_source"
    return {"status": "pending", "is_actionable": False, "reason": reason,
            "last_error": "review_required: " + reason}


def discovery_counts(decisions):
    counts = Counter()
    excluded = Counter()
    review = Counter()
    for decision in decisions:
        if decision["status"] == "skipped_non_actionable":
            counts["excluded"] += 1
            excluded[decision["reason"]] += 1
        elif decision["is_actionable"]:
            counts["actionable"] += 1
        else:
            counts["review_required"] += 1
            review[decision["reason"]] += 1
    return {"actionable": counts["actionable"], "excluded": counts["excluded"],
            "review_required": counts["review_required"],
            "exclusion_reasons": dict(sorted(excluded.items())),
            "review_reasons": dict(sorted(review.items()))}
