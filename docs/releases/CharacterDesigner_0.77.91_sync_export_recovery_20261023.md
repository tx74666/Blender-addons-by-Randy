# Character Designer 0.77.91 — sync checks and export recovery

Local build, both215-file official deployment checks and the protected current
artist update are complete. X's `validation/sync_preflight_20261023/README091.md`
and `acceptance_summary091.json` record the loaded .91 runtime, three exact
author-state snapshots and normal temporary SDK cleanup. Frame73/Object/POSE,
selection and the latest dirty edits were retained. The formal artist file and
user preferences were not overwritten; no full reload was used.

Sync confirmation previously fingerprinted every Custom animation in its fresh
plan and immediately repeated that scan in a rollback snapshot which never
consumed the result. It now retains the complete fresh confirmation proof and
omits only the unused rollback proof. Preview plus confirmation makes two scans
instead of three; confirmation makes one instead of two. Default snapshot and
plan token semantics remain identical. This is a scan-count reduction, not a
measured complete Sync or GUI speedup.

Restoring a retired animation validates both saved Action slots before changing
membership. Invalid or missing Source/Custom slots leave the retired row and
active list intact. New local Actions without Source remain supported.

Export startup can launch its worker before the completion poll is registered.
If that registration fails, the newly owned worker and Link lock are disposed so
retry remains possible, while existing foreign polls and owners are preserved.
Ordinary, linked, worklist, new-Action and Composition paths are covered.
The generic completion poll now ignores Composition jobs before reading their
owner or polling/cancelling/publishing them, keeping Composition receipt checks
on its own service path even after partial timer-cleanup failures.

Validation includes273 relevant pure tests,12 Blender5.2 reconciliation fixture
checks, and the independent export native lifecycle checks. The cross-poll
regression failed before the one-line job-kind correction and passes afterward.
The full approved package retains215 files: seven changed,208 identical to .89,
including approved body_region SHA `09543e30a254824919c14e0ddab96ed0c7ab5ef79329651dfe24668b0b8ab101`.
The complete .91 projection SHA is
`a5241e97d8e7efd320ae38685aba73ddd7ad533c9d1ccf17dd9d3306d7284689`.

The built .90 candidate was withheld before any deployment after independent
review found the cross-poll edge. Its frozen inputs, archive and failure evidence
remain unchanged. The corrected package uses .91; it does not overwrite .90.

No new complete Sync time, GUI FPS, model deformation, Unity integration or
hardware performance claim is made. The independent bulk snapshot fixture is
validation-tool research and is not part of this package or its accepted guard.
No Git commit or push is included. See the final X README/acceptance receipt for
actual build, deployment, runtime, dirty-state protection and SDK cleanup status.
