"""Offline regression cases for the zero-processed production cycle."""
import unittest

from app.services.event_discovery_policy import discovery_counts, discovery_decision


class DiscoveryPolicyTests(unittest.TestCase):
    def test_observed_twelve_filings_have_separate_outcomes(self):
        titles = (["임원ㆍ주요주주특정증권등소유상황보고서"] * 3
                  + ["기업설명회(IR)개최(안내공시)              "] * 3
                  + ["동일인등출자계열회사와의상품ㆍ용역거래변경"] * 3
                  + ["분기보고서 (2026.09)"] * 2
                  + ["최대주주등소유주식변동신고서"])
        report = discovery_counts(discovery_decision(t, router_ready=False) for t in titles)
        self.assertEqual((report["actionable"], report["excluded"], report["review_required"]), (0, 7, 5))
        self.assertEqual(report["review_reasons"], {
            "affiliate_transaction_evidence_required": 3,
            "quarterly_report_analysis_not_supported": 2,
        })

    def test_correction_of_routine_notice_is_not_excluded(self):
        result = discovery_decision("기업설명회(IR)개최(안내공시)", router_ready=True, is_correction=True)
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["is_actionable"])
        self.assertEqual(result["reason"], "correction_evidence_required")

    def test_explicit_hold_wins_over_routine_and_router_ready(self):
        result = discovery_decision("최대주주등소유주식변동신고서", router_ready=True, review_hold=True)
        self.assertEqual(result["reason"], "explicit_review_hold")
        self.assertEqual(result["status"], "pending")

    def test_transaction_plan_is_not_mistaken_for_routine_ownership_report(self):
        result = discovery_decision("임원ㆍ주요주주특정증권등거래계획보고서", router_ready=True)
        self.assertTrue(result["is_actionable"])

    def test_unknown_report_requires_review(self):
        result = discovery_decision("새로운유형공시", router_ready=False)
        self.assertEqual(result["reason"], "unsupported_or_insufficient_source")
        self.assertTrue(result["last_error"].startswith("review_required:"))

    def test_quarterly_report_cannot_bypass_missing_analysis_support(self):
        self.assertFalse(discovery_decision("분기보고서 (2026.09)", router_ready=True)["is_actionable"])

    def test_empty_cycle_has_explicit_zero_counts(self):
        self.assertEqual(discovery_counts([]), {"actionable": 0, "excluded": 0,
                         "review_required": 0, "exclusion_reasons": {}, "review_reasons": {}})


if __name__ == "__main__":
    unittest.main()
