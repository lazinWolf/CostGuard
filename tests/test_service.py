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
        row = {"result": {"proposals": [{"spec": spec("fix"), "hypothesis": "h"}]}, "fixes": []}
        with (patch("costguard.service.db.get_investigation", return_value=row),
              patch("costguard.service.db.create_fix_jobs", side_effect=ValueError("no proposal"))):
            with self.assertRaises(service.Conflict):
                service.test_fixes("investigation")
        with patch("costguard.service.db.get_investigation", return_value=None):
            with self.assertRaises(service.NotFound):
                service.test_fixes("investigation")


def spec(name, prompt="Be brief.", cases=1):
    return {"name": name, "suite_id": "suite", "kind": "prompt", "provider": "test", "model": "echo",
            "system_prompt": prompt, "pricing": artifact("x").pricing.model_dump(mode="json"),
            "cases": [{"case_id": f"c{n}", "prompt": "hi", "expected_text": "hi"} for n in range(cases)]}


class FixTests(unittest.TestCase):
    """A fix run is compared with the original baseline, under the original check's prices and limits."""

    def setUp(self):
        self.original = {"request": {"baseline_id": "base-artifact", "pricing": artifact("x").pricing.model_dump(mode="json"),
                                     "policy": {"max_cost_increase_percent": "20"}, "monthly_requests": 1000}}
        self.jobs = {"job-a": {"id": "job-a", "state": "completed", "artifact_id": "fix-artifact", "progress": 1, "total": 1}}
        self.reports = {}
        self.found = {"investigation_id": "inv", "check_id": "check", "report_id": None}
        self.compare = patch("costguard.service.compare", return_value={"id": "fix-report", "report": {"status": "pass"}}).start()
        patch("costguard.service.db.find_fix", side_effect=lambda job_id: self.found if job_id == "job-a" else None).start()
        patch("costguard.service.db.get_job", side_effect=self.jobs.get).start()
        patch("costguard.service.db.get_comparison", side_effect=lambda report_id:
              self.original if report_id == "check" else self.reports.get(report_id)).start()
        self.recorded = patch("costguard.service.db.set_fix_report").start()
        patch("costguard.service.events.emit").start()
        self.addCleanup(patch.stopall)

    def test_a_finished_fix_is_compared_with_the_original_baseline_and_limits(self):
        self.assertEqual(service.finish_fix("job-a"), "fix-report")
        args = self.compare.call_args.args
        self.assertEqual(args[:2], ("base-artifact", "fix-artifact"))
        self.assertEqual(args[3].max_cost_increase_percent, 20)
        self.assertEqual(args[4], 1000)
        self.recorded.assert_called_once_with("inv", "job-a", "fix-report")

    def test_other_jobs_unfinished_fixes_and_measured_fixes_are_left_alone(self):
        self.assertIsNone(service.finish_fix("job-elsewhere"))
        self.jobs["job-a"]["state"] = "running"
        self.assertIsNone(service.finish_fix("job-a"))
        self.jobs["job-a"]["state"] = "completed"
        self.found["report_id"] = "fix-report"
        self.assertIsNone(service.finish_fix("job-a"))
        self.compare.assert_not_called()

    def test_each_fix_shows_how_far_it_is_and_the_cheapest_passing_one_wins(self):
        def measured(cost):
            return {"report": {"status": "pass", "baseline_case_count": 8, "economic": {
                "cost_per_request_usd": {"candidate": cost}, "quality_score": {"candidate": "1"}}}}
        self.reports.update(r1=measured("0.0002"), r2=measured("0.0001"))
        row = {"result": {"proposals": [{"spec": spec(f"analyst-fix-1{letter}", f"{'word ' * size}"), "hypothesis": f"h{letter}"}
                                        for letter, size in (("a", 3), ("b", 9), ("c", 5))]},
               "fixes": [{"name": "a", "job_id": "job-a", "report_id": "r1"}, {"name": "b", "job_id": "job-b", "report_id": "r2"},
                         {"name": "c", "job_id": "job-c", "report_id": None}]}
        self.jobs.update({"job-b": {"id": "job-b", "state": "completed", "progress": 1, "total": 1},
                          "job-c": {"id": "job-c", "state": "running", "progress": 3, "total": 8}})
        rows = service.fix_rows(row)
        self.assertEqual([(fix["label"], fix["state"], fix["winner"]) for fix in rows],
                         [("Fix A", "measured", False), ("Fix B", "measured", True), ("Fix C", "running", False)])
        self.assertEqual((rows[2]["progress"], rows[2]["total"]), (3, 8))
        self.assertEqual(rows[0]["words"], 3)
        self.assertEqual({fix["state"] for fix in service.fix_rows({**row, "fixes": []})}, {"proposed"})


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
