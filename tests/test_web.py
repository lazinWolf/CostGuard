import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from costguard import service
from costguard.economics import compare_artifacts
from costguard.models import ComparisonPolicy
from main import app
from tests.test_execution import artifact

POLICY = {"max_cost_increase_percent": "20", "max_quality_drop": "0"}


def saved_check(candidate_tokens=3000):
    left, right = artifact("baseline"), artifact("candidate", candidate_tokens)
    left.configuration = {"system_prompt": "Answer briefly."}
    right.configuration = {"system_prompt": "Answer at length, politely."}
    report = compare_artifacts(left, right, left.pricing, ComparisonPolicy.model_validate(POLICY), 1000)
    saved = {"id": "check-1", "report": report.model_dump(mode="json"), "request": {
        "baseline_id": str(left.artifact_id), "candidate_id": str(right.artifact_id),
        "pricing": left.pricing.model_dump(mode="json"), "policy": POLICY, "monthly_requests": 1000}}
    return saved, left, right


class CheckPageTests(unittest.TestCase):
    def render(self, investigations=()):
        saved, left, right = saved_check()
        artifacts = {str(left.artifact_id): left, str(right.artifact_id): right}
        with (patch("costguard.service.db.get_comparison", return_value=saved),
              patch("costguard.service.db.get_artifact", side_effect=artifacts.get),
              patch("costguard.service.db.source_experiment", return_value=None),
              patch("costguard.service.db.related_investigations",
                    return_value={"investigations": list(investigations), "source": None}),
              patch("costguard.web.db.get_analyst_settings", return_value=None)):
            return TestClient(app, base_url="http://localhost").get("/checks/check-1")

    def test_the_page_leads_with_the_verdict_and_each_limit(self):
        response = self.render()
        self.assertEqual(response.status_code, 200, response.text[:300])
        page = response.text
        self.assertIn("FAIL", page.upper())
        self.assertIn("breaks 1 of 2 limits", page)
        self.assertIn("cost per request may rise by at most 20%", page)
        self.assertIn("+200.00%", page)
        self.assertIn("no fewer correct answers than the baseline", page)
        self.assertIn("No analyst model is configured", page)

    def test_a_missing_check_is_a_404(self):
        with patch("costguard.service.db.get_comparison", return_value=None):
            response = TestClient(app, base_url="http://localhost").get("/checks/nope")
        self.assertEqual(response.status_code, 404)

    def test_old_addresses_redirect_to_the_check(self):
        client = TestClient(app, base_url="http://localhost", follow_redirects=False)
        self.assertEqual(client.get("/reports/abc").headers["location"], "/checks/abc")
        self.assertEqual(client.get("/investigations?report_id=abc").headers["location"], "/checks/abc#explain")
        with patch("costguard.web.db.get_investigation", return_value={"report_id": "abc"}):
            self.assertEqual(client.get("/investigations/i1").headers["location"], "/checks/abc#explain")


class CheckListTests(unittest.TestCase):
    def test_fixes_nest_under_the_check_they_fix_in_order(self):
        row = lambda name: {"id": name, "quality_baseline": "0.75", "quality_candidate": "0.25", "quality_cases": 8}
        rows = [row("fix-2"), row("fix-1"), row("original"), row("unrelated")]  # newest first
        with (patch("costguard.service.db.list_comparisons", return_value=rows),
              patch("costguard.service.db.fix_parents", return_value={"fix-1": "original", "fix-2": "fix-1"})):
            checks = service.list_checks()
        self.assertEqual([check["id"] for check in checks], ["original", "unrelated"])
        self.assertEqual([fix["id"] for fix in checks[0]["fixes"]], ["fix-1", "fix-2"])
        self.assertEqual(checks[0]["correct"], "6/8 → 2/8 correct")


if __name__ == "__main__":
    unittest.main()
