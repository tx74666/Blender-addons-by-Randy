# Character Designer 0.77.92 — Forearm calculation and empty Reference Sets

**Local delivery completed, 2026-10-23.** Complete-X native49 checks, both
official deployments/checks and the protected artist .91→.92 update passed.
Six outer and24 installer checks preserve the original complete author state
apart from version. The current dirty scene was backed up; the formal file and
preferences were not saved by this update. The task endpoint was normally
closed. See [final acceptance](D:/Blender/Projects/Character/X/validation/gui_playback_20261023/acceptance_summary092.json)
and [X README092](D:/Blender/Projects/Character/X/validation/gui_playback_20261023/README092.md).

Forearm correction previously read the same matrix rows repeatedly when forming
a weighted blend. `forearm_twist_math.blended_matrix` now prepares each bone's
products once for current-call trusted 4×4 rows, one to four influences and a
cache miss. It retains the original ordered built-in `sum`, weight validation
and normalization, finite/singularity guards, cache keys and Matrix copy
isolation. Generic inputs, larger blends and cache hits retain their existing
paths. No cross-frame positive validation cache is introduced.

Two isolated real-X ABBA experiments contributed 128 samples each, with exact
tested pose, evaluated mesh-position and Shape Key witnesses. The first manual
`frame_set` experiment observed less calculation time but no complete-frame
benefit. A second experiment explicitly entered the display component's
background playback state: calculation means were 25.6289→23.0414 ms and complete
`frame_set` means 63.1297→61.8089 ms, about 10.10% and 2.09% lower in that ordered
shared-host run. These are within-experiment comparisons; the change of playback
mode between experiments is not a version gain. Neither experiment used native
Play or measured GUI FPS. See the raw [first result](D:/Blender/Projects/Character/X/validation/forearm_hotpath_20261023/blend_native01/result.json)
and [explicit-state result](D:/Blender/Projects/Character/X/validation/forearm_hotpath_20261023/blend_playback_state01/result.json).

Math correctness also includes 16 native mathutils fixtures and 86 paired native
edge cases, including nonfinite values and both accepted and rejected
near-singular inputs. CPython 3.12 and Blender's bundled 3.13 each passed 16
targeted pure tests and 744 differential comparisons across 186 cases. Pure
MatrixStub equality or timings do not replace native model evidence. The
[math evidence index](D:/Blender/Projects/Character/X/validation/forearm_hotpath_20261023/README.md)
links the tests, source pins and all limits without duplicating raw arrays.

Reference Sets with no recoverable scene entries now return no selection before
reading either `active_set_id` or the empty dynamic `scene_set_choice` enum. The
two-line guard avoids that warning route without assigning, clearing, reordering
or persisting a selection. Existing nonempty-set selection and fallback logic
remain unchanged. Ten pure regressions pass, with four empty-path failures on
the fixed old source. Seven tiny real-RNA/AST cases preserve results and fixture
state; the two empty cases produce two baseline warnings and none for the
candidate. This is targeted validation, not full add-on or artist validation.
See [Reference acceptance](D:/Blender/Projects/Character/X/validation/gui_playback_20261023/reference_enum/acceptance_summary.json).

The definitions-only runtime installer changes the two original function objects'
`__code__` fields, literal version and exactly three Python source-signature
rows. Its 15 pure transaction tests and bundled Python 3.13 static audit pass;
the latter checks `len/map/range/sum/tuple/zip` native bindings. Source review
covers wrapped/mixed functions, module and function-cached builtins, untouched
state and four rollback boundaries. Complete-X native49 checks and the live
transaction subsequently passed; the original function/globals/defaults,
RNA/handler/timer/cache identities remain. See [pure receipt](D:/Blender/Projects/Character/X/validation/gui_playback_20261023/runtime092/pure_acceptance.json),
[3.13 receipt](D:/Blender/Projects/Character/X/validation/gui_playback_20261023/runtime092/static_acceptance313.json)
and [independent source review](D:/Blender/Projects/Character/X/validation/forearm_hotpath_20261023/runtime092_installer_review.json).

Independent GUI observations used unchanged .91. The layout target was a
971×393 **DOPESHEET** subtype, not a certified ACTION editor; only the larger of
two Asset Browser areas was replaced. The short ordered layout run establishes
no layout benefit, regression or exclusion of all UI cost. Its separate
10-frame profile observed Forearm frame-post/update counts of 10/10 and
graph-post 0, with no duplicate identity in the inspected handler lists. Shared
Timeline/Dope Sheet header code is distinguished by area. These limited counts
do not inventory every native callback or establish an overall UI cost.

A separate .91 Material/Solid native-Play ABBA measured POST_PIXEL changed-frame
cadence of 1.9747/2.9229/3.4553/2.6918 Hz over frames 11–30. Both Solid phases
were higher in that run, while host memory briefly reached about 97.4%. This is
neither pure-GPU attribution, physical presentation FPS, current artist FPS nor
a .92 math gain. See [layout observation](D:/Blender/Projects/Character/X/validation/gui_playback_20261023/README_layout01.md)
and [shader raw result](D:/Blender/Projects/Character/X/validation/gui_playback_20261023/shader01/result.json).

The frozen package has 215 files, including 183 Python files: four changed and
211 byte-identical to approved .91. Changes are `forearm_twist_math.py`,
`reference_views.py`, literal version in `__init__.py`, and the package README.
Approved `body_region.py` remains
`09543e30a254824919c14e0ddab96ed0c7ab5ef79329651dfe24668b0b8ab101`;
the unrelated paused canonical Body-region candidate is excluded. Build/deploy
must use the complete [projection](D:/Blender/Projects/Character/X/validation/gui_playback_20261023/projection092/projection.json),
SHA `fcab8cbd4285eddc18805cf1ebaf7e75b61723bb61f64e88bfd8546514ad4be1`.
The locally deployed ZIP is 1,656,007 bytes, SHA
`312fe7bbb120e3b2d548946d947bb0d740ceffd799e002244b9e42d33799ae77`;
[build receipt](D:/Blender/Projects/Character/X/validation/gui_playback_20261023/build092_receipt.json)
records its exact 215-file payload. No Git commit or push is included.

Artist PID40512 remains at frame49, Object mode, Dress active, selection empty,
both armature data in POSE and dirty=true. Fresh SaveCopy SHA
`9aa537a48ae7fcbb3e8bb236f2dbe93d9281a08e87ead5d042648881eabe4b4a`
has40,576,771 bytes; no independent serialized reopen is claimed. Complete live
state before/after SaveCopy and after the two-function update is exact except
version. Event-loop context and normal SDK cleanup passed; OS port62567 has no
listener. No .92 artist GUI FPS or complete Sync time was measured.
