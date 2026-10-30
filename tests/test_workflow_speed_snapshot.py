"""Bounded one-shot rollout summaries: no Blender, network, or model access."""

from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import workflow_speed_snapshot as workflow


THREAD = "01a162b4-6137-7171-831f-a44bf1f14628"
NOW = datetime(2026, 10, 22, 13, 0, tzinfo=timezone.utc)
SECRET = "sk-private-bearer-password-should-never-appear"


def event(minute=0, second=0, *, kind="CommandExecution", marker=1,
          duration=100, status="completed", **fields):
    return {"timestamp": f"2026-10-22T12:{minute:02d}:{second:02d}Z",
            "type": "event_msg", "payload": {
                "type": "item_completed", "started_at_ms": 100000,
                "completed_at_ms": 100000 + duration,
                "item": {"type": kind,
                         "id": f"exec-00000000-0000-0000-0000-{marker:012d}",
                         "status": status, **fields}}}


def encoded(row):
    return json.dumps(row).encode("utf-8") + b"\n"


class WorkflowSpeedSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="workflow-snapshot-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.rollout = self.folder / f"rollout-2026-10-22T00-00-00-{THREAD}.jsonl"

    def write(self, *rows):
        self.rollout.write_bytes(b"".join(encoded(row) for row in rows))

    def snapshot(self, **options):
        return workflow.snapshot(THREAD, self.rollout, now=NOW, **options)

    def test_long_tool_background_launch_and_reasoning_are_distinct(self):
        self.write(event(duration=145106), event(1, kind="Reasoning", duration=5200, raw_content=SECRET),
                   event(2, marker=2, duration=1186, command=["python", "run_trusted_blender_validation.py", "--background"]),
                   event(3, kind="McpToolCall", marker=3, duration=20004, tool="wait_threads"))
        report, _ = self.snapshot()
        self.assertEqual(report["counts"]["recorded_reasoning_spans"], 1)
        self.assertEqual(report["recorded_duration_sums_ms"]["recorded_reasoning_span_ms"], 5200)
        self.assertEqual(len(report["long_completed_tools"]), 1)
        self.assertEqual(report["long_completed_tools"][0]["recorded_duration_ms"], 145106)
        self.assertEqual(report["last_tool_marker"]["operation"], "coordination_wait")
        progress = report["last_progress_marker"]
        self.assertEqual(progress["operation"], "background_launch")
        self.assertEqual(progress["duration_scope"], "launcher_command_only")
        self.assertIsNone(progress["background_job_duration_ms"])
        self.assertEqual(progress["background_job_completion"], "not_inferred")
        self.assertTrue(all(gap["attribution"] == "unattributed" for gap in report["largest_event_gaps"]))
        self.assertNotIn(SECRET, json.dumps(report))

    def test_output_and_cursor_allowlist_remove_sensitive_source_fields(self):
        row = event(tool=SECRET, command=[SECRET], aggregated_output=SECRET,
                    arguments={"api_key": SECRET}, error=SECRET)
        row["payload"]["item"]["id"] = SECRET
        row["other_private_field"] = SECRET
        self.write(row)
        report, cursor = self.snapshot()
        self.assertNotIn(SECRET, json.dumps([report, cursor]))
        self.assertNotIn(str(self.folder), json.dumps([report, cursor]))
        self.assertTrue(report["last_tool_marker"]["marker_id"].startswith("sha256:"))
        self.assertEqual(report["last_tool_marker"]["tool"], "other_tool")
        cursor["last_tool_marker"]["untrusted_extra"] = SECRET
        cursor["last_progress_marker"]["tool"] = SECRET
        again, next_cursor = self.snapshot(cursor=cursor)
        self.assertNotIn(SECRET, json.dumps([again, next_cursor]))
        self.assertEqual(again["last_tool_marker"]["marker_id"], report["last_tool_marker"]["marker_id"])
        self.assertEqual(again["read"]["content_bytes_read"], 0)

    def test_half_json_line_is_retried_once_appended(self):
        line = encoded(event())
        halfway = len(line) // 2
        self.rollout.write_bytes(line[:halfway])
        first, cursor = self.snapshot()
        self.assertEqual(first["counts"]["events_in_window"], 0)
        self.assertEqual(cursor["offset"], 0)
        self.assertEqual(first["read"]["stop_reason"], "pending_partial_line")
        with self.rollout.open("ab") as stream:
            stream.write(line[halfway:])
        second, cursor = self.snapshot(cursor=cursor)
        self.assertIsNone(second["cursor_reset_reason"])
        self.assertEqual(second["counts"]["events_in_window"], 1)
        self.assertEqual(cursor["offset"], len(line))
        third, _ = self.snapshot(cursor=cursor)
        self.assertEqual(third["counts"]["tool_markers"], 0)

    def test_initial_tail_skips_multi_megabyte_line_and_recovers_next_marker(self):
        self.rollout.write_bytes(b"x" * (2 * 1024 * 1024) + b"\n" + encoded(event()))
        report, cursor = self.snapshot(max_bytes=4096, max_record_bytes=2048)
        self.assertEqual(report["counts"]["tool_markers"], 1)
        self.assertEqual(report["counts"]["oversized_or_partial_lines_skipped"], 1)
        self.assertLessEqual(report["read"]["content_bytes_read"], 4096)
        self.assertLessEqual(report["read"]["fingerprint_bytes_read"], 1281)
        self.assertEqual(cursor["offset"], self.rollout.stat().st_size)

    def test_incremental_oversized_unterminated_line_advances_without_reread_loop(self):
        self.rollout.write_bytes(b"{")
        _, cursor = self.snapshot(max_bytes=512, max_record_bytes=400)
        with self.rollout.open("ab") as stream:
            stream.write(b"x" * 12000 + b"\n" + encoded(event()))
        total_markers = 0
        previous_offset = -1
        for _ in range(30):
            report, cursor = self.snapshot(cursor=cursor, max_bytes=512, max_record_bytes=400)
            self.assertGreater(cursor["offset"], previous_offset)
            self.assertLessEqual(report["read"]["content_bytes_read"], 512)
            previous_offset = cursor["offset"]
            total_markers += report["counts"]["tool_markers"]
            if cursor["offset"] == self.rollout.stat().st_size:
                break
        else:
            self.fail("oversized line did not recover within the bounded invocation count")
        self.assertEqual(total_markers, 1)
        self.assertFalse(cursor["discard_until_newline"])

    def test_line_bound_and_cursor_process_each_marker_once(self):
        self.write(*(event(marker=index) for index in range(6)))
        cursor, seen = None, []
        for _ in range(3):
            report, cursor = self.snapshot(cursor=cursor, max_lines=2)
            self.assertEqual(report["counts"]["scanned_lines"], 2)
            seen.extend(marker["marker_id"] for marker in report["recent_tool_markers"])
        self.assertEqual(len(set(seen)), 6)
        self.assertEqual(cursor["offset"], self.rollout.stat().st_size)

    def test_time_budget_stops_without_consuming_a_record(self):
        self.write(event())
        with mock.patch.object(workflow.time, "monotonic", side_effect=[0, 2]):
            report, cursor = self.snapshot(scan_seconds=1)
        self.assertEqual(report["read"]["stop_reason"], "time_limit")
        self.assertEqual(cursor["offset"], 0)
        self.assertEqual(report["counts"]["scanned_lines"], 0)

    def test_time_window_ignores_old_and_future_events(self):
        old = event()
        old["timestamp"] = "2026-10-22T11:59:59Z"
        future = event()
        future["timestamp"] = "2026-10-22T13:00:01Z"
        self.write(old, event(59), future)
        report, _ = self.snapshot(window_seconds=3600)
        self.assertEqual(report["counts"]["outside_time_window"], 2)
        self.assertEqual(report["counts"]["tool_markers"], 1)

    def test_same_size_rewrite_resets_cursor(self):
        self.write(event(marker=1))
        _, cursor = self.snapshot()
        self.write(event(marker=2))
        self.assertEqual(self.rollout.stat().st_size, cursor["file_size"])
        os.utime(self.rollout, ns=(cursor["mtime_ns"] + 1000000, cursor["mtime_ns"] + 1000000))
        report, _ = self.snapshot(cursor=cursor)
        self.assertEqual(report["cursor_reset_reason"], "same_size_file_changed")
        self.assertTrue(report["last_tool_marker"]["marker_id"].endswith("000000000002"))

    def test_truncation_replacement_and_source_rollover_reset_cursor(self):
        self.write(event(marker=1), event(marker=2))
        _, cursor = self.snapshot()
        self.write(event(marker=3))
        report, cursor = self.snapshot(cursor=cursor)
        self.assertEqual(report["cursor_reset_reason"], "file_truncated")
        replacement = self.folder / "replacement.jsonl"
        replacement.write_bytes(encoded(event(marker=4)))
        os.replace(replacement, self.rollout)
        report, cursor = self.snapshot(cursor=cursor)
        self.assertEqual(report["cursor_reset_reason"], "file_replaced")
        rollover = self.folder / ("rollout-new-" + THREAD + ".jsonl")
        rollover.write_bytes(encoded(event(marker=5)))
        report, _ = workflow.snapshot(THREAD, rollover, cursor=cursor, now=NOW)
        self.assertEqual(report["cursor_reset_reason"], "source_changed")

    def test_rewrite_then_append_is_detected_by_bounded_anchor(self):
        self.write(*(event(marker=index) for index in range(10)))
        _, cursor = self.snapshot()
        payload = bytearray(self.rollout.read_bytes())
        payload[-10] = ord(" ")
        self.rollout.write_bytes(payload + encoded(event(marker=20)))
        report, _ = self.snapshot(cursor=cursor)
        self.assertEqual(report["cursor_reset_reason"], "boundary_changed")

    def test_malformed_shapes_never_echo_values_or_crash(self):
        malformed = event()
        malformed["type"] = {"secret": SECRET}
        malformed["payload"]["type"] = [SECRET]
        malformed_item = event()
        malformed_item["payload"]["item"]["type"] = [SECRET]
        malformed_status = event(status={"secret": SECRET})
        self.write(malformed, malformed_item, malformed_status)
        with self.rollout.open("ab") as stream:
            stream.write(b"not json " + SECRET.encode() + b"\n")
        report, _ = self.snapshot()
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertEqual(report["counts"]["invalid_json_lines"], 1)
        self.assertEqual(report["last_tool_marker"]["status"], "unrecorded")

    def test_gap_and_marker_output_are_bounded_even_without_tools(self):
        rows = [{"timestamp": f"2026-10-22T12:{minute:02d}:00Z", "type": "response_item",
                 "payload": {"type": "reasoning", "raw_content": SECRET}}
                for minute in range(30)]
        self.write(*rows)
        report, _ = self.snapshot(max_markers=3, gap_seconds=30)
        self.assertEqual(len(report["largest_event_gaps"]), 3)
        self.assertEqual(report["counts"]["tool_markers"], 0)
        self.assertNotIn(SECRET, json.dumps(report))

    def test_invalid_cursor_fields_and_secret_marker_are_cleaned(self):
        self.write(event())
        _, cursor = self.snapshot()
        cursor["anchor_start"] = SECRET
        report, _ = self.snapshot(cursor=cursor)
        self.assertEqual(report["cursor_reset_reason"], "invalid_cursor")
        self.assertNotIn(SECRET, json.dumps(report))

    def test_discovery_uses_only_matching_thread_and_has_deadline(self):
        self.write(event())
        (self.folder / "rollout-other-thread.jsonl").write_bytes(b"secret")
        found = workflow.find_rollout(THREAD, self.folder, workflow.time.monotonic() + 1)
        self.assertEqual(found, self.rollout)
        with self.assertRaisesRegex(ValueError, "discovery_limit"):
            workflow.find_rollout(THREAD, self.folder, 0)

    def test_cli_cursor_round_trip_and_source_not_overwritten(self):
        self.write(event())
        before = self.rollout.read_bytes()
        cursor = self.folder / "cursor.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = workflow.main([THREAD, "--rollout", str(self.rollout), "--cursor", str(cursor)])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(cursor.read_bytes())["schema"], workflow.CURSOR_SCHEMA)
        self.assertEqual(self.rollout.read_bytes(), before)
        with redirect_stdout(io.StringIO()):
            result = workflow.main([THREAD, "--rollout", str(self.rollout), "--cursor", str(self.rollout)])
        self.assertEqual(result, 1)
        self.assertEqual(self.rollout.read_bytes(), before)

    def test_cli_errors_do_not_echo_sensitive_path_or_exception_text(self):
        stdout = io.StringIO()
        missing = self.folder / SECRET / ("rollout-" + THREAD + ".jsonl")
        with redirect_stdout(stdout):
            result = workflow.main([THREAD, "--rollout", str(missing)])
        self.assertEqual(result, 1)
        self.assertNotIn(SECRET, stdout.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())["error_code"], "snapshot_input_error")

    def test_bounds_and_thread_mismatch_rejected(self):
        self.write(event())
        for options in ({"max_bytes": 100}, {"max_lines": 0}, {"scan_seconds": 20},
                        {"max_markers": 101}, {"max_bytes": 512, "max_record_bytes": 1024}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.snapshot(**options)
        with self.assertRaisesRegex(ValueError, "thread_filename_mismatch"):
            workflow.snapshot("00000000-0000-0000-0000-000000000000", self.rollout, now=NOW)


if __name__ == "__main__":
    unittest.main()
