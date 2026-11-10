# Character Designer 0.77.169 — native weight-paint entry

Select one bound mesh and open **Weight > Weight Tools > Paint Weights**.
The entry shows existing Deform bones that have groups on that mesh, hides
other bones for this view, and enters native Weight Paint with the rig in Pose
Mode. Native bone selection activates its corresponding group. The current
Blender 5.2 keymap provides Alt-click and Ctrl-Shift-click for this selection.

**Finish Painting** restores the preceding display and painting selection flags,
then returns to rig Pose Mode. Newly painted weights and locks are retained.
This temporary view lives in RAM; finish it before closing or reopening the file.
Automatic Weights remains a separate operation.

The entry uses actual Armature modifiers, `use_deform`, and existing groups.
Single-user checks count actual `Object.data` owners, not ID reference counts.
The observed rig's three ID users were one rig object and two Action references.
169 corrects 168's false refusal without copying data or editing those references.
The previously built/deployed 168 remains recorded and was never adopted live.

The code changes no weights, group definitions or locks, Rest, pose transforms,
Shape Keys, Actions/NLA/drivers, custom-shape bindings, or history ownership.
There is no new workspace, handler, timer, or continuous mesh scan. The existing
bone-display frame hook only skips this rig while its temporary paint view owns
the display. Native Original restoration, generation, rebinding and animation
conversion are not called.

The complete release contains 267 files/235 Python files, with four existing
files and one new file over approved167; 262 files are inherited exactly. Held
canonical controller/worklist edits are excluded. The frozen projection is
`D:/Blender/Projects/Character/X/validation/weight_paint_entry_20261109/stage169_01/projection.json`,
SHA256 `96078ab9c9ad9b531d9c79579a1c8bb0dbfa13d643d3960f95942627719ac974`.
Official ZIP SHA256 is
`c219526f060e6b2f6265a59e0369a1a8ee2017e0f4d1538aef73533ab5170a47`;
CRC/payload267 and matching Blender5.2/X deployment checks have zero differences.

Validation reuses the four actual tiny-native 168 checks for entry/exit, flags,
new-weight/lock retention and failure rollback. Three new 169 native checks
allow metadata references and refuse genuinely shared rig/mesh data. All seven
checks have empty error/cleanup lists and actual two-operator RNA cleanup.
Actual character native selection also matched each picked bone to its group;
one intended screen location hit an overlapping bone, and that original false
report is retained. Physical Alt-click was not injected. These are scoped checks,
not full production-animation, save/reopen painting, or performance certification.

Live narrow adoption and final button results are recorded separately by
Animation; disk deployment alone is not live adoption. The updater preserves the
original source-signature reference and makes no whole-runtime refresh claim.
The managed X2 Worklist/Make/AltR/WholeMove ownership limits remain unchanged.

Current artist adoption is confirmed by `artist_scoped169_update01.json`
(SHA256 `91cdccabe869dd63ae53eba3ddc8256602db3daf49afbcbc621e729105654261`):
two code/doc pairs, two operators and seven bindings, zero Scene reads/writes
and context unchanged. The actual two physical buttons passed once at frame67;
56 matching Cosha bones were selectable, the active bone/group matched, and
Finish restored display/paint flags/group and returned Pose with the original
knee-pole control. `artist_product169_smoke01.json` has SHA256
`3d668bf2daab1972eec38b17f89b1e0adf74e366a4d7be46af92ada7a1b95b22`.
The declared native-data fingerprint is exact, no brush stroke or Scene Save was
performed, current dirty work is retained, and Console returned to Timeline.
These references are under the same X validation directory as the projection.
