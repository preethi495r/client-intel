from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from client_intel.jobs import Jobs, QueueFull
from client_intel.planning import Request


class JobTests(unittest.TestCase):
    def test_restart_preserves_completed_and_interrupts_unfinished(self):
        with TemporaryDirectory() as directory:
            jobs = Jobs(Path(directory))
            jobs.initialise()
            completed = jobs.enqueue(Request("C-demo", "renewal"))
            unfinished = jobs.enqueue(Request("C-demo", "renewal"))
            jobs.start(completed)
            jobs.start(unfinished)
            jobs.progress(unfinished, "writing")
            self.assertEqual(jobs.get(unfinished)["stage"], "writing")
            jobs.finish(completed, "completed", outcome="verified", run_directory="run-demo")
            Jobs(Path(directory)).initialise()
            self.assertEqual(jobs.get(completed)["run_directory"], "run-demo")
            self.assertEqual(jobs.get(unfinished)["status"], "interrupted")
            self.assertFalse(jobs.start(unfinished))

    def test_queue_has_fixed_pending_capacity(self):
        with TemporaryDirectory() as directory:
            jobs = Jobs(Path(directory))
            jobs.initialise()
            for _ in range(8):
                jobs.enqueue(Request("C-demo", "renewal"))
            with self.assertRaises(QueueFull):
                jobs.enqueue(Request("C-demo", "renewal"))
