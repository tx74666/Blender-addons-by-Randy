# Finger Joint Tool

## Lateral surface-center preference (0.61.29)

The previous clearance-first solver could place an otherwise valid straight
axis visibly to one side of the artist's selected center strip. `surface_centering`
now intersects a narrow longitudinal input with regular ordered sections: a
face strip contributes the midpoint of its two adjacent section vertices, and
an edge rail contributes its section vertex. At least three well-spread samples
and consistent transverse tangents are needed. Large root faces and the final
cap ring are excluded. Broad/ambiguous or short inputs return no preference;
the previous safe volume solve still handles transverse loops and markers.

An equal-section least-squares fit estimates lateral slope against the detected
root-to-tip direction. Averaged transverse tangents define the surface normal;
the computation is mesh-local, independent of screen axes or click order.
`surface.centering` holds a fitted path and normal, separate from immutable
`surface.origin`, the original surface path and the uninset topology span.
It is reflected with the rest of the record and retained during owned layouts.

The solver adds center-plane candidates with independent depth offsets/slopes
and small lateral alternatives. Coverage/retreat, direction limits, minimum
clearance and the full-segment certificate are unchanged. Among eligible equal-
coverage candidates, lateral deviation is preferred before clearance/stability.
Dimensionless score quantization at 1e-4 suppresses float-noise tie changes under
rigid rotations; it never quantizes geometry or relaxes certification. Exact
alignment is not accepted if unsafe, and no curve fallback is introduced.
The center hint does not define bend direction or alter the Bone Roll workflow.
Saved older axes stay unchanged until a new Capture; there is no extra UI.

Tests: 69 focused headless cases, including `test_selected_strip_controls_lateral_position_not_depth`.
It shifts the unselected opposite surface, confirms the old volume-centered
solution's lateral error, verifies a safe surface-centered solution, rotates
actual geometry and checks mirrored/persistent hints after a ring update.
X's long-strip regression now checks center-plane deviation on all ten fingers
as well as containment, coverage, two consecutive layouts and no saved-file
mutation. Its GUI test supports `--ring`, uses an aligned orthographic view and
has an inspected preview screenshot. This is scripted View3D operator/render
validation, not a manual keypress test in the user's open Blender session.

## Real root-transition containment (0.61.28)

This corrects .27's false refusal of long strips that include the knuckle before
the first regular ring. `prepare_body()` retains the identified sleeve as local
metadata but marks `root_extension` when an explicit selected start precedes it
by more than 5% of stable root thickness. The extension must remain within both
60% of sleeve length and twice root thickness; deeper palm selections fail.
`Volume` then uses the actual face-connected closed surface for the proof, not
an artificial cap through the selected root. A closed, consistently oriented,
non-degenerate surface is still required. An opening elsewhere in this connected
component causes a conservative refusal, not an open-mesh fallback.

The larger proof domain does **not** expand the search range: candidates retain
the selected longitudinal bounds and the same coverage, end retreat, direction
and full-segment clearance constraints. Ordinary sleeve captures retain their
small capped volume. No geometry is created or changed for either proof.

Root-extended records keep local sleeve/selection evidence, not a fingerprint
of the whole connected body. Definition validation rebuilds and checks the
actual proof surface, including after unrelated edits and owned ring updates.
Opposite and active-deformation checks use the same remapped root-extended body.
Thus other fingers' topology updates need not invalidate every reference, but
new holes or geometry making an existing axis unsafe still reject the guide.

Long selected strips can wrap slightly around the very end of a capped finger.
Only trailing backtracking within half a local radius, after an extremum within
one radius of the detected tip, is trimmed from the working range. The immutable
`surface.path` remains complete. This is not permission for interior folds or
arbitrary path truncation. Internal retreat is still separate from the topology
span consumed by Ring Layout.

The paired UI now omits the L/R switch, side suffix and repeated side count
labels. Equal counts display once; unequal counts warn. Bilateral records and
opposite validation are unchanged. Successful capture/switch/clear clears stale
layout/bend status without clearing the underlying safety checks.

Validation this release: 68 focused headless cases (nine internal-axis cases),
including a synthetic real-root closed/open proof and oversized-extension guard.
X's `test_real_x_finger_long_internal_blender.py` captures ten ten-face strips
covering the knuckle transition and tip, verifies actual whole-segment clearance
on both sides, then applies Index/Middle layouts consecutively and revalidates
all slots. Captures leave mesh data untouched; layouts preserve ten keys and
custom normals; the saved X.blend hash is unchanged. Previous short-strip tests
did not cover the reported selection type. `test_finger_bank_gui.py` also passes
repeated keyboard Capture, failed replacement, visibility, switching and actual
layout Undo/Redo in an isolated Blender GUI process.
The separate X `test_real_x_finger_long_gui.py` invokes the Capture operator in
a real View3D context and checks the resulting internal record and rendered
preview; its screenshot was inspected. It does not claim a manual mouse-click
test or run embedded asset scripts, and never saves the user's asset.

## One-click straight interior reference (0.61.27)

Normal `finger_bank.capture(CAPTURE)` builds a complete record from the existing
five-finger census, before committing any reference metadata. Rest geometry is
read automatically, independent of the active key; current deformation containment
is also checked when applicable. No key choice/value/coordinates are changed.
The successful record is intrinsically confirmed. The panel removes Mark,
Confirm, Swap, Basis and top-surface controls; one eye toggle replaces Show/Hide.
Legacy standalone APIs remain for backward-compatible scripts, not normal UI.

The record separates:

- `input` and `support_input`: live selection/support correspondences for validation;
- `surface.origin`: immutable captured selection IDs, original coordinates,
  topology fingerprint and source identity (historical IDs, not current indices);
- `surface.path`: original selected path or marker, and SELECTED/DETECTED range mode;
- `basis.path` / compatibility `current.path`: uninset geometric/topology span;
- `body`: ordered body rings and closed-tip surface used for containment;
- `internal.path`: exactly two points, the certified internal straight segment.

Longitudinal selection retains its own range and is oriented using detected
root-to-tip direction. A closed transverse loop or pointlike cross-section
locates the existing full finger range. Backtracking/ambiguous paths are rejected.
No original surface is overwritten merely to move a preview inside the mesh.
After owned layout changes the live support/body is rebound and the interior
segment recomputed; historical input and the uninset span remain intact.

`finger_internal.py` uses existing rings to estimate a root-to-tip chord, a
least-squares direction and stable transverse widths. It searches translations
and small direction deviations (dot >= .97 with the longitudinal chord), not
arbitrary longest diagonals. A root virtual cap closes only the detected sleeve;
actual cap/body triangles form the remaining temporary volume. Open or
inconsistently oriented/non-manifold surfaces and degenerate triangles fail.
It never creates a scene mesh or extends into the palm to obtain more length.

Endpoint retreat trials are 10%, 12.5%, then 15% of stable root/distal thickness.
The distal estimate excludes the final possibly collapsed ring. At least 85%
of the requested longitudinal coverage must remain; failure is explicit, not a
short segment or curve fallback. The earliest feasible retreat maximizes axial
coverage among these candidates; for equal coverage it prefers larger surface
clearance, then less residual distance to section centers. Minimum clearance is
4% of the smaller stable thickness. These are conservative automatic defaults,
not five manual depth sliders or an assertion of global geometric optimality.

BVH nearest-surface distance quickly ranks candidates. Solid-angle winding at
the midpoint establishes the interior component. Recursive interval certificates
use the distance field's 1-Lipschitz bound: for midpoint distance d and half-length
h, every point in that interval has at least d-h clearance. Only a complete cover
of certified intervals succeeds; refinement limits fail conservatively. Endpoint
tests or finite sampling alone never authorize success. Reflected axes are
separately certified inside the actual opposite body before reference replacement.

`definition.frame(purpose='TOPOLOGY')` returns the original uninset path for Ring
Layout. Default frame/preview and Bone Roll use the internal segment, with the
reliable selected top normal if available. A bare ring's missing bend direction
does not block Basic Setup; Bone Roll reports its own requirement. This does
not straighten the mesh or move existing bone joints. Existing topology writes
still retain their Basis-only preflight; captures do not require changing keys.
Guides concern base Edit/rest geometry, not evaluated modifiers or posed skin.

Duplicate-marker finding: .26 `mark(START)` retained the completed record while
`_pending()` drew an additional 3-axis pending cross. It was GPU overlay geometry,
not a Basic Setup Empty. Bank previews now suppress pending crosses, replacement
clears pending state, and redraw clears queued/cache references. Failure keeps
the old guide but puts an explicit old-result warning in both panel and viewport.
Display/load/Undo/Redo do not accumulate handlers. No historical scene objects
are removed: the independent legacy Joint tool's real Empty is not a start guide.

Validation: 59 previous focused cases (bank key expectations updated for a single
rest reference) plus 8 `test_finger_internal_blender.py` cases. Tests cover full
segment containment, endpoint clearance, original-range layout boundaries,
non-Basis safety/no writes, rotated transforms, open-edge capture, bad midpoint
despite valid endpoints, concavity/holes, atomic failure, compact UI, repeated
capture/no Empty, visibility and save/reopen. `test_finger_bank_gui.py` sends
actual F7 capture events three times, a failed event with labelled old result,
switches fingers/visibility, and uses F8 plus Ctrl-Z/Ctrl-Shift-Z for layout.
Screenshots are inspected separately from headless assertions. X's disposable
`test_real_x_finger_internal_blender.py` checks ten unbound finger identities and
both interior segments while a non-Basis key is active, without Confirm/Top;
then five paired Roll operations and Index/Middle layouts, retaining ten keys,
custom normals and the saved file hash. It never saves the real asset.

## Five paired Basic Setup records (0.61.26)

`finger_detect.py` builds a read-only census of capped regular quad sleeves.
It stops at nonregular palm transitions and rejects branches, open/hidden tips,
collapsed regions and indistinguishable duplicate protrusions. Expanded root
rows are trimmed; equivalent candidates sharing a cap are deduplicated. The
selected vertices must intersect exactly one detected finger region (a local
protrusion, not necessarily a disconnected mesh island). A nearby five-member
group is identified using four approximately aligned roots/directions and a
distinct, offset thumb. Sorting the remaining four toward/away from the thumb
supplies Index/Middle/Ring/Pinky. Ambiguous order is refused, not derived from
element indices, capture order, screen X, weights or bone names.

Mesh Local X is the default reflection plane; an existing X Mirror modifier's
mirror_object supplies its reference when present. Positive/negative plane X
uses the L/R convention. Expected reflected roots/tips locate unique opposite
candidates; a missing member remains a warning/anchor, not a fabricated mesh.
Verification requires matching counts, a one-to-one reflected vertex map within
max(length*1e-4, 1e-7) mesh-local units, reversed face winding/connectivity and
the same root-to-tip ordered loops. Initial detection needs one complete,
geometrically distinguishable five-finger hand. The other hand may be damaged.

`finger_bank.py` owns object-scoped references through `finger_bank_ui.py`'s
Object PropertyGroup: ten side records, one active identity, and a cached survey
with five pair warnings, ordered rings and persistent missing-member anchors.
The UI has five pair indicators and one Capture/Start/End row; L/R changes the
displayed member only. Current-key/Basis samples, independent pending markers,
optional top normals, revision and confirmation are retained per side. Normal
automatic Capture orients the selected/full span from the detected finger and
confirms it. A closed ring or short face uses full ordered body centers plus
the cap extreme. A long path retains its selected span. Manual Start/End never
cross identities and requires the existing explicit review step.

Capture stages in a temporary legacy reference and commits only after unique
identity and geometry validation. Only that digit is replaced; failure preserves
old records. A verified opposite guide reflects path/normal and remaps actual
mesh elements; asymmetric/absent mates get an error, not guessed geometry. Set
Top Surface cannot take a different finger's patch. Range/bend operations sync
the mate. This does not modify the mesh, rig, key values, selection or binding.

Local evidence stores coordinate correspondences and oriented input-face/edge
connectivity. Short-marker captures also retain every vertex and face supporting
the detected full-body path; an unselected tip/key edit or a local face split
cannot leave that path silently valid. After unrelated topology/index edits, exact coordinate matches and
local connectivity permit rebinding; changed/ambiguous local geometry is refused
while keeping the settings. Local validation never treats an unchanged vertex
count as proof. Owned layout changes preserve reference span, restamp verified
new body coordinates, re-run the census and report any asymmetry. Preparing a
second finger no longer invalidates the first finger's saved reference. There
is still only one active immutable layout recipe; switching fingers requires
Prepare Rings and hides the previous ring overlay. This release does not make
topology changes bilateral; the warning is the intended result of one-side edits.

Recheck updates candidates/counts without changing geometry and can recover a
previously missing opposite tip using its stored mirrored anchor. Manual mesh
edits show a recheck warning; old records are not erased. Object-scoped pointers
prevent another mesh from borrowing the first character's active definition.
Reference metadata still does not consume Edit Mode mesh Undo. Geometry actions
retain native Undo/Redo; transient caches/overlays are invalidated as before.
Panel drawing only reads cached data; complete validation runs outside drawing.
Legacy single-definition services remain for scripts/F3 compatibility.

Validation: 8 new tests in `test_finger_bank_blender.py` plus 51 previous focused
tests. They cover unbound geometry, arbitrary capture order, isolated overwrite,
cross-finger failure, pending isolation, closed-ring/current-key capture,
asymmetry/recovery, lost opposite cap/recovery, transformed objects, ambiguous
order, save/reopen and consecutive topology edits on different fingers.
`test_finger_bank_gui.py` verifies five indicators, one-side mismatch, keyboard
Undo/Redo and an unaffected digit after Redo. X's disposable integration removes
the Armature modifier and vertex groups before runtime detection, then uses
ground-truth bone names only to select test inputs; both hands' ten identities
are detected geometrically. It also tests paired Roll reuse and layout changes
on Index followed by Middle, retaining the saved X.blend hash and ten Shape Keys.

## Shared definition before actions (0.61.25)

`finger_definition.py` owns one persistent Scene reference; the UI and lazy
overlays live in `finger_definition_ui.py`. Definition capture only reads an
owned BMesh copy and writes reference metadata. It never changes mesh/bone
coordinates, mode, active Shape Key, selection, binding, UVs or weights.

Inputs are a connected face path (including non-quads), an open edge path, two
disconnected endpoint patches, separately marked Start/End, or one continuous
rest bone chain selected in Edit/Pose Mode. A clear elongated patch can provide
a PCA span. Ambiguous/nonelongated patches instead request explicit endpoints.
Face/edge traversal preserves topology order; PCA signs are correlated by
vertex identity across current-key and Basis samples, not world direction.
The safe-sleeve discovery is only an orientation hint, preferably from Basis;
its failure cannot block a valid reference or replace the chosen endpoints.
Only unique cap/continuation evidence is called a detected tip. Other arrows
are explicitly marked for review. The selected span is never silently reduced
to the regular quad portion, and a surface path is never called a bone center.

Amber Start and mint End transverse markers bracket a blue reference path;
the optional orange arrow uses the negative projected top normal. Bare edges
and bones do not supply a bend side. Set Top Surface is independent of the
range. A pending Start is drawn immediately. There are no mesh color edits or
helper objects. Object matrices are applied at display/action time. These are
base Edit mesh/Shape Key or bone-rest references, not evaluated modifiers or
posed geometry. The path length is world-space arc length.

Capture on any active relative Shape Key is permitted. Current-key and Basis
coordinates are sampled separately; Use Basis Reference changes metadata only,
never the user's active key. Confirm is required before consumers modify data.
Prepare Rings additionally requires the actual active Basis and the existing
topology/data preflight; it maps regular ring centers into the confirmed path,
keeps original safe rings inside that span, then stores an immutable recipe.
Roots, end caps and unsafe transitions stay protected. Bone Roll uses the same
confirmed Basis bend reference, retaining the 0.61.24 bilateral transaction.
No automatic placement or weight recalculation is connected in this release.

Reference topology/selected-coordinate stamps detect stale input. After the
tool's own layout apply, the owned original source can validate reference data
only if the full generated-mesh fingerprint still matches. Separate top
references use that same check. A new range revision invalidates a previous
prepared layout; clearing the definition prevents applying its old recipe.
Existing legacy recipes and F3 operators remain compatible. The compact main
panel no longer asks the artist to capture the same strip in three tools.

Definition metadata is a reference setting, not an Edit Mesh undo edit: capture,
confirm and clear deliberately omit UNDO rather than inserting steps which
cannot restore Scene properties. X clears the markers without changing the
model. Native Undo/Redo remains on topology and bone changes. Overlay caches
are invalidated on load/undo/redo; Show restores the persistent guide. No H or
other global keymap is added. Old guides aren't silently migrated.

Tests: `test_finger_definition_blender.py` (11) plus the existing 40 focused
cases. `test_finger_definition_gui.py` proves capture doesn't consume the prior
mesh edit's real keyboard Undo/Redo. The layout and bilateral GUI fixtures now
consume the shared definition. X's `test_real_x_finger_definition_blender.py`
checks all ten fingers from non-Basis capture through ring updates and paired
Roll, preserving ten Shape Keys, custom normals, heads/tails and source hash.
`test_real_x_finger_long_definition_blender.py` also captures ten-face paths
across the irregular root and fingertip on all ten fingers, verifies detected
direction and full span beyond the regular sleeve, and checks no mesh/file write.
Full snapshot validation runs outside draw callbacks, with a shared cached
frame for the panel and bone overlay; actual actions validate synchronously.

## Bilateral bone-axis correction (0.61.24)

Both surface-defined calibration and the legacy Roll Reference entry now update
existing matching L/R finger bones in one transaction. No new opposite bones are
created. Selection and active bone are retained, and Head/Tail/parent/length,
mesh, Shape Keys and weights are not written. This change is for bone Roll,
not automatic bilateral topology editing or the separate bone-placement tool.

`finger_symmetry.py` centralizes exact Blender name-flip pairing, Armature Local
X reflection and shared rest-axis guards. Parent and connected-joint settings
must agree. Reflected heads/tails must be within 25% of the longer paired bone
length (minimum absolute tolerance 1e-5), and reflected direction dot must exceed
0.9. These conservative checks allow small asymmetry without inferring a new
symmetry plane for an arbitrarily oriented rig. Locked or missing counterparts
abort the entire operation rather than silently falling back to one hand.

`finger_flex.plan` accepts one finger's selected side or matching bilateral
selection, completes/deduplicates the pair and identifies the captured guide's
side by proximity. An equidistant guide is rejected. Source bend B is a polar
vector, so the mate uses S(B), then its own tangent T to form A=T cross B.
Legacy X/Z rotation axes are axial: their reflected target is -S(A), projected
to the mate's transverse plane. Copying Roll floats or merely reflecting A
without the sign reversal would give the wrong positive bend sense.

For the legacy per-finger reference, one selected side supplies each reference
(active selected side if both sides are present, otherwise the sole side or L).
The source anchor is kept; its counterpart is synchronized, not left with an
unrelated old Roll. Active Bone references across hands are reflected once.
Previews cover both sides. Pose/constraint/animation guards cover all affected
bones/descendants before any write. Native X Mirror is temporarily disabled
during explicit writes/rollback and restored in finally to avoid double mirroring.

Tests: `test_finger_flex_blender.py` now has 11 cases; `test_finger_bones_blender.py`
has 5, including Local Z, both-selected deduplication, opposite-side pose refusal,
missing counterparts and injected bilateral rollback. `test_finger_symmetry_gui.py`
provides an isolated two-hand preview and actual keyboard Undo/Redo fixture.
The X integration flex test applies six paired bones from each hand's guide,
checks actual positive rotations and neutral skin matrices on all counterparts,
and verifies source mesh/weights/Shape Keys and file hash remain unchanged.

## Root-surface range and sliding (0.61.23)

The 0.61.23 capture was **Capture Finger Root**. Select a connected surface at
the desired start, optionally including a lengthwise surface to the tip. The
root may contain triangles/poles or an irregular fan; no specific 3-to-1 pattern
or closed loop is required there. `finger_range.py` searches within three face
steps for a regular circumferential quad sleeve, extends it in both directions,
then accepts only a unique small closed cap opposite a continuing surface.
Direction is not derived from face indices, a global axis, or a bone binding.
Open/hidden tips, two-ended capped tubes and ambiguous neighboring fingers are
rejected with a selection hint, rather than silently reversing the finger.

The minimum selected-vertex projection along the proximal sleeve tangent gives
the root reference plane. The distal cap extreme supplies the tip; world-space
centerline arc length (including root/cap offsets) normalizes joint positions.
The virtual root is drawn dashed using a translated section outline, explicitly
not an exact palm-surface intersection. The first/last safe real rings remain
separate fixed edit boundaries. A target outside them is preview-only and blocks
Apply; the user-defined root is never silently shortened to fit the safe sleeve.

`finger_ring_slide.py` assigns nearby existing rings one-to-one to targets,
preferring exact matches then joint centers, before support/filler rings.
Each movement is restricted to 49% of the smaller neighboring spacing, so rings
cannot pass each other or collapse onto one target. Transverse UV/data seams,
sharp/marked edges and face/material discontinuities pin a ring. Missing targets
are inserted after the assigned rows move, so the two flank rings remain on
opposite sides of their center. No original artist loop is dissolved.

Sampling always uses the immutable captured source rails, including independent
corner-side interpolation for UV seams. Shape Keys and weights on moved/new
vertices are interpolated from that same source, not from already moved points.
Supported floating attributes interpolate; integers/booleans use the nearer
endpoint. All original indices and all outside coordinates/data remain stable;
staged verification checks moved coordinates/keys/weights and unchanged regions
before swapping the mesh. Custom normal transfer retains the previous outside
packed-normal protection. New captures cache seam-protected rows for responsive
slider previews; changing the real mesh or its data requires recapture.

Schema-2 scene recipes store the selected faces, full root/tip/length, ordered
safe rows/fractions and protected rows. Schema-1 recipes preserve the old
insert-only behavior; recapture is explicit migration, not automatic reinterpretation.
No bones, bindings or group definitions are created/changed; no auto weights or
root fan remeshing occurs. Sliding may change the polygon/Subdivision surface
between sampled rails; it is not a promise of identical evaluated shape.

Validation: `tests/test_finger_range_blender.py` (7 new cases), the 7 legacy
layout cases and 22 previous finger/UI cases, plus the GUI fixture's actual
Undo/Redo and update after Redo. Real X checks cover all 10 fingers both with
longitudinal selections and with only a proximal root face, preserving 10 Shape
Keys/custom normals, exact rig state and the source file hash. Root-only test:
`X/tests/test_real_x_finger_root_range_blender.py`. All real checks are disposable;
the user's working Blender and saved X.blend are not edited by testing.

## Two-joint layout (0.61.22)

The main Mesh Edit Mode entry is now **Rig > Body > Fingers > Finger Ring Layout**.
`finger_layout.py` owns capture, surface sampling, staged topology and data
verification; `finger_layout_ui.py` owns persistent scene state and lazy overlays.
The old one-loop operator remains available via F3 **Finger Joint Rings**, with
its original Side A/B spacing settings in the confirmation dialog.

1. Select one longitudinal row of at least two top quads on the finger body.
   Capture expands each band around the circumference, stopping with an error
   at incompatible ring sizes, hidden faces, open boundaries or branches. It
   never silently includes palm webbing or rebuilds a fingertip cap. A valid
   previous `finger_flex` top-strip guide can be reused when no faces are selected.
2. The captured section's ends remain fixed. Centerline arc-length fractions
   specify the two joint positions; original per-vertex longitudinal rails
   interpolate the real non-circular cross-sections. A valid flex guide supplies
   root-to-tip order, otherwise the artist verifies the labelled boundaries and
   can reverse them. Coral/cyan numbered rings mark joint centers; support and
   between-joint rings are muted grey. Numeric sliders update preview only.
3. Three-ring width is a half-width on either side of each center, separately
   adjustable per joint. Between-joint count excludes those support rings and
   all original artist rings. Roots, tips and **all original vertex coordinates**
   remain unchanged. Original shape loops are never dissolved; adding rings to
   non-planar quads/Subdivision can still affect evaluated shading/surface shape.
4. Generate / Update stages a fresh copy of the captured mesh, subdivides only
   the matching longitudinal connectors and interpolates loop/point data. Source
   data is stored as a private Mesh referenced by the Scene recipe (one current
   layout). Save/reopen keeps the update workflow. Repeated update is idempotent;
   changing counts rebuilds from the same source, not from the last result.
5. A full current-data fingerprint prevents overwriting intervening geometry,
   UV/attribute, Shape Key, transform or weight edits. Because Edit Mode hides
   some RNA attribute buffers, fingerprinting uses an unlinked synchronized
   snapshot. The real scene is not switched or edited during preview/capture.

Shape Keys remain present with original coordinates, values and relationships;
new coordinates and weights are interpolated. No bone or group definitions are
rewritten. Custom normals use a temporary float-vector loop layer for interpolation;
unaffected vertices retain original packed normals to avoid re-quantization of
the rest of the character. The affected normal-space encode permits a 0.005
vector tolerance, while unaffected normal vectors are checked at 1e-6. Supported
original attributes/UVs, faces, seam/sharp flags, weights and Shape Keys are
verified before swapping meshes. Commit exceptions restore the old mesh/recipe.

Source capture and previews require the Basis key and one local single-user mesh.
Animated/driven/absolute/locked keys, Skin data, index-parented children,
Multires levels, baked or index-dependent modifiers and unsupported attribute
types are rejected. Armature/Subdivision/Mirror modifiers are retained, not
applied; guide lines refer to base Edit Mode coordinates, not posed evaluation.
Hidden preview is not cancellation of applied geometry; use Undo to revert, or
X to release the editable recipe without deleting geometry. Load/Undo/Redo clear
transient drawing handlers' cached geometry. No H/S/Shift keymap is installed.

Not implemented here: direct viewport ring dragging, removal/relocation of original
shape rings, asymmetric bend-side spacing, automatic bone placement, reweighting,
or 45/90-degree deformation acceptance. These are separate from retaining and
interpolating the character's existing weights. This scope intentionally keeps
the first geometry workflow predictable.

Tests: `tests/test_finger_layout_blender.py`, `tests/test_finger_layout_gui.py`.
X integration: `X/tests/test_real_x_finger_layout_blender.py`, run against the saved
X.blend only in a disposable Blender process. All ten real fingers pass with six
top quads and 48 added vertices each; the file hash and rig remain unchanged.

## Earlier single-loop prototype

The prototype logic lives in `addons/character_designer/finger_joint.py` and is
exposed under `Rig > Body > Fingers > Joint Topology`. The same Fingers panel
also contains the per-finger root guide and placement controls.

The artist selects one closed mesh edge loop in Edit Mode and clicks
`Lock Center Loop`. The tool accepts it only when the loop has two continuous
neighboring quad bands. It keeps the locked loop as the center ring, inserts
one loop into each adjacent band, leaves all three rings selected, and creates
a `CD_FingerJoint_*` empty marker in `Character Designer | Finger Joints`.

The two ratios are measured from the selected loop toward each side. They are
deliberately called Side A and Side B until a finger direction or bone chain
is explicitly supplied; the prototype must not guess which side is proximal.

## Safety contract

- No runtime AI or external service is used.
- The prototype does not edit bones, weights, or Shape Key values.
- Existing Shape Key blocks must remain present and receive the new topology.
- Non-quad, open, branched, disconnected, or interrupted selections are
  rejected before the operation.
- A marker with the same source-loop signature prevents accidental duplicate
  ring generation.
- The operator is undoable. The `.blend` file is not saved by the tool.

## Current implementation notes

`bmesh.ops.subdivide_edges` splits the connector edges of each neighboring
quad band. `edge_percents` controls the two ring positions, so the selected
middle loop remains the artist's reference. The marker stores the source
signature, source vertex IDs, and the two ratios as custom properties.

## Supported first version

- One simple closed loop.
- At least four vertices.
- Two one-face-deep neighboring quad bands.
- A mesh in Edit Mode with no topology interruption beside the loop.

The first root-guide implementation now supports five named finger slots. Each
slot locks one mesh center face and, by default, uses the nearest matching
finger chain from Main Rig as the direction line. A two-vertex manual line is
available as a fallback. Check solves the closest intersection of the face
normal line and the finger centerline, rejects a visibly skew or outward
solution, Preview draws the construction, and Apply places the selected
existing finger chain while preserving its rolls, weights, and hierarchy
settings.

## Surface-defined bending (0.61.19)

`finger_flex.py` adds a separate, persistent scene guide. One connected top quad
row supplies its length through its end cross-edges and an area-weighted top
normal. Single long quads and longitudinal edges with active adjacent faces
are also supported. Blue is the user-confirmed root-to-tip direction, orange
points inward (opposite the projected top normal), and green is a positive bend
arc. Both arrows can be reversed. A bare edge cannot uniquely define a normal.

Given unit T and outward top normal N, B = -normalize(N - T dot(N,T)) and
A = T cross B. Saved schema-1 captures keep their old outward convention;
schema-2 captures explicitly store normal_sign = -1 and all source faces.
Each selected bone uses its own head-to-tail T and the guide's bend side to
construct A. Bone-mode previews put purple hinges and proposed wire bones at
the unchanged bone Heads; red lines show current Local X. Applying aligns
local X to A, including the chain root, and checks
that an actual positive rotation moves toward B. The mesh guide uses base
Edit Mode coordinates; posed/modifier-evaluated surfaces are not sampled.
Source topology changes invalidate capture. Armature rest-axis changes require
neutral affected poses, no constraints on affected bones, no rig animation or
drivers, and a local single-user rig with uniform positive scale. Failure
restores all selected rolls. The tool does not save the user's blend file.

Run `tests/test_finger_flex_blender.py` in factory-startup Blender for rotation,
transform, ambiguity, rollback, persistence, and Shape Key/weight preservation
checks. X's `tests/test_real_x_finger_flex_blender.py` exercises both hands' 30
real bones in a disposable process without saving the source asset.

Still not implemented: automatic anatomical face/direction discovery, weight presets,
pose preview, and complex finger-root or webbing
topology. These should be added only after real test meshes show a stable need.

## Verification

Run:

```powershell
D:\Blender5.2\blender.exe --background --factory-startup --python tests/test_finger_joint_blender.py
```

The regression test checks three generated cross-sections, asymmetric Side A
and Side B ratios, Shape Key preservation, marker metadata, and duplicate
refusal. Before extending the tool, add a focused failing test for the new
topology case.
