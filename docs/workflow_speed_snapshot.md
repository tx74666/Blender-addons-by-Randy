# Lightweight workflow speed snapshots

Use `tools/workflow_speed_snapshot.py` for one bounded status check of a known
local Codex chat. It reads local JSONL directly and makes no model, connector,
Blender, Unity, or UI call. It does not schedule, sleep, or monitor.

```powershell
python -B tools/workflow_speed_snapshot.py THREAD_UUID --rollout "ABSOLUTE_ROLLOUT_PATH" --cursor "TASK_EVIDENCE_PATH\progress.cursor.json"
```

The cursor parent must already exist. Keep the cursor in the current task's
evidence directory. Omit `--cursor` for a read-only check. For a known day,
`--sessions-dir` can locate the matching file in that one directory.

An initial read inspects at most the last 1 MiB. Later calls read only the new
bytes since the cursor. Read/line/record/time limits and unconsumed-byte counts
are reported; oversized or incomplete records are skipped or deferred rather
than loaded without bounds. A large backlog may need another explicit
invocation. The time bound is cooperative and is not an operating-system I/O
timeout.

Prefer `last_progress_marker` and `last_tool_marker` for progress checks.
A changed marker establishes a recorded operation, not task acceptance.
Messages, commands, tool arguments, stdout, errors and reasoning content are
excluded. Durations of background launch commands do not establish the
background process duration or success. Visible event gaps remain
`unattributed`. Recorded item durations may overlap.

If the summary lacks information required for a decision, read only the
specific small evidence file or relevant chat item. Do not replace a semantic
review or an owner release/save receipt with a marker. Fingerprints cover the
file identity and bounded boundaries; they do not authenticate all earlier
history.

Verification:
```powershell
python -B -m unittest discover -s tests -p test_workflow_speed_snapshot.py
```

First local smoke on 2026-10-22 inspected Character and X in parallel. Their
source files were about 15 and 17 MB; each initial read was capped at 1 MiB and
took about 0.24 seconds including a Python subprocess. The sanitized summaries
totaled 14,845 characters, versus 763,063 characters in the earlier full
`read_thread` responses. This demonstrates reduced status-reading payload; it
does not measure model response latency or speed up an animation bake.
Evidence: `D:/Blender/Projects/Character/X/validation/workflow_speed_20261022/`.

