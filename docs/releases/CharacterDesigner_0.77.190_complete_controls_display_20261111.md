# Character Designer 0.77.190 — complete painting Controls display

Source and isolated native validation are complete. Packaging, disk deployment
and the artist update are pending Root review; no delivery or scene-save claim
is made by this candidate note.

The Weight page previously inferred its entire posing display from the painted
mesh's Deform groups. Missing groups could leave leg, Foot Roll/Toe and Hair
bones hidden, and Finish retained that filtered display. Controls and Finish now
use the existing normal registered Body display/current IK/FK policy. The strict
resolver supplies selection hints only; unresolved hints remain visible in the
UI and Finish result instead of limiting which public Body controls are shown.

New trusted sessions preserve foreign Hair/Dress eyes, bone/Pose hide flags and
selection locks. Custom shapes are enabled for Controls. Paused Object-to-Controls
re-entry keeps fresh foreign display choices. The exact existing owned Foot/Wrist
mechanism exclusions run last, including when a mechanism also belongs to a
foreign public collection. No name-based bone classification, collection
membership rewrite, IK/FK switch, reweight or recovery authority is added.

An explicit Controls request can repair a legacy whole hidden public cohort with
its eye On. Eyes Off, partial hides and known locks remain. Cleared legacy blanket
selection masks are disclosed as a reset with unknown prior intent. The original
display checkpoint and pose-baseline objects remain intact. Finish keeps newly
painted weights and restores the captured raw pose/frame/AutoKey through the
existing author and rollback checks; visibility repair alone does not call Finish
or restore a historical pose.

Complete approved189 projection `481dac4d4a1ef35fec62a42e3abd5f7709c942d86c71d000e0917c510610446b`
plus4 changed files: `weight_paint_entry.py`, `body_original_mode.py`, `__init__.py`
and `README.md`. Total270 files/238 Python,266 exact inherited files. All5 paused
Rig-provider changes in canonical are excluded from this release candidate.

Official canonical-based projection:
`D:/Blender/Projects/Character/X/validation/weight_controls_visibility_20261111/stage190_02/projection.json`
SHA256 `8fd37eed3debceefee1ffa1843e0cc59baa11b28d78e954900450a352cb462bd`.

The exact2 runtime files passed10 generated native cases in isolated stage05:
5 modern transitions/Finish/fault rollback,3 contaminated legacy checkpoints,
and2 edge cases for mechanism exclusions and fresh paused display choices.
The tests include full Foot controls/current mixed IK/FK modes, known foreign
hides/locks, custom shapes, newly edited weights and exact raw pose/frame/AutoKey
restoration. Genuine unresolved selection diagnostics survive preview and Finish.

Final native receipt:
`D:/Blender/Projects/Character/X/validation/weight_controls_visibility_20261111/native_final05_01.result.json`
SHA256 `576ef3e38cbd47aef736a1d71a227bbb68e719b8b5bdaa46c770662346ec9d9f`.
Owned PID45688 exited normally; no stderr/cleanup errors. Source270 hashes and
loaded module paths remained exact. Earlier native RNA-wrapper identity failure,
diagnostic receipt, private stages and the pre-output CRLF preparation failure
are retained. Foreign Hair/Dress fixtures prove display behavior, not full artist
Hair/Dress topology or solver acceptance. The normal-stage tests do not by
themselves prove active-session hotpatch continuity.

Canonical4 apply used exact fresh189 pins and exclusive writer guards, with old
bytes backed up; all5 paused canonical source hashes remained exact. No build,
deployment, live code commit, artist action, scene/preferences save, Unity action
or Git upload occurred in this preparation. Fresh artist/session/source/Rest/
weights protection and an explicit Controls action remain separate delivery gates.
