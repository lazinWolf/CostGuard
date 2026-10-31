import json
import unittest
from unittest.mock import patch

from costguard import recorded, service
from tests.test_execution import artifact
from tests.test_service import spec

POLICY = {"max_cost_increase_percent": "20"}


def example():
    """A tiny recorded session: one check and one fix, with its analyst result."""
    left, right, fix = artifact("baseline"), artifact("candidate", 3000), artifact("fix")
    runs = [{"artifact": json.loads(item.model_dump_json()), "spec": spec(item.name)} for item in (left, right, fix)]
    request = lambda candidate: {"baseline_id": str(left.artifact_id), "candidate_id": str(candidate.artifact_id),
                                 "pricing": left.pricing.model_dump(mode="json"), "policy": POLICY, "monthly_requests": 1000}
    return {"name": "tiny", "check_id": "check", "recorded": {"model": "test/echo", "analyst": "test/analyst", "at": "2026-10-31T00:00:00+00:00"},
            "runs": runs, "comparisons": [{"id": "check", "request": request(right)}, {"id": "fix-report", "request": request(fix)}],
            "investigation": {"id": "inv", "report_id": "check", "state": "completed", "error": None, "request": {},
                              "result": {"proposals": []}, "fixes": [{"name": "fix", "job_id": "job", "report_id": "fix-report"}]}}


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.artifacts, self.comparisons, self.jobs, self.settings = {}, {}, {}, {}
        self.investigations = []
        stubs = {
            "import_artifact": lambda item: self.artifacts.setdefault(str(item.artifact_id), item) and (str(item.artifact_id), True),
            "import_job": lambda job_id, run, artifact_id: self.jobs.setdefault(job_id, (run, artifact_id)),
            "save_suite": lambda run: None,
            "get_artifact": lambda artifact_id: self.artifacts[artifact_id],
            "get_comparison": lambda report_id: self.comparisons.get(report_id),
            "save_comparison": lambda left, right, request, report, report_id=None: self.comparisons.update({report_id: report}),
            "import_investigation": lambda item: self.investigations.append(item["id"]),
            "save_setting": lambda key, value: self.settings.update({key: value}),
        }
        for name, function in stubs.items():
            patch(f"costguard.recorded.db.{name}", side_effect=function).start()
        self.addCleanup(patch.stopall)

    def test_replay_saves_the_runs_comparisons_and_investigation_under_their_ids(self):
        self.assertEqual(recorded.replay(example()), "check")
        self.assertEqual(len(self.artifacts), 3)
        self.assertEqual(set(self.comparisons), {"check", "fix-report"})
        self.assertEqual(self.investigations, ["inv"])
        self.assertEqual(self.settings["recorded:tiny"]["report_ids"], ["check", "fix-report"])
        self.assertEqual(self.settings["recorded:tiny"]["model"], "test/echo")

    def test_verdicts_are_recomputed_not_taken_from_the_file(self):
        recorded.replay(example())
        self.assertEqual(self.comparisons["check"]["status"], "fail")  # +200% against a 20% limit
        self.assertEqual(self.comparisons["fix-report"]["status"], "pass")

    def test_replaying_twice_changes_nothing(self):
        data = example()
        recorded.replay(data)
        before = dict(self.comparisons)
        recorded.replay(data)
        self.assertEqual(self.comparisons, before)
        self.assertEqual(len(self.jobs), 3)

    def test_a_replayed_run_always_gets_the_same_job_id(self):
        self.assertEqual(recorded.job_id_for("a"), recorded.job_id_for("a"))
        self.assertNotEqual(recorded.job_id_for("a"), recorded.job_id_for("b"))


class LoadTests(unittest.TestCase):
    def test_an_unknown_example_is_not_found(self):
        with self.assertRaises(service.NotFound):
            service.load_recorded("../../etc/passwd")

    def test_the_packaged_example_is_loadable_and_self_consistent(self):
        example = recorded.read("support-triage")
        self.assertIsNotNone(example, "run `python -m costguard record` to export examples/recorded/support-triage.json")
        ids = {item["id"] for item in example["comparisons"]}
        self.assertIn(example["check_id"], ids)
        self.assertEqual(example["investigation"]["report_id"], example["check_id"])
        self.assertTrue({fix["report_id"] for fix in example["investigation"]["fixes"]} <= ids)
        for item in example["comparisons"]:
            request = item["request"]
            artifacts = {str(run["artifact"]["artifact_id"]) for run in example["runs"]}
            self.assertTrue({request["baseline_id"], request["candidate_id"]} <= artifacts)

    def test_recorded_checks_are_tagged_on_the_list(self):
        row = {"id": "check", "quality_baseline": None, "quality_candidate": None, "quality_cases": None}
        with (patch("costguard.service.db.list_comparisons", return_value=[row, dict(row, id="other")]),
              patch("costguard.service.db.fix_parents", return_value={}),
              patch("costguard.service.db.recorded_examples", return_value=[{"report_ids": ["check"]}])):
            checks = service.list_checks()
        self.assertEqual({item["id"]: item["recorded"] for item in checks}, {"check": True, "other": False})


if __name__ == "__main__":
    unittest.main()
