import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from costguard import service
from main import app
from tests.test_execution import artifact


class ServiceTests(unittest.TestCase):
    def test_comparing_a_missing_run_is_not_found(self):
        with patch("costguard.service.db.get_artifact", return_value=None):
            with self.assertRaises(service.NotFound):
                service.compare("a", "b", artifact("x").pricing)

    def test_comparison_is_saved_with_its_request_and_announced(self):
        left, right = artifact("baseline"), artifact("candidate", 2000)
        saved = {}
        with (patch("costguard.service.db.get_artifact", side_effect=[left, right]),
              patch("costguard.service.db.save_comparison",
                    side_effect=lambda *args: saved.update(request=args[2]) or "report-1"),
              patch("costguard.service.events.emit") as emit):
            result = service.compare("a", "b", left.pricing, monthly_requests=1000)
        self.assertEqual(result["id"], "report-1")
        self.assertEqual(saved["request"]["monthly_requests"], 1000)
        self.assertIsNone(saved["request"]["policy"])
        emit.assert_called_once_with("comparison.saved", report_id="report-1", status=result["report"]["status"])

    def test_investigating_needs_an_analyst_and_a_report(self):
        with patch("costguard.service.db.get_analyst_settings", return_value=None):
            with self.assertRaises(service.Conflict):
                service.start_investigation("report", "why?")
        with (patch("costguard.service.db.get_analyst_settings", return_value={}),
              patch("costguard.service.db.get_comparison", return_value=None)):
            with self.assertRaises(service.NotFound):
                service.start_investigation("report", "why?")

    def test_database_conflicts_become_service_conflicts(self):
        with patch("costguard.service.db.run_investigation_proposal", side_effect=ValueError("no proposal")):
            with self.assertRaises(service.Conflict):
                service.run_proposal("investigation")


class HttpMappingTests(unittest.TestCase):
    def test_domain_errors_become_http_statuses(self):
        client = TestClient(app, base_url="http://localhost")
        with patch.dict("os.environ", {"COSTGUARD_DEMO": "0"}):
            disabled = client.post("/api/v1/demo")
        self.assertEqual(disabled.status_code, 404)
        self.assertIn("Demo is disabled", disabled.json()["detail"])
        with patch("costguard.service.db.get_analyst_settings", return_value=None):
            conflict = client.post("/api/v1/investigations", json={"report_id": "r", "question": "Why did cost rise?"})
        self.assertEqual(conflict.status_code, 409)
        with patch("costguard.service.start_mock_demo", side_effect=service.Unavailable("down")):
            self.assertEqual(client.post("/api/v1/demo").status_code, 503)


if __name__ == "__main__":
    unittest.main()
