"""Take one bounded, content-free snapshot of a local Codex rollout.

Examples (no monitoring, sleeping, SDK access, or model calls)::

    python tools/workflow_speed_snapshot.py THREAD_UUID --sessions-dir DAY_DIR
    python tools/workflow_speed_snapshot.py THREAD_UUID --rollout FILE --cursor STATE

The default sessions directory is today's local ~/.codex/sessions/YYYY/MM/DD;
use --sessions-dir or --rollout for older sessions. Only a single directory is
searched. --cursor explicitly opts into creating/updating a small local state
file; omit it for a read-only snapshot. Initial reads start at the bounded tail.
An incremental read continues from its cursor, so a backlog may need another
invocation. Counts/durations apply only to newly scanned records in the time
window; previous last markers are carried separately in the cursor.

Output deliberately excludes source paths, messages, commands, arguments,
stdout, error text, and reasoning content. Marker identifiers use a restrictive
format or a hash. Gaps have no inferred cause. Background launcher durations
are command durations, never background-job durations. Fingerprints detect
boundary edits/replacement/truncation, not arbitrary edits to all old history.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time


SCHEMA = "workflow-speed-snapshot-v1"
CURSOR_SCHEMA = "workflow-speed-cursor-v1"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
MARKER_RE = re.compile(rf"(?:exec-{UUID}|fco_{UUID}|subagent-completed-{UUID}|call_[0-9a-f]{{24,40}})\Z")
TOOL_TYPES = {"CommandExecution", "McpToolCall", "Extension", "FileChange",
              "FunctionCallOutput", "SubAgentActivity"}
SAFE_TOOLS = {"exec_command", "write_stdin", "wait_threads", "read_thread",
              "send_message_to_thread", "sleep", "clock.sleep", "js",
              "apply_patch", "spawn_agent", "wait_agent", "send_message",
              "followup_task", "list_agents", "get_worktree_creation_status"}
WAIT_TOOLS = {"wait_threads", "wait_agent", "sleep", "clock.sleep"}
MESSAGE_TOOLS = {"send_message_to_thread", "send_message", "followup_task"}
SAFE_EVENTS = {"event_msg", "response_item", "turn_context", "session_meta",
               "token_usage_record", "world_state", "compacted",
               "inter_agent_communication_metadata"}
SAFE_SUBTYPES = {"item_completed", "token_count", "task_started", "task_complete",
                 "reasoning", "message", "function_call", "function_call_output",
                 "custom_tool_call", "custom_tool_call_output", "agent_message",
                 "thread_settings_applied", "context_compacted"}
STATUSES = {"completed", "failed", "in_progress", "running", "cancelled"}
FINGERPRINT_BYTES = 256
LIMITS = {
    "max_bytes": (128, 8 * 1024 * 1024),
    "max_lines": (1, 20000),
    "max_record_bytes": (64, 1024 * 1024),
    "window_seconds": (1, 86400),
    "scan_seconds": (0.001, 10),
    "max_markers": (1, 100),
    "long_tool_seconds": (0, 86400),
    "gap_seconds": (0, 86400),
}


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        return stamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _iso(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _safe_id(value):
    if isinstance(value, str) and (MARKER_RE.fullmatch(value) or re.fullmatch(r"sha256:[0-9a-f]{16}", value)):
        return value
    return "sha256:" + _hash(str(value).encode("utf-8", errors="replace"))[:16]


def _duration(payload):
    start, end = payload.get("started_at_ms"), payload.get("completed_at_ms")
    if type(start) is int and type(end) is int and 0 <= start <= end:
        return end - start
    return None


def _marker(payload, timestamp):
    item = payload.get("item")
    if not isinstance(item, dict) or not isinstance(item.get("type"), str) or item["type"] not in TOOL_TYPES:
        return None
    kind = item["type"]
    raw_tool = item.get("tool", item.get("name", item.get("kind")))
    tool = raw_tool if isinstance(raw_tool, str) and raw_tool in SAFE_TOOLS else "other_tool"
    operation = "tool_execution"
    if tool in WAIT_TOOLS:
        operation = "coordination_wait"
    elif tool in MESSAGE_TOOLS:
        operation = "coordination_message"
    elif kind == "FileChange":
        operation = "file_change"
    elif kind == "SubAgentActivity":
        operation = "subagent_event"
    if kind == "CommandExecution":
        command = item.get("command", "")
        if isinstance(command, list):
            command = " ".join(part for part in command if isinstance(part, str))
        if isinstance(command, str) and (
            ("run_trusted_blender_validation.py" in command and "--background" in command)
            or re.search(r"\bStart-Process\b", command, flags=re.IGNORECASE)
        ):
            operation = "background_launch"
    result = {"timestamp_utc": _iso(timestamp), "marker_id": _safe_id(item.get("id")),
              "type": kind, "tool": tool, "operation": operation,
              "status": item.get("status") if isinstance(item.get("status"), str) and item["status"] in STATUSES else "unrecorded",
              "recorded_duration_ms": _duration(payload),
              "duration_scope": "launcher_command_only" if operation == "background_launch" else "recorded_item_span"}
    if operation == "background_launch":
        result["background_job_duration_ms"] = None
        result["background_job_completion"] = "not_inferred"
    return result


def _clean_saved_marker(value):
    """Cursor input is untrusted too; rebuild only the output allowlist."""
    if not isinstance(value, dict) or not _timestamp(value.get("timestamp_utc")):
        return None
    if not isinstance(value.get("type"), str) or value["type"] not in TOOL_TYPES:
        return None
    allowed_ops = {"tool_execution", "coordination_wait", "coordination_message",
                   "file_change", "subagent_event", "background_launch"}
    operation = value.get("operation")
    if not isinstance(operation, str) or operation not in allowed_ops:
        return None
    duration = value.get("recorded_duration_ms")
    result = {"timestamp_utc": _iso(_timestamp(value["timestamp_utc"])),
              "marker_id": _safe_id(value.get("marker_id")), "type": value["type"],
              "tool": value.get("tool") if isinstance(value.get("tool"), str) and value["tool"] in SAFE_TOOLS else "other_tool",
              "operation": operation, "status": value.get("status") if isinstance(value.get("status"), str) and value["status"] in STATUSES else "unrecorded",
              "recorded_duration_ms": duration if type(duration) is int and duration >= 0 else None,
              "duration_scope": "launcher_command_only" if operation == "background_launch" else "recorded_item_span"}
    if operation == "background_launch":
        result.update(background_job_duration_ms=None, background_job_completion="not_inferred")
    return result


def _latest(current, candidate):
    if candidate and (not current or candidate["timestamp_utc"] >= current["timestamp_utc"]):
        return candidate
    return current


def _fingerprint(stream, start, size):
    stream.seek(start)
    return _hash(stream.read(size))


def find_rollout(thread_id, sessions_dir, deadline):
    """Single-directory lookup with a count/time bound; never scan session history."""
    matches = []
    with os.scandir(sessions_dir) as entries:
        for count, entry in enumerate(entries, 1):
            if count > 4096 or time.monotonic() >= deadline:
                raise ValueError("discovery_limit")
            if entry.name.startswith("rollout-") and entry.name.endswith("-" + thread_id + ".jsonl") and entry.is_file():
                matches.append((entry.stat().st_mtime_ns, entry.name, Path(entry.path)))
    if not matches:
        raise ValueError("rollout_not_found")
    return max(matches)[2]


def snapshot(thread_id, rollout, *, cursor=None, now=None, max_bytes=1024 * 1024,
             max_lines=2000, max_record_bytes=256 * 1024, window_seconds=3600,
             scan_seconds=2.0, max_markers=12, long_tool_seconds=30, gap_seconds=60):
    """Return (safe_report, next_cursor); bounded reads, no source writes.

    Content reads are <= max_bytes. Cursor checks/additional boundary reads are
    bounded by 5 * FINGERPRINT_BYTES + 1 bytes, reported separately.
    """
    options = {key: value for key, value in locals().copy().items() if key in LIMITS}
    for key, value in options.items():
        low, high = LIMITS[key]
        if type(value) not in (int, float) or not low <= value <= high:
            raise ValueError("invalid_bound")
        if key in {"max_bytes", "max_lines", "max_record_bytes", "max_markers"} and type(value) is not int:
            raise ValueError("invalid_bound")
    if max_record_bytes > max_bytes:
        raise ValueError("record_limit_exceeds_byte_limit")
    if not isinstance(thread_id, str) or not re.fullmatch(UUID, thread_id):
        raise ValueError("invalid_thread_id")
    path = Path(rollout)
    if not path.name.endswith("-" + thread_id + ".jsonl"):
        raise ValueError("rollout_thread_filename_mismatch")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timezone_required")
    now = now.astimezone(timezone.utc)
    cutoff = now - timedelta(seconds=window_seconds)
    deadline = time.monotonic() + scan_seconds
    source_id = _hash(str(path.resolve()).encode("utf-8"))
    cursor = cursor if isinstance(cursor, dict) else {}
    counts = Counter()
    duration_sums = Counter()
    markers, long_tools, gaps = [], [], []
    last_event = last_tool = last_progress = None
    previous_time = None
    fingerprint_io = 0
    reset = "initial_tail"
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        size = stat.st_size
        identity = [stat.st_dev, stat.st_ino]
        prefix_size = min(size, FINGERPRINT_BYTES)
        prefix_hash = _fingerprint(stream, 0, prefix_size)
        fingerprint_io += prefix_size
        valid_cursor = cursor.get("schema") == CURSOR_SCHEMA and cursor.get("thread_id") == thread_id
        if valid_cursor:
            offset = cursor.get("offset")
            if cursor.get("source_id_sha256") != source_id:
                reset = "source_changed"
            elif cursor.get("file_identity") != identity:
                reset = "file_replaced"
            elif type(offset) is not int or offset < 0 or type(cursor.get("file_size")) is not int:
                reset = "invalid_cursor"
            elif size < cursor["file_size"] or offset > size:
                reset = "file_truncated"
            elif size == cursor["file_size"] and stat.st_mtime_ns != cursor.get("mtime_ns"):
                reset = "same_size_file_changed"
            else:
                old_prefix_size = cursor.get("prefix_size")
                anchor_start, anchor_size = cursor.get("anchor_start"), cursor.get("anchor_size")
                checks_valid = (type(old_prefix_size) is int and 0 <= old_prefix_size <= FINGERPRINT_BYTES
                                and type(anchor_start) is int and 0 <= anchor_start <= offset
                                and type(anchor_size) is int and 0 <= anchor_size <= FINGERPRINT_BYTES
                                and anchor_start + anchor_size <= offset)
                if checks_valid:
                    prefix_unchanged = _fingerprint(stream, 0, old_prefix_size) == cursor.get("prefix_sha256")
                    anchor_unchanged = _fingerprint(stream, anchor_start, anchor_size) == cursor.get("anchor_sha256")
                    fingerprint_io += old_prefix_size + anchor_size
                    reset = None if prefix_unchanged and anchor_unchanged else "boundary_changed"
                else:
                    reset = "invalid_cursor"
        elif cursor:
            reset = "invalid_cursor"
        if reset is None:
            start = cursor["offset"]
            discard = cursor.get("discard_until_newline") is True
            last_tool = _clean_saved_marker(cursor.get("last_tool_marker"))
            last_progress = _clean_saved_marker(cursor.get("last_progress_marker"))
            previous_time = _timestamp(cursor.get("previous_event_timestamp_utc"))
            if previous_time and previous_time < cutoff:
                previous_time = None
        else:
            start = max(0, size - max_bytes)
            discard = False
            if start:
                stream.seek(start - 1)
                discard = stream.read(1) != b"\n"
                fingerprint_io += 1
        stream.seek(start)
        data = stream.read(min(max_bytes, size - start))
        consumed = 0
        stop_reason = "snapshot_end"
        while consumed < len(data):
            if counts["scanned_lines"] >= max_lines:
                stop_reason = "line_limit"
                break
            if time.monotonic() >= deadline:
                stop_reason = "time_limit"
                break
            newline = data.find(b"\n", consumed)
            if newline < 0:
                if discard or len(data) - consumed > max_record_bytes:
                    counts["oversized_or_partial_line_chunks_skipped"] += 1
                    consumed = len(data)
                    discard = True
                    stop_reason = "discarding_long_line"
                else:
                    counts["pending_partial_lines"] += 1
                    stop_reason = "pending_partial_line"
                break
            length = newline + 1 - consumed
            counts["scanned_lines"] += 1
            if discard or length > max_record_bytes:
                counts["oversized_or_partial_lines_skipped"] += 1
                consumed = newline + 1
                discard = False
                continue
            raw = data[consumed:newline + 1]
            consumed = newline + 1
            try:
                row = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError):
                counts["invalid_json_lines"] += 1
                continue
            if not isinstance(row, dict):
                counts["invalid_json_lines"] += 1
                continue
            stamp = _timestamp(row.get("timestamp"))
            if not stamp:
                counts["invalid_timestamp_lines"] += 1
                continue
            if stamp < cutoff or stamp > now:
                counts["outside_time_window"] += 1
                continue
            counts["events_in_window"] += 1
            if previous_time and stamp >= previous_time:
                gap = (stamp - previous_time).total_seconds()
                if gap >= gap_seconds:
                    gaps.append({"from_utc": _iso(previous_time), "to_utc": _iso(stamp),
                                 "seconds": round(gap, 3), "attribution": "unattributed"})
                    gaps = sorted(gaps, key=lambda value: value["seconds"], reverse=True)[:max_markers]
            elif previous_time:
                counts["timestamp_regressions"] += 1
            previous_time = stamp
            kind = row.get("type")
            payload = row.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            event = {"timestamp_utc": _iso(stamp), "type": kind if isinstance(kind, str) and kind in SAFE_EVENTS else "other_event",
                     "subtype": payload.get("type") if isinstance(payload.get("type"), str) and payload["type"] in SAFE_SUBTYPES else "other"}
            last_event = _latest(last_event, event)
            if kind != "event_msg" or payload.get("type") != "item_completed":
                continue
            item = payload.get("item")
            if not isinstance(item, dict):
                continue
            if item.get("type") == "Reasoning":
                counts["recorded_reasoning_spans"] += 1
                duration = _duration(payload)
                if duration is not None:
                    duration_sums["recorded_reasoning_span_ms"] += duration
                continue
            marker = _marker(payload, stamp)
            if not marker:
                continue
            counts["tool_markers"] += 1
            counts[marker["operation"]] += 1
            if marker["status"] == "failed":
                counts["failed_status_markers"] += 1
            duration = marker["recorded_duration_ms"]
            if duration is not None:
                duration_sums[marker["operation"] + "_ms"] += duration
            markers.append(marker)
            markers = markers[-max_markers:]
            last_tool = _latest(last_tool, marker)
            if marker["operation"] not in {"coordination_wait", "coordination_message", "subagent_event"}:
                last_progress = _latest(last_progress, marker)
            if marker["status"] == "completed" and duration is not None and duration >= long_tool_seconds * 1000:
                long_tools.append(marker)
                long_tools = sorted(long_tools, key=lambda value: value["recorded_duration_ms"], reverse=True)[:max_markers]
            gaps = sorted(gaps, key=lambda value: value["seconds"], reverse=True)[:max_markers]
        offset = start + consumed
        if offset < size and stop_reason == "snapshot_end":
            stop_reason = "byte_limit"
        anchor_size = min(offset, FINGERPRINT_BYTES)
        anchor_start = offset - anchor_size
        anchor_hash = _fingerprint(stream, anchor_start, anchor_size)
        fingerprint_io += anchor_size
    for key in ("scanned_lines", "events_in_window", "tool_markers"):
        counts[key] += 0
    report = {"schema": SCHEMA, "thread_id": thread_id, "observed_utc": _iso(now),
              "window_start_utc": _iso(cutoff), "cursor_reset_reason": reset,
              "bounds": options, "source_id_sha256": source_id,
              "read": {"file_bytes_at_open": size, "start_offset": start, "next_offset": offset,
                       "content_bytes_read": len(data), "fingerprint_bytes_read": fingerprint_io,
                       "unconsumed_bytes_at_open": size - offset, "stop_reason": stop_reason},
              "counts": dict(counts), "recorded_duration_sums_ms": dict(duration_sums),
              "last_event_in_new_records": last_event, "last_tool_marker": last_tool,
              "last_progress_marker": last_progress, "recent_tool_markers": markers,
              "long_completed_tools": long_tools, "largest_event_gaps": gaps,
              "limits": ["Gaps are unattributed; they do not establish model reasoning, server delay, or a hang.",
                         "Durations are recorded item spans and may overlap; they are not an exclusive time budget.",
                         "A background launch marker does not establish background-job completion or duration.",
                         "Progress markers identify recorded tools, not semantic task acceptance.",
                         "Bounded fingerprints do not verify arbitrary edits to all earlier history."]}
    next_cursor = {"schema": CURSOR_SCHEMA, "thread_id": thread_id, "source_id_sha256": source_id,
                   "file_identity": identity, "file_size": size, "mtime_ns": stat.st_mtime_ns,
                   "offset": offset, "discard_until_newline": discard,
                   "prefix_size": prefix_size, "prefix_sha256": prefix_hash,
                   "anchor_start": anchor_start, "anchor_size": anchor_size, "anchor_sha256": anchor_hash,
                   "previous_event_timestamp_utc": _iso(previous_time) if previous_time else None,
                   "last_tool_marker": last_tool, "last_progress_marker": last_progress}
    return report, next_cursor


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("thread_id", help="Exact lower-case thread UUID.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--rollout", type=Path, help="Exact rollout file ending in -THREAD_UUID.jsonl.")
    source.add_argument("--sessions-dir", type=Path, help="One day directory to search; no recursion.")
    parser.add_argument("--cursor", type=Path, help="Explicitly read/update a small incremental cursor.")
    parser.add_argument("--max-bytes", type=int, default=1024 * 1024, help="Content byte budget; fingerprint reads add at most 1281 bytes.")
    parser.add_argument("--max-lines", type=int, default=2000)
    parser.add_argument("--max-record-bytes", type=int, default=256 * 1024)
    parser.add_argument("--window-seconds", type=float, default=3600)
    parser.add_argument("--scan-seconds", type=float, default=2.0, help="Cooperative scan deadline (0.001 to 10 seconds).")
    parser.add_argument("--max-markers", type=int, default=12)
    parser.add_argument("--long-tool-seconds", type=float, default=30)
    parser.add_argument("--gap-seconds", type=float, default=60)
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(UUID, args.thread_id):
            raise ValueError("invalid_thread_id")
        cursor = None
        if args.cursor and args.cursor.exists():
            if args.cursor.stat().st_size > 65536:
                raise ValueError("cursor_too_large")
            cursor = json.loads(args.cursor.read_bytes())
        rollout = args.rollout
        if rollout is None:
            day_dir = args.sessions_dir or (Path.home() / ".codex" / "sessions" / datetime.now().strftime("%Y/%m/%d"))
            rollout = find_rollout(args.thread_id, day_dir, time.monotonic() + 2)
        report, next_cursor = snapshot(args.thread_id, rollout, cursor=cursor,
                                      **{key: getattr(args, key) for key in LIMITS})
        if args.cursor:
            if args.cursor.resolve() == rollout.resolve():
                raise ValueError("cursor_must_not_be_source")
            temporary = args.cursor.with_name(args.cursor.name + ".tmp")
            # Exclusive creation avoids clobbering another invocation's temporary.
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(next_cursor, stream, ensure_ascii=True, indent=2)
                stream.write("\n")
            os.replace(temporary, args.cursor)
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError, TypeError, RecursionError) as error:
        code = str(error) if isinstance(error, ValueError) and str(error) in {
            "invalid_bound", "record_limit_exceeds_byte_limit", "invalid_thread_id", "timezone_required",
            "rollout_thread_filename_mismatch", "discovery_limit", "rollout_not_found",
            "cursor_too_large", "cursor_must_not_be_source"} else "snapshot_input_error"
        print(json.dumps({"schema": SCHEMA, "ok": False, "error_code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
