# Character Designer 0.77.51 · Body part workflow

Rig now shows **Body / Hair / Skirt**. Body uses one **Body Part** selector for
both legs, one leg, both arms, one arm, and both feet or one foot. An existing
part can use **Rebuild Controls** immediately after the original bone positions
have been edited. **Release to Edit** is the secondary action when the original
bones still need adjustment. A released part keeps the existing rebuild path.

Whole-leg rebuilding includes the knee Pole, leg controls and installed foot
controls. Foot-only rebuilding keeps the leg IK and Pole. Whole-character
placement and Show Body Controls are in Body. Old saved FIX selections map to
Body; legacy operator identifiers remain available for existing callers.

Regional release now retains selected custom widget geometry, native mesh
attributes, materials, display transforms, colors, locks, source bindings and
visual fitting backups. Small native Mesh copies referenced by the Armature
survive saving and reopening between release and bind. They are validated before
rebuilding and consumed after a successful operation. The one-click rebuild
wraps release and bind in one rollback boundary. Shared surviving widget geometry
is reused without being rewritten. New controls receive new solver ownership;
old solver frames and inputs are not copied as display settings.

Native bones, current native Rest, weights, authored mesh and Shape Key data,
unrelated control regions and existing historical animation are protected.
Head, torso and fingers do not gain independent teardown in this release.
Unsupported animation or external dependencies refuse the operation. Existing
animation recovery data retains its earlier body proportions and is not
silently retargeted. See the [workflow](../body_region_controls.md).

## Release scope

The local release uses accepted 0.77.50 plus seven runtime files:
`__init__.py`, `ui_constants.py`, `rig_fix_ui.py`, `body_setup_ui.py`,
`body_region_ui.py`, `body_region.py` and new `region_styles.py`.
The paused 0.77.48 watcher, provenance and display optimizations remain
canonical-only. The shared transaction fix and foot-only backend are byte-for-
byte 0.77.50. No playback or FPS improvement is claimed.

Validation, exact source projection, package hash and deployment state are
recorded in the X-owned evidence folder
`D:\Blender\Projects\Character\X\validation\body_part_integration_20261018`.
Only isolated test scenes may be saved by these checks. Updating the plugin
does not rebuild or move the open artist scene.

## Validation and local installation

Blender 5.2.0 LTS `fbe6228777e7`: 9 native regional-style tests, 18 existing
regional regressions and a targeted routing/re-registration test passed.
The style cases include save/reopen, shared widgets, retained animation owners,
injected failures and the public one-click Undo/Redo operation. The two pure UI
suites passed 28 checks. This does not claim the unrelated broad UI suite passes
its older Limb IK field-order assertion.

An isolated copy of current X-Cosha input `8d25e5ef…92581` passed whole-leg
rebuild, public repeated rebuild, custom styles and finished-hand protection,
small Foot Roll/Toe Bend checks, opposite-foot isolation, and native save/reopen.
Authored mesh/weights/Shape Keys, native Rest and historical Actions were equal.
The first rebuild's maximum reported surface error was `1.623235e-5 m` against
the `1e-4 m` limit. Final maximum foot-anchor error was `6.604750e-6 m`.
This is not a complete gait or physical shoe-ground contact certification.

The complete 202-file projection SHA256 is
`3710a8cd1c6cf69f1b999eb51879b6d039bed110201afc51bcfe9d6ea948c0d3`.
Package SHA256 is
`8fe11c4ca31c3f5121f1f6430e84c950683085107679544e5f8be4179cb5420e`.
Both the Blender 5.2 installation and X validation copy were deployed and
checked with zero mismatches. The paused optimizations remain excluded.

Artist PID 45600 was observed with X open; it was not refreshed, rebuilt, moved
or saved by this work. Its file hash remained unchanged. Load the installed UI
using the normal **Refresh Add-on** action or on the next normal launch.
Only the isolated validation scene was saved. Background checks exited;
the separate unsaved Builder6 window was untouched. No Git commands, commit,
push or publication were performed. Actual assistant model/effort: unrecorded.
