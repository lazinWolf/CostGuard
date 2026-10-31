import unittest
from decimal import Decimal
from unittest.mock import patch

from pydantic import ValidationError

from costguard import checks, service

CASES = [{"case_id": "refund", "prompt": "I want my money back", "expected_text": "billing"},
         {"case_id": "crash", "prompt": "The app crashes", "expected_text": "technical"}]


def check_input(**changes):
    values = dict(model="ollama/llama3.2:3b", input_per_million_usd="0.1", output_per_million_usd="0.4",
                  baseline_prompt="Reply with the category only.", candidate_prompt="Be friendly and thorough.",
                  cases=CASES)
    return checks.CheckInput(**(values | changes))


class CheckInputTests(unittest.TestCase):
    def test_two_experiments_differ_only_in_name_and_prompt(self):
        baseline, candidate, pricing = checks.build_specs(check_input(
            baseline_name="strict", candidate_name="friendly", temperature=0))
        self.assertEqual((baseline.provider, baseline.model), ("ollama", "llama3.2:3b"))
        self.assertEqual((baseline.name, candidate.name), ("strict", "friendly"))
        self.assertEqual(baseline.system_prompt, "Reply with the category only.")
        self.assertEqual(candidate.system_prompt, "Be friendly and thorough.")
        self.assertEqual(baseline.model_dump(exclude={"name", "system_prompt"}),
                         candidate.model_dump(exclude={"name", "system_prompt"}))
        self.assertEqual(pricing.entries[0].input_per_million_usd, Decimal("0.1"))
        self.assertEqual([case.case_id for case in baseline.cases], ["refund", "crash"])
        self.assertEqual(baseline.temperature, 0)

    def test_each_check_gets_its_own_suite_unless_told_otherwise(self):
        self.assertNotEqual(check_input().suite_id, check_input().suite_id)
        self.assertEqual(check_input(suite_id="mine").suite_id, "mine")

    def test_the_demo_check_matches_the_demo_suite(self):
        demo = checks.preset()
        baseline, candidate, pricing = checks.build_specs(checks.demo_check("ollama/llama3.2", 0.1, 0.4))
        self.assertEqual(baseline.suite_id, "support-triage")
        self.assertEqual(baseline.evaluator, "normalized")
        self.assertEqual((baseline.temperature, candidate.temperature), (0, 0))
        self.assertNotEqual(baseline.system_prompt, candidate.system_prompt)
        self.assertEqual(pricing.version, demo["reference_pricing"]["version"])
        self.assertEqual(checks.demo_check("ollama/llama3.2", 0.1, 0.4).policy.max_quality_drop, 0)

    def test_a_check_must_be_checkable(self):
        bad = [dict(model="llama3.2"), dict(candidate_name="baseline"), dict(cases=[]),
               dict(cases=[{"case_id": "a", "prompt": "x"}]),
               dict(cases=[{"case_id": "a", "prompt": "x", "expected_text": "  "}])]
        for changes in bad:
            with self.assertRaises(ValidationError, msg=changes):
                check_input(**changes)
        with self.assertRaises(ValueError):  # Case names must be unique across the suite.
            checks.build_specs(check_input(cases=CASES + CASES[:1]))


class PipelineTests(unittest.TestCase):
    """A check is queued as two jobs; the runner compares them once the second is done."""

    def setUp(self):
        self.settings: dict = {}
        self.jobs: dict = {}
        self.patches = [
            patch("costguard.service.db.get_setting", side_effect=lambda key: self.settings.get(key)),
            patch("costguard.service.db.save_setting", side_effect=lambda key, value: self.settings.update({key: value})),
            patch("costguard.service.db.get_job", side_effect=lambda job_id: self.jobs.get(job_id)),
            patch("costguard.service.db.create_job", side_effect=self.queue),
            patch("costguard.service.db.save_suite"), patch("costguard.service.db.save_analyst_settings"),
            patch("costguard.service.events.emit")]
        self.compare = patch("costguard.service.compare", return_value={"id": "report-1"}).start()
        for item in self.patches:
            item.start()
        self.addCleanup(patch.stopall)
        self.started = service.create_check(check_input(baseline_name="base", candidate_name="cand"))

    def queue(self, spec) -> str:
        """Stand-in for db.create_job: a job is named after its experiment."""
        self.jobs[spec.name] = {"id": spec.name, "state": "queued", "artifact_id": None, "error": None,
                                "progress": 0, "total": len(spec.cases), "cases": []}
        return spec.name

    def complete(self, name):
        self.jobs[name].update(state="completed", artifact_id=f"artifact-{name}")

    def test_creating_a_check_queues_both_runs_and_a_plan(self):
        self.assertEqual(self.started, {"baseline_job_id": "base", "candidate_job_id": "cand"})
        self.assertEqual(self.settings["pending-check:cand"]["policy"]["max_cost_increase_percent"], None)
        self.assertEqual(service.check_progress("cand")["state"], "running")

    def test_nothing_is_compared_until_both_runs_completed(self):
        self.assertIsNone(service.finish_check("unrelated"))
        self.complete("cand")
        self.assertIsNone(service.finish_check("cand"))
        self.compare.assert_not_called()

    def test_the_pair_is_compared_once_with_the_checks_policy(self):
        self.complete("base"), self.complete("cand")
        self.assertEqual(service.finish_check("cand"), "report-1")
        self.assertIsNone(service.finish_check("cand"))
        self.compare.assert_called_once()
        self.assertEqual(self.compare.call_args.args[:2], ("artifact-base", "artifact-cand"))
        progress = service.check_progress("cand")
        self.assertEqual((progress["state"], progress["report_id"]), ("done", "report-1"))

    def test_a_stopped_run_fails_the_check_with_its_reason(self):
        self.jobs["base"].update(state="failed", error="model request failed: HTTP 504 (timeout)")
        progress = service.check_progress("cand")
        self.assertEqual(progress["state"], "failed")
        self.assertIn("HTTP 504", progress["error"])
        self.jobs["base"].update(state="cancelled", error=None)
        self.assertEqual(service.check_progress("cand")["error"], "The run cancelled.")

    def test_progress_of_an_unknown_check_is_not_found(self):
        with self.assertRaises(service.NotFound):
            service.check_progress("nope")


if __name__ == "__main__":
    unittest.main()
