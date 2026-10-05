# Hair strand motion contract

Status: Real-character Blender preview and unbaked FBX/active-sidecar export are
verified. Unity isolated fixtures passed full-32 native conversion/persistence
and concurrent focused gravity-independence checks, followed by all-32 finite
fixed-root sampling. A separate same-clock native collision check passed direct
single-strand contact and response with the other three strands unchanged.
Official Wiggle 1.1.2 is installed/enabled in the artist's Blender 5.1 session;
Character Designer 0.76.1 was refreshed, configured and saved to X.blend.
The isolated and live integration work is complete. These checks
do not establish equal-time or physical equivalence between Wiggle and Magica,
or a per-component performance result.

## Artist workflow

Character Designer > Rig > Hair > Strand Motion records the existing independent
Hair chains. Initialize Strand Motion creates source-owned metadata without
changing bones, segments, topology, weights or Shape Keys. Previous / Next /
Selected highlights one chain and loads its persistent settings. Choose Front /
Bangs, Side or Back / Long and use its preset; adjust a strand, then Apply Strand
Settings or Apply to Group. Restore Defaults removes local overrides against the
saved group default. Initialization suggests editable groups from source-local
root position and whole-chain length; this classification never establishes a
mirror pair. Unassigned strands remain explicit until assigned.

After intentionally changing an existing Hair chain's Rest or segments, use
**Reconcile Strands** to align the saved records with the current owned chains.
The button remains available even when the saved metadata still parses.
Reconciliation preserves valid stable identities and authored settings; failed
ownership or topology proof does not assign replacement identities by guesswork.

Along Strand uses normalized root-to-tip depth controls, independent of segment
count. The root/tip controls cannot be removed. Extra controls interpolate
piecewise linearly. Apply commits the edited controls. Mirror synchronization
affects the paired strand's configuration only; both physical bone chains remain
independent. Center strands are one chain. Unproven pairs remain unpaired; Mark
Left / Pair Right explicitly records a user-chosen correspondence after validating
the complete chains, without automatically searching for nearby strands.

Enable the external official Wiggle Bones 1.1.2 extension to preview a pair or all
configured strands. The adapter verifies its original manifest and source
fingerprints before every start. Preview runs through timeline playback. Stop,
Save, Undo/Redo, Load, Render, incompatible mode/structure edits and a backwards
timeline jump end preview and restore the author's starting pose/settings. The
Blender installation copies and the current artist scene require separate
deployment/refresh/save verification.

## Persistence and proof

- `character_designer_hair_strands_v1`: source UUID plus UUID5 physical-strand and
  chain identities, stable ordering, exact source vertex sets/layers, bone
  ownership, Rest/parent proof, reciprocal pair IDs and provenance.
- `character_designer_hair_motion_v1`: group defaults, per-strand overrides,
  reciprocal mirror synchronization flags and normalized depth settings.
- Configured sources use one private, non-executable `Hair Motion Identity`
  Text as an independent ownership anchor. A source pointer retains it on save;
  it has no fake user. Remove/rebind of the same mesh keeps its UUID, strand IDs,
  groups and normalized depth curve, then requires explicit proof reconciliation
  for new bone segments. Copies cannot inherit the original identity, including
  after deleting the original. Plain unconfigured binding/removal retains its
  previous exact property reversibility.
- UI navigation state is transient. Persistent records survive save/reopen and
  do not depend on visible names. Reads never repair records. Rest/segment proof
  changes require an explicit reconcile; missing identities are not reassigned
  by nearest position or bone names.
- Native topology correspondence must cover the complete vertex set and every
  ordered layer reciprocally. Partial or ambiguous maps never become a pair.
- Proof priority is existing native provenance, native topology mapping, exact
  geometry with unique reflected Basis vertices, then a unique graph mapping.
  Geometry and graph proofs include the complete edge/face incidence and a
  reciprocal boundary map. Geometry uses exact source-local X reflection, with
  no nearest-neighbor fallback or matching tolerance. An already proven mapping
  survives coordinate edits while identity and connectivity remain valid.

## Semantic settings

`recovery` and `damping` range from 0 to 1. `gravity` is acceleration in m/s²
(0 to 20); `stretch` is a fraction from 0 to 1. Depth controls carry position,
recovery, damping, gravity and a relative mass coefficient (0.01 to 10).
They describe intended behavior, not interchangeable solver coefficients.

Wiggle mapping `wiggle-1.1.2-v1` uses stiffness `800 * recovery²`, damping
`20 * damping`, and gravity multiplier
`gravity / (length(scene.gravity) * scene.unit_settings.scale_length)`.
Wiggle already stores endpoints in world space; Armature object scale is not
applied a second time. Actual scene gravity direction is preserved. The upstream
engine ignores `scene.use_gravity` and ignores `fps_base`, so the adapter requires
`fps_base == 1`. Nonzero requested gravity with a zero scene vector is refused.
Mass is relative. No physical equivalence with Magica is implied by these formulas.

Preview is exclusive: existing enabled Wiggle bones are refused because the
upstream rebuild affects its whole Scene. Hair constraints/drivers, Armature
object feedback, linked/shared rig data and unproven pose-writing callbacks are
refused. Known disjoint callbacks can supply an explicit ownership proof. Head
animation can remain an input; only owned Hair tails are simulated. The adapter
does not bake, replace Actions, create keys or remove another add-on's handlers.
Idle preview guards do not run a recurring mode timer. Sidebar backend status is
briefly cached; start always performs fresh strict validation.
Changing a source binding or capture first stops preview and restores author
state. Navigation reuses only its current action's verified registry; every next
action verifies ownership again. Effective settings for all strands perform one
complete profile validation before assembling independent results.

## FBX sidecar and Unity handoff

Model export keeps `bake_anim=False`. Export first stops transient preview;
disposable model/Action workers disable Wiggle before evaluation. Neither calls
Wiggle Bake. An active configured Hair source creates
`<FBX stem>.hair-motion.json` in the existing atomic publication transaction.

The JSON schema is `cdesigner.hair-motion/1`, `schema_version: 1`:

- `source_uid`, source name, Character Designer version and exporter revision;
- export `asset_id`, actual `fbx_sha256`, topology proof, source/consumer unit
  contract, Blender/FBX/Unity axis declarations;
- ordered stable strand/chain/pair IDs, side, pairing provenance, vertex/layer
  proof and boundary maps, explicit source/exported bone names, original
  source-local Rest;
- effective group/settings and normalized depth controls per strand;
- `simulation_baked: false` and versioned conversion IDs;
- application policy `inherit_existing_native`, `require_explicit_apply: true`,
  `component_layout: independent_per_strand`,
  `conversion_validation: pending`.

Capture uses strict live native proof, repeated in the snapshot before bone
cleanup. Exported names come from the actual retained skeleton and accessory
merge table, never a naming regex. Source Rest is marked in source units; native
pre-FBX data is not mislabeled as converted Unity Rest. Multiple configured Hair
sources are refused by the first schema revision. An old owned sidecar retained
for reference safety is explicitly inactive in the current model manifest;
consumers must require that manifest's active advertisement and matching hashes.

Unity must preserve the current author-tuned Cosha Magica profile/colliders as its
baseline and require explicit application. Its present single Hair component
shares parameters across 32 roots. Independent strand settings require disjoint
root inventories, with no overlapping old component. Paired strands share
settings, not simulated state. Existing Dress physics is outside this change.

Comparative validation starts with a proven short-front pair and long-back pair:
use the same head motion, units, timing and resting geometry; compare tip peak,
response delay, decay/settling, fixed roots, left/right independence and collision
behavior. Expand to all strands only after this evidence. Measure component cost,
save/reopen, model reimport and Play-exit restoration. Pure JSON or Blender tests
alone do not prove Unity import, equivalent motion, hand feel or GUI performance.

The current native Magica BoneCloth uses Transform origins and fixes each root;
Wiggle simulates bone tails. A four-bone chain therefore does not expose the same
last endpoint in the two APIs. Comparisons must identify the same geometric tip
through the actual imported Rest/bind transform, or report the structural
difference. Adding an end Transform requires an explicit implementation decision
and must not silently change the source/export skeleton.

Conversion capability reports must distinguish represented fields from retained
baseline fields and approximations. The current native Magica tether stretch
limit is a constant; distance compression is not semantic stretch. It has no
equivalent normalized-depth mass or gravity field. Recovery/damping curves are
sampled into 16 uniform runtime values, so a 24-knot semantic curve is not copied
exactly. Preserve unrepresented native settings and report any calibrated or
approximate mapping rather than claiming equality.

Unity preview has isolated transient ownership. Character tuning's automatic
Play-exit capture is guarded while a tentative sidecar preview is active.
Versioned strand identity/path records extend capture, restore, validation and
publication beyond the legacy single-Hair part key. The authored baseline is
archived and quarantined for explicit application, with exact restoration on
cancel or failure. Selection records require exact imported Transform binding;
the old 32-root selection is not copied into a one-strand particle domain.

## Verified native integration boundary — 2026-10-04

Blender 5.1.0 real-character checks cover 32 four-bone chains (128 segments),
Front 10 / Side 8 / Back 14, proven setting synchronization, independent preview,
exact restoration and ordinary export/reimport. Character Designer 0.76.1 passed
16 native UI tests and was deployed/checked as 144 files for Blender 5.1, 5.2
and X; deployment alone does not refresh an already open artist session.

Unity 6000.5.9f1 native lifecycle checks cover exact imported binding, transient
four-strand preview, Undo/Redo, injected late rollback, all-32 committed capture,
publish/save/reopen, settings/selection/collider preservation and stale tickets.
The final PlayerLoop check sampled two focused copies concurrently (8 active
teams for 96 actual frames), stopped all eight, then sampled 32 teams for 60
frames. All 2,688 samples were finite. Maximum fixed-root error was
2.604774635983631e-7 m. A gravity-only change on one strand produced a maximum
tail response difference of 0.02753540128469467 m; the other three strands had
zero difference under matching measured native schedules. Context, files,
Play-start settings and the owned Builder lease were restored.

The gravity counterfactual is preserved as its own evidence. A separate collision
check ran two otherwise identical focused copies concurrently: eight active
teams for 96 actual frames, with matching measured native schedules. Only the
owned Head sphere's trajectory differed; both copies used the same explicit
QA-owned 1.5 mm sphere radius. Those changes were discarded with the fixture.
All 768 samples passed; direct contact
was observed in 32 frames. The target strand's maximum response difference was
0.00666339136660099 m, the other three strands had zero difference, and their
minimum conservative sphere/friction-envelope clearance was
0.09564614507482139 m. Maximum fixed-root error was 2.604774635983631e-7 m.
All 96 prepared native settings/reference/selection/Rest records survived load;
only eight teams ran, while the other 88 configurations remained disabled.
Collider identity, actual native edge geometry, normal/friction changes and the
simultaneous control response jointly establish this contact evidence.

The first background cleanup reached its 120-second timeout and retained the
owned fixture, pending guard and Builder lease. After foregrounding Unity, the
existing idle Cancel action retried the exact guarded cleanup successfully.
No persistence flag was cleared and no code gate was waived. Context, files,
Play-start settings and the lease were preserved/released in the final report.
The scheduling cause of the first timeout was not established; unattended
cleanup is therefore not claimed reliable from this run.

The final native comparison passed; its comparison against Blender's recorded
1/30-second time schedule remains diagnostic. No fake clock, manual solver
stepping or end bone was introduced. The fixture prepares 96 inactive native
configurations and enables 8 then 32, so it is functional QA rather than a clean
component-cost benchmark. The earlier sequential comparison remains a failed
historical report and is not relabeled by the concurrent pass.

Raw evidence is under `D:\Blender\Projects\Character\X\Validation\hair_motion_20261004`.
Unity reports are under `D:\Unity Projects\RandomRealm2\Logs\CharacterTuning\HairMotion`:

- `native-lifecycle-20261004-115839-0506100.json`: full-32 lifecycle pass.
- `native-play-537cc03b542243898eabc2645653958a-final-ef81022ad2004c18b47aa72ca7680f0f.json`:
  concurrent dynamic pass; Playback source SHA256
  `4eac0e51932b39bfaae6aee8f2cf3f9639936d0e9a609234913e53de8f79aba8`.
- `native-play-31b7b2675985466eb535cc5206b6dd38-final-9e9ac4da1590448c95e8ec88a7c092fe.json`:
  direct native collision/contact-isolation pass; report SHA256
  `cb6d7f7de3d7dbf52dea2649ecfc79992da21744ced8efadf0d4ebb20af0c0c9`;
  current formal Playback source SHA256
  `429d54455c78a341c0112d147028b0598a76f48c39caee89b2e9f908f0517896`.
- `native-play-31b7b2675985466eb535cc5206b6dd38-runtime-pending.json`:
  preserved successful sampling evidence before cleanup completed.
- `native-play-1d32735017a84c1a8ea7216e73bcfe3f-final-5e9981d98f674860bf78c156ab9c28a1.json`:
  earlier sequential timing mismatch, preserved with `Passed:false`.

The lifecycle run preserved Adventure's dirty 46-root context. The gravity Play
run preserved Billiards' clean one-root context. The later collision run ended
with Adventure as the sole loaded scene, still dirty with 46 roots, after a fresh
authorized normal Stop and the guarded cleanup retry. These are separate runs
with human scene changes between them; no original scene save or reload was
performed by the QA. Adventure's on-disk SHA256 remained
`1e52f0a3ecbe310ebe2031232b78c46ec296cd3a94ad90d908f825eaa3abb3f6`.
The current production single-component Hair baseline and Dress remain outside
the isolated fixture application.

The collision run used the native Editor DLL last written at
2026-10-04T16:00:24.9005288Z (6,639,104 bytes). The subsequent supporting Editor
rebuild passed with exit code 0, 220 warnings and zero errors in 197.48 seconds.
Its log is `unity_editor_collision_support_build_20261005.log` in X's validation
directory. Root's process check found no retained task dotnet/MSBuild/VBCS workers.

## Verified artist installation and save — 2026-10-05

The authorized live Blender 5.1.0 operation passed with Character Designer
0.76.1 and `bl_ext.user_default.wiggle_bones` 1.1.2 loaded. Official extension
installation, preferences save and artist save each returned `FINISHED`.
X.blend was saved at 2026-10-05T01:32:20.3313822+08:00, 32,258,575 bytes,
SHA256 `2690a4a97fa30e0e94f7df6a14d3f75e7ec8f5b4e71175d91ac966da6875445d`;
the UI title was clean. Configuration records 32 strands, Front 10 / Side 8 /
Back 14. The 24 records carrying mirror IDs include two center self-identities.

All seven live checkpoints preserved the raw asset fingerprint, selection,
display, pose inputs and legacy settings; maximum evaluated pose-matrix error
was zero. This live context contains 377 meshes, 118,221 vertices, 40 Shape Keys,
2,415 Rest bones, 140,535 weight assignments and three Actions. The frozen
isolated preview/candidate context reported 375 meshes and 20 Shape Keys;
each before/after comparison applies to its own input and fingerprint schema.
Only the owned Hair metadata and its independent identity Text were added.
The observed Object Mode, active/selected Cosha, frame 39 and View 3D editor
were preserved. No artist preview, bake or file reload was started; there is no
new independent disk-reopen audit claim.

An independent native recovery copy returned `FINISHED` before integration.
Legacy `wiggle_2` 2.2.4 was verified inactive and retained: its original file,
top-level values/presence and inactive raw backing survived official registration,
with a separate settings backup. There was no automatic conversion of legacy
values into the official backend. The isolated legacy-transition proof and the
live audit/recovery records remain separate evidence.

The immutable report is
`live_install_refresh_config_save_20261004T173113.695674_0000.json` in X's
validation directory, SHA256
`45687a1ef5f4bd3fdf8e5170322bd9537c708ffb67ae76e9f914108eee8c25b5`.
The integration index records the recovery, legacy proof/audit/backup and
installer-source hashes. The verified execution metadata is `gpt-6.1-sol` /
`ultra` at turn-context UTC 2026-10-04T16:41:33.821Z, line 22778 of
`C:/Users/Randy/.codex/sessions/2026/10/02/rollout-2026-10-02T02-58-29-01a0f8d5-1398-7482-bbe4-d123cd51098d.jsonl`;
this identifies that recorded phase rather than assigning one model to every
historical stage.

Unity was released to Main after verification. Its Adventure dirty/46-root
end state above is the snapshot at release, without a claim about subsequent
Main activity. Intentional Rest/identity/backend changes trigger targeted
revalidation. Applying a profile to a production Unity actor remains an explicit
separate action.
