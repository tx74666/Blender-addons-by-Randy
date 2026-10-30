# Character Designer 0.77.127 — Hair strand list and categories

Hair Bones now lists each authenticated strand and its current segment count.
Front / Bangs, Side, Back / Long and Unassigned filters reuse saved motion
profile groups. Check rows or import a Pose selection, assign a category, or
rebuild the explicit checked chains together. Hidden checks remain counted;
empty and stale scopes refuse. Synchronized pairs require both sides checked
for category changes. Category assignment retains effective motion and depth.

Rebuild uses the approved125 selected-chain sampling and protection pipeline.
A 6/6/4 version can become 7/6/4 without changing the other chains. Original and
earlier versions remain. Rest display permits category/selection and rebuilding
retains its Pose display guard. Native Undo survives UI refresh failure.

Only hair_bones.py and hair_resegment_ui.py changed at runtime; the two new
modules are hair_strand_browser.py and hair_strand_list_ui.py. The full formally
delivered126 Forearm fix is inherited. Unrelated paused candidates are excluded.
The Hair panel also resolves its existing retained-version rig references.

Validation: 19 focused pure tests, 9 private rich native cases, and 7 native GUI
Undo/Redo histories passed. Real UILayout drew original, mixed-count version
and Rest display, with screenshots inspected. Rich native checks retain current
paint, weights, Action slots/NLA, drivers and constraints; categories persist
through private save/reopen. Source and family refusal inputs are tested; family
active/recovery adapter states are injected, not actual solver transitions.
Early fixture/harness failures and pinned derivatives are retained.

Evidence: D:/Blender/Projects/Character/X/validation/hair_strand_browser_20261029/
validation_handoff_browser01.json. Final package/deployment evidence is recorded
in acceptance_summary127.json after official delivery. No artist counts, live
code, scene/preferences, Unity or Git upload are changed by this Hair release.
Disk deployment is separate from live refresh. Sampling remains approximate;
these fixtures do not establish artist dynamics, collision or FPS acceptance.
