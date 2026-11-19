import asyncio
import unittest

from agent.command_output import StderrTail, failure_message, success_summary


class CommandOutputTests(unittest.TestCase):
    def test_lock_busy_explains_the_conflict_and_shows_engine_line(self):
        message = failure_message(75, ["saad-deploy: deployment lock is busy for atlant (waited 600 seconds)"], app_id="atlant")
        self.assertTrue(message.startswith("deployment lock is busy for atlant"))
        self.assertIn("Deployment lock busy for atlant (exit code 75)", message)

    def test_unknown_exit_without_output(self):
        self.assertEqual(failure_message(3, []), "Operation failed (exit code 3).")

    def test_secrets_are_redacted(self):
        message = failure_message(1, [
            "fatal: https://x-access-token:ghs_abcdefghijklmnopqrstuvwxyz0123@github.com/o/r",
            "GITHUB_TOKEN=github_pat_abcdefghijklmnopqrstuvwxyz",
            "Authorization: Bearer abcdefghijklmnop",
        ])
        self.assertNotIn("ghs_abcdefghijklmnopqrstuvwxyz0123", message)
        self.assertNotIn("github_pat_abcdefghijklmnopqrstuvwxyz", message)
        self.assertNotIn("abcdefghijklmnop", message.replace("[redacted]", ""))

    def test_timeout_message(self):
        self.assertIn("within 30 minutes", failure_message(None, ["building"], timeout_minutes=30))

    def test_success_summary_uses_last_engine_line(self):
        self.assertEqual(success_summary(["noise", "saad-deploy: atlant already runs abc1234; nothing to deploy"]), "atlant already runs abc1234; nothing to deploy")
        self.assertEqual(success_summary([]), "Operation completed.")

    def test_tail_is_bounded_and_drains(self):
        async def run():
            reader = asyncio.StreamReader()
            reader.feed_data(b"x" * 100_000 + b"\nsaad-deploy: done\n")
            reader.feed_eof()
            tail = StderrTail()
            await tail.drain(reader)
            return tail.lines()

        lines = asyncio.run(run())
        self.assertEqual(lines[-1], "saad-deploy: done")
        self.assertLess(sum(len(line) for line in lines), 20_000)
