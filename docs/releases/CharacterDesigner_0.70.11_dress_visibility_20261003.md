# Character Designer 0.70.11 — Dress object visibility

In the saved Cosha scene, the independent `SK_Dress_Rig` armature had
`hide_get=True`, `hide_viewport=False` and `visible_get=False`. Its 33 native
target bones were internally unhidden, so the old Original Dress button was blue
despite the object being hidden. Checking CoshaRig's Bone Collections did not
expose the independently parented skirt armature.

Native and Controls button states now include effective object visibility once
per target rig. Explicitly displaying a group reveals its owned armature object
and its existing bone targets. Turning a group off hides that group's bones,
not the entire character object. Visibility snapshots include object eye and
viewport flags with their scene/view-layer identity. Returns and failures restore
those flags; cross-scene/layer restores are rejected rather than changing another
layer. Excluded or hidden collections report the blocker and roll back without
unhiding whole helper collections.

Older active Original sessions are supported. Their first explicit reveal adds
the current object's flags to the exact saved session transactionally. It retains
all original pose, constraints, Rest, membership and recovery fields. A failed
reveal restores the original raw session and all display flags. No draw or reload
mutates an old session or guesses its historical object state.

## Evidence and boundaries

- 18 Original tests, 14 Bone Display tests and 3 Forearm runtime-batch tests
  pass: 35 checks. Coverage includes object eye/viewport hiding, old records,
  rollback, collection hide/exclusion, view-layer boundaries, and rename/save/reopen.
- The read-only saved-state probe confirmed the actual hidden Dress object and
  the previous false-blue state. It never saved the loaded artist file.
- The real saved Cosha verification used Blender 5.2 factory startup and two
  threads. It revealed all 33 target bones, extended the legacy snapshot, kept
  exact pose channels, toggled the group off/on, restored its hidden object on
  return, revealed Controls and exercised a new Original roundtrip.
- Mesh geometry, every Shape Key, UV, groups/weights, mesh bindings, skeleton
  Rest and parent relations hash identically. Non-armature object visibility,
  including hidden helper shapes and wires, remains unchanged.
- Built and verified `character_designer-0.70.11.zip`: 130 files, 966133 bytes.
  Deployed to Blender 5.2's installed add-on and the X validation copy at 00:21:07
  Asia/Shanghai; both deployment checks report zero differing files. Backup:
  `C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20261003-002107-c546ae72`.
- In the current artist X window, Dress was correctly gray with its rig hidden.
  No Refresh prompt was visible. Clicking Dress revealed the skirt bone chains
  and the separate armature in the Outliner; the button became blue and visible
  object count changed 8 to 9. A second click hid the Dress bones and returned
  gray, while the armature object remained visible; a third click restored the
  bone display. Body/Hair and the artist's active Pose Mode were retained.
- Saved the current artist `D:\Blender\Projects\Character\X\X.blend` with
  Original and Dress visible at **2026-10-03 00:30:50 Asia/Shanghai**. Blender
  confirmed `Saved "X.blend"`, the title's unsaved marker disappeared, and the
  file timestamp advanced; size 32209289 bytes. No pose rotation was performed
  during the live display check. Background verification never writes X.blend.
- No claim of new switch timing or FPS is made by this display-correctness fix.
  The 0.70.10 deferred-runtime and rollback checks remain intact.

Evidence in `D:\Blender\Projects\Character\X\validation`:

- `dress_visibility_0710_saved_state.{json,log}` and `probe_dress_visibility.py`
- `dress_visibility_original_tests_20261003.log`
- `dress_visibility_display_tests_20261003.log`
- `dress_visibility_forearm_batch_20261003.log`
- `dress_visibility_0711_real_verification_20261003.{json,log}` and
  `verify_real_dress_visibility.py`

Local source changes only; no commit, push, PR or publication.
