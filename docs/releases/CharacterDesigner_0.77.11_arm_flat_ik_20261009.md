# Character Designer 0.77.11 — Arm Flat IK continuation

Applying the existing Arm Flat asset while using Hand IK reproduced its shallow
downward FK elbow plane. Moving either hand afterwards bent the elbow downwards.
The real asset now explicitly opts into stable IK bend matching with its verified,
side-specific source reference frames. The authored wrist stays matched while
the shallow arm pose receives a bounded correction, so subsequent IK movement
keeps the elbows bending backwards. Existing Pole angles remain unchanged.

Unkeyed, unconstrained native descendants of the tagged arm chains that already
evaluate to the desired matrix are no longer reassigned that matrix. This keeps
the existing connected Fist fingers' dormant location and other artist channels.
Driver refusal and final matrix verification remain active. Ordinary assets,
FK/BLEND geometry, deeply bent poses and Original retain exact matching.

Validation uses isolated Blender 5.2.0 LTS processes, disabled autoexec and two
threads. Fifteen focused regression tests passed, including normal/mirrored Fist
raw channels and independent wrist-following checks. Seven full-character cases
passed on the latest unsaved-scene backup, covering both IK and FK sides,
Original, ordinary exact matching and atomic both-arm application. Retractions
of 2%, 15% and 30%, including after clearing visible controls, retain posterior
bend dots above 0.97. The existing capture and pose/weight suites passed 27 and
14 cases respectively during development.

The release is a 152-file projection of shipped 0.77.9; only
`control_pose_assets.py` and the version change. Projection SHA256:
`199b46ab6a5972b426a1daf3be9df03de4903cc270b294da20995523e59dea22`.
The ZIP was built and deployed to Blender 5.2 and X/addons; both `--check`
results report zero differing files. Held Dress/Cloth and unrelated RR changes
were excluded. No commit, push or public release was performed.

The current artist scene was backed up, the add-on refreshed to 0.77.11,
Arm Flat metadata saved, and both arms applied atomically in IK. Native saving
reported FINISHED; the original editor layout was restored and saved again.
Rest, mesh geometry, weights, Shape Keys, Action curves, Pole angles and all
unrelated raw pose channels passed the live protection checks. A prior 0.77.10
attempt was rolled back without saving after detecting connected-finger channel
changes. No task-owned background Blender remains from completed tests.

Evidence and backup: `D:\Blender\Projects\Character\X\validation\arm_flat_fix_20261009`.
See `README.md`, `backup_receipt.json`, `live_fix_receipt.json` and native logs.
Isolated tests do not verify actual Asset Browser mouse events or native Undo.

Final read-only native reopen validation also passed: saved metadata and six
reference source frames, protected content, unrelated raw channels and
Thirty-six retraction/control-reset checks. Both artist and protected backup
file hashes remained unchanged, and the background process exited 0.
