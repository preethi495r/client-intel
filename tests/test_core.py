from datetime import date
from decimal import Decimal
import unittest

from client_intel.models import AS_OF, Evidence, Source
from client_intel.run import _estimated_cost
from client_intel.tools.commercial import _state
from client_intel.tools.telemetry import _month_history, _observations
from client_intel.verification import audit_rendered, render, verify_draft


class ToolTests(unittest.TestCase):
    def test_pending_terms_do_not_enter_signed_coverage(self):
        row = {"start_date": "2026-10-01", "end_date": "2027-10-01", "signed_date": "2026-10-01",
               "status": "pending_signature"}
        state, _, _, caveat = _state(row, AS_OF)
        self.assertEqual(state, "pending")
        self.assertIn("conflicts", caveat)

    def test_equivalent_observations_preserve_both_sources(self):
        raw = {"month": "2026-09", "units_used": "50", "included_units": "100"}
        a = Source("usage_monthly", "U-example-a", 1, raw)
        b = Source("usage_monthly", "U-example-b", 2, dict(raw, units_used="50.0"))
        forward, unused = _observations([a, b], {})
        backward, _ = _observations([b, a], {})
        first, second = next(iter(forward.values())), next(iter(backward.values()))
        self.assertFalse(unused)
        self.assertEqual(first[:3], ("observed", Decimal("50"), Decimal("100")))
        self.assertEqual(second[:3], first[:3])
        self.assertCountEqual([item.rowid for item in first[3]], [1, 2])

    def test_conflicting_measurement_is_unknown_and_missing_is_not_zero(self):
        september, october, november = (date(2026, month, 1) for month in (9, 10, 11))
        a = Source("usage_monthly", "U-example-a", 1, {"month": "2026-09", "units_used": "50", "included_units": ""})
        b = Source("usage_monthly", "U-example-b", 2, dict(a.raw, units_used="51"))
        c = Source("usage_monthly", "U-example-c", 3, dict(a.raw, month="2026-11", units_used="0"))
        observations, _ = _observations([a, b, c], {})
        series, _ = _month_history([september, october, november], observations, [])
        self.assertEqual([(item["status"], item["used"]) for item in series],
                         [("conflicting", None), ("missing", None), ("observed", "0")])


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.usage = Evidence("usage.oct", "usage_trends", "Streaming usage was 42 hours in October.",
                              (Source("usage_monthly", "U-example", 1, {}),))
        self.other = Evidence("usage.sep", "usage_trends", "Archive usage was 57 queries in September.",
                               (Source("usage_monthly", "U-other", 2, {}),))
        self.options = {"F01": self.usage, "F02": self.other}
        self.index = {evidence.key: evidence for evidence in self.options.values()}
        self.trigger = dict(label="Test", lead_heading="Lead", support_heading="Context", action_heading="Next")

    def test_genuine_number_cannot_borrow_other_metric_or_period(self):
        sections, issues = verify_draft({"lead": ["F01"], "context": ["F02"], "actions": [], "gaps": []}, self.options)
        self.assertFalse(issues)
        text, mapping = render({"client_name": "Example Account"}, str(AS_OF), self.trigger,
                               sections, self.index, "verified", "")
        self.assertFalse(audit_rendered(text, mapping, self.index))
        forged = text.replace("42 hours in October", "57 hours in September")
        codes = {item["check"] for item in audit_rendered(forged, mapping, self.index)}
        self.assertIn("rendered_unbound_number", codes)
        self.assertIn("rendered_text_mismatch", codes)

    def test_missing_evidence_cannot_become_a_suggested_action(self):
        sections, issues = verify_draft({"lead": ["F01"], "context": [], "gaps": [], "actions": [
            {"action": "Ask about their viewing needs.", "why": ["F99"], "readiness": "needs_validation"}
        ]}, self.options)
        self.assertEqual(sections["actions"], [])
        self.assertEqual(issues[0]["check"], "unresolved_or_missing_citation")

    def test_ordinal_label_needs_matching_evidence(self):
        note = Evidence("notes.division", "meeting_context", "The note discusses second-division matches.",
                        (Source("meeting_notes", "N-example", 3, {}),))
        self.options["F03"] = note
        draft = {"lead": [], "context": [], "gaps": [], "actions": [
            {"action": "Which second division matches does the note cover?", "why": ["F03"], "readiness": "needs_validation"},
            {"action": "Which second-screen app does the note cover?", "why": ["F03"], "readiness": "needs_validation"},
            {"action": "Ask whether fifty thousand matches are covered.", "why": ["F03"], "readiness": "needs_validation"},
        ]}
        # Evaluate the missing label and genuine word quantity separately within the production limit.
        accepted, issues = verify_draft(dict(draft, actions=draft["actions"][:2]), self.options)
        self.assertEqual(len(accepted["actions"]), 1)
        self.assertEqual([item["check"] for item in issues], ["unbound_number"])
        accepted, issues = verify_draft(dict(draft, actions=draft["actions"][2:]), self.options)
        self.assertFalse(accepted["actions"])
        self.assertEqual([item["check"] for item in issues], ["unbound_number"])


class TraceTests(unittest.TestCase):
    def test_token_cost_uses_explicit_rates_only(self):
        model = {"usage": {"input_tokens": 1000, "output_tokens": 250}}
        self.assertFalse(_estimated_cost(model, {})["available"])
        env = {"CLIENT_INTEL_RATE_VERSION": "fixture", "CLIENT_INTEL_INPUT_EUR_PER_MTOK": "2",
               "CLIENT_INTEL_OUTPUT_EUR_PER_MTOK": "8"}
        self.assertEqual(Decimal(_estimated_cost(model, env)["estimated_eur"]), Decimal("0.004"))


if __name__ == "__main__":
    unittest.main()
