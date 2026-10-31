import unittest
from unittest.mock import patch

from costguard import mcp_server

CHECK = {"model": "ollama/llama3.2:3b", "input_per_million_usd": "0.1", "output_per_million_usd": "0.4",
         "baseline_prompt": "Reply with the category only.", "candidate_prompt": "Be friendly.",
         "cases": [{"case_id": "a", "prompt": "hi", "expected_text": "billing"}]}


class McpTests(unittest.TestCase):
    def test_starting_a_check_is_refused_unless_runs_are_allowed(self):
        with patch.dict("os.environ", {"COSTGUARD_MCP_ALLOW_RUNS": "0"}), patch("costguard.mcp_server.service") as service:
            with self.assertRaisesRegex(ValueError, "COSTGUARD_MCP_ALLOW_RUNS"):
                mcp_server.start_check(CHECK)
        service.create_check.assert_not_called()

    def test_an_allowed_run_goes_through_the_same_service_as_the_web_page(self):
        with patch.dict("os.environ", {"COSTGUARD_MCP_ALLOW_RUNS": "1"}), patch("costguard.mcp_server.service") as service:
            service.create_check.return_value = {"baseline_job_id": "b", "candidate_job_id": "c"}
            self.assertEqual(mcp_server.start_check(CHECK)["candidate_job_id"], "c")
        self.assertEqual(service.create_check.call_args.args[0].model, "ollama/llama3.2:3b")
        with patch.dict("os.environ", {"COSTGUARD_MCP_ALLOW_RUNS": "1"}):
            with self.assertRaises(ValueError):  # Invalid input is rejected before anything runs.
                mcp_server.start_check({**CHECK, "cases": []})

    def test_reading_a_check_needs_no_flag_and_calls_no_model(self):
        from tests.test_web import saved_check
        saved, left, right = saved_check()
        artifacts = {str(left.artifact_id): left, str(right.artifact_id): right}
        with (patch("costguard.service.db.get_comparison", return_value=saved),
              patch("costguard.service.db.get_artifact", side_effect=artifacts.get),
              patch("costguard.service.db.source_experiment", return_value=None),
              patch("costguard.service.db.related_investigations", return_value={"investigations": [], "source": None}),
              patch("costguard.service.recorded_reports", return_value={})):
            check, why = mcp_server.get_check("check-1"), mcp_server.explain_check("check-1")
        self.assertEqual(check["verdict"], "fail")
        self.assertEqual([row["status"] for row in check["limits"]], ["fail", "pass"])
        self.assertEqual(why["prompt_change"]["baseline_words"], 2)
        self.assertEqual(why["changed_cases"][0]["case_id"], "one")
        self.assertIn("input tokens", why["why"][0])


if __name__ == "__main__":
    unittest.main()
