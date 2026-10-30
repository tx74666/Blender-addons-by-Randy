# Character Designer 0.77.126 — Test Angle with animated forearms

Edit Calibration intentionally leaves Mesh Edit Mode so the deformed sleeve can
update. The old preview detected an animated or driven hand and disabled Test
Angle entirely. Calibration could be edited only at the current animation pose.

Test Angle now adds a session-owned, evaluated rotation preview after the hand's
normal animation. It retains the entry wrist swing and applies an absolute twist
within the existing ±120° range. Author pose inputs, rotation representations,
locks, Actions, slots, NLA, drivers, Rest and weights remain unchanged. An actual
evaluated-pose check verifies each requested trial; changing the slider alone is
not accepted as success.

Trials temporarily use Pose display when entered from Rest Position and restore
the original display on completion. Confirm keeps the edited calibration profile;
Cancel restores it. Both return to the entry editing context. Existing unanimated
angle solving in Pose display is retained.

Native ID links, serialized session receipts, complete resource signatures and
constraint order authenticate the temporary helper and final rotation constraint.
Confirm, Cancel, errors, frame changes, Undo/load recovery and unregister remove
only those owned resources. Unknown edits or references preserve recovery data.
The helper's known native Mesh-data reference is accepted only after proving one
actual Mesh Object and precisely that owned reference; genuinely shared data
retains its refusal.

The complete release extends approved 125, including its selected-strand Hair
features and all approved Physics/Relax/Body code. The older unrelated Forearm
bulk-publish candidate is retained byte-for-byte under
`validation/forearm_test_angle_20261029/source_backups/` and remains excluded. The
closed playback-FPS branch is not reopened.

Focused native, GUI, preservation, rollback and package/deployment receipts are
indexed in
`D:/Blender/Projects/Character/X/validation/forearm_test_angle_20261029/acceptance_summary126.json`.
That record states the actual live-update scope separately from disk deployment.
No artist test pose, geometry edit, scene/preferences save, full reload/restart,
Unity operation or Git upload is inferred from this code fix.

Final delivery 2026-10-30: complete235/203py; official ZIP and both5.2/X deployments
match every byte. Current artist Forearm code was already126 (127 definitions and
helper/RNA links); the agent did not transplant code. The extended author-data
witness failed on unavailable Action.fcurves, so no complete artist-array claim is
made. The original failed reports, private successful updater tests, unknown
refresh provenance and all earlier failures remain in the acceptance entry.
