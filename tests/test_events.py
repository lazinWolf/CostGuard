import unittest

from costguard import events


class EventTests(unittest.TestCase):
    def setUp(self):
        self.saved = list(events._sinks)
        events.clear_sinks()

    def tearDown(self):
        events._sinks[:] = self.saved

    def test_events_reach_every_sink_with_a_kind_and_a_time(self):
        seen = []
        events.add_sink(seen.append)
        events.emit("run.finished", cases=8)
        self.assertEqual(seen[0]["event"], "run.finished")
        self.assertEqual(seen[0]["cases"], 8)
        self.assertIn("at", seen[0])

    def test_a_failing_sink_never_breaks_the_caller_or_other_sinks(self):
        seen = []
        events.add_sink(lambda event: 1 / 0)
        events.add_sink(seen.append)
        events.emit("comparison.saved")
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
