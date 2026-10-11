# Composition controls/export handoff (2026-10-11)

This is source-only M1 work. No native Blender test, worker launch, artist
mutation, deployment or build has been performed by this agent. The root owns
the fresh-window serial integration test and release projection.

## Projection

Project the three new runtime modules into the same `character_designer`
package as the frozen approved .24 worker files. Production uses sibling
files and exact SHA-256 checks, with no dependency on X validation paths.
Do not replace these workers with the unrelated canonical pending drafts:

- `animation_export_worker.py`:
  `b2668e204e4e75e71af165e3427eed8a3e2387c2dd2635cbd757d27d748a97be`
- `unity_export_worker.py`:
  `ade4b7627365371877c76d03edcde1229148671c1e7dfdcfe7f64d708f628622`

The explicit `CHARACTER_DESIGNER_COMPOSITION_TEST_ADDON_ROOT` fixture mount
is only for flat draft tests. Released code establishes its own exact sibling
package before importing helpers. It refuses an already loaded other package
or changed approved worker bytes.

## APIs

`animation_composition_export` re-exports the controls APIs:

```python
from character_designer import animation_composition as composition
from character_designer import animation_composition_export as export
from character_designer import unity_animation

packet = unity_animation.load_package(exact_prepared_b_packet)
scope = export.resolve_scope(context, rig, prepared_spec, packet)
scope = export.validate_scope(context, rig, prepared_spec, packet, scope)
fingerprint = export.effective_fingerprint(
    item.custom_action, item.custom_slot, scope, prepared_spec)
assert fingerprint['fingerprint_known']

# In an active Full Stack session, this official wrapper commits only the
# allowlisted B draft, exits Tweak, and restores its prior session afterward.
with composition.isolated_sync(context, rig) as inputs:
    numeric_b = inputs['effective_values']
    numeric_sha = inputs['composition_export']['effectiveChannelsSha256']
    # A native fixture may inspect/sample these disposable IDs inside this
    # block. They are disposed before it returns. No background launch is
    # needed for the foreground two-A invariance comparison.
```

For an inactive item, `composition._export_session(context, item)` resolves
the exact prepared packet and protected A. Pass that authenticated session
and item to `export.isolate_export(context, session, item)`.

The active-session entry is
`composition.enter(context, spec, item.item_id)`; it resolves A from its
prepared exact packet when no authenticated reference is supplied. Use its
official lifecycle methods for Tweak/commit/leave, rather than changing native
Action pointers or NLA tracks in a fixture.

Inside isolation, inputs include disposable `rig`, `action`, exact integer
`action_slot`, `dependency_objects`, `scope`, `timing`, `packet`, public
`composition` / `composition_export`, private `effective_values`, and
`b_fingerprint`. Snapshot and launch only through:

```python
job = export.begin_isolated_export(
    context, inputs, fresh_nonexisting_fbx_path, export_rig_name,
    launch_proof=frozen_collection_proof)
```

`launch_proof` is an owned plain dictionary with exactly
`_worklist_fingerprint`, `_worklist_composition`,
`_worklist_composition_metadata`, `_worklist_export_implementation`.
The starter freezes it before Popen and returns only after the snapshot is
durable. `poll_isolated_export(job)` validates actual foreground/worker
numeric equality before atomic FBX/sidecar publication and again reads the
published sidecar before the Link callback. `cancel_isolated_export(job)`
uses the existing shared exporter cancellation and disposal path.

## Scope and timing admission

The existing full bind/parents/units/owned controls and static Dress mapping
remains strict. An authenticated adopted Rest adapter is used for protected
source Rest only; generated controls/constraints/drivers stay live. All
selected limbs and spine must be exact FK. Coupled limb/spine/torso groups
must be complete. Unknown, blended, IK or external coupled dependencies
reject. Editable native/control IDs come from exact validated records and
the full packet path mapping, never names, prefixes or suffixes.

Prepared source B must carry top-level `packet['composition']` exactly equal
to its normalized DTO. Changing A or weapon configuration requires a genuine
new prepared variant. A genuine local NewAction may use its existing sealed
bind template with only `compositionId`, `editWorkItemId`, `editNewActionId`
changed; it additionally needs the authenticated persisted native creation or
the registration-owned typed transient proof. `new_creation` is a keyword-only
Scope argument for initial registration. A text new ID alone grants nothing.
The worker repeats the pure sealed DTO/template comparison.

Moving B requires genuine finite Action metadata `unity_sample_rate` equal
to `spec.fps`, `unity_start_frame == 1`, and positive `unity_duration`.
The requested interval must fit both that duration and the selected FK key
range. No retiming is performed. HoldPose first proves constant curves,
including Bezier handles, and additionally proves constant native effective
values; only then may its positive interval extend the source range.
New local Actions must receive timing from their authenticated native
creation path, not from the export validator.

Outside-mask ordinary Custom curves are preserved. Isolated B contains only
the exact allowlisted FK channels, canonical Rest inputs outside that domain,
canonical source/data-derived object placement, and the validated neutral
owned manual Dress wires. All NLA tracks, forward native ID-property
references, and display widget pointers are removed from disposable IDs.
Primitive generated-bone ownership markers survive for skeleton filtering.
Unknown static dependencies and Action constraints reject as well as the
unchanged ordinary animated-external dependency guard.

## Required native two-A fixture

1. Preserve exact native rig/data, full Rest, original Action and all selected
   slots/curves, NLA state, display/widgets, mesh/key ownership, Scene/frame,
   and working context. Import/protect Actions with separate full Action
   fingerprints; `_artist_state` is not an Action-content hash.
2. Prepare two genuine B variant rows and sealed packets: B1 with authenticated
   A1 Walk, B2 with A2 another compatible Walk. The variant IDs are real and
   independent; their authored B frames/Rest/scope/timing are identical. Use
   each row's own exact contract and official reference/session entry. Never
   override one item's DTO or copy curves to manufacture this proof.
   Pin the actual fresh model SHA independently from the historical .24 model.
   Add validates its real FBX hash and strict complete packet/native mapping;
   then explicit `worklist.activate(..., 'CUSTOM')` installs that new row's
   native Action/slot/full Link association before entering composition.
   The driver uses official Restore in the historical Scene, officially leaves
   any embedded Original session in that owning Scene, then adopts the same
   native Rig through an independently cleared `Scene.copy()` and the actual
   fresh workspace. This supports the copied test prefab's genuine target GUID
   without changing packets or the historical target association. The copy
   preserves actual CharacterDesigner configuration and native setup pointers.
   After the callback, it restores the historical native association, adoption
   record, original/motion Data and actual fake-user flags, old rows and global
   workspace, verifies raw preservation, and removes only its owned QA Scene.
   The historical source-model marker is preserved; fresh-model proof comes
   from the actual current Link, item/model file and full native mapping.
3. For each A, seek at least two different C poses, including differing object
   Root motion. Inside `isolated_sync`, capture `scope.proof`, B content/input
   fingerprint, the exact public channel inventory, numeric SHA and complete
   `effective_values` payload. Assert equal values and hashes across all A
   and seek cases. Do not compare only metadata booleans or FBX bytes.
4. Require exact all 10 local TRS components for every allowed native path,
   full native output skeleton count/paths, ordinary strict dependency guard
   failures for foreign/A-driven static targets and Action constraints, and
   rejection of FK mode/blend/rate/range/partial-coupling tampering.
5. After each isolation verify the protected original A and full selected
   Custom B contents (including outside-mask curves), native data identity,
   Rest, object/pose transforms, Action/slot binding, Scene/time and context.
   Restore checks remain exact. The isolation's own before/after checks cover
   those Action fingerprints plus data/Rest/object/pose/binding/time; the
   fixture additionally owns complete asset/NLA/UI lifecycle preservation.
6. Only the root may launch the owned background worker after the fresh-window
   RAM check. Its independent effective values must exactly match foreground
   float32 canonical values before Link publication or collection receipt.

## Cheap validation

Canonical `python tests/test_animation_composition_controls_export.py`: 35
tests pass. `python tests/test_character_composition_native_fixture.py`: 14
admission guards pass. The native driver is
`tools/verify_character_composition_native.py`; the fixture must pin complete
167-file .25 projection/root/version through `release_projection.load_source`.
Run explicit test files; recursive discovery of X staging enters the archived
`canonical_before_composition` Blender package and needs native `bpy`.
The shared C# binary Rest fixture, UTF-16 ordinal ordering, float32 identity,
exact path/role admission, B-vs-A fingerprint split, curve/rate/range guards,
closed graph, ID-reference cleanup, allocation/filter cleanup, artist Root
motion/slot checks, sealed DTO/native new admission, unlisted runtime injection
and pre-publication receipt rejection are covered.

`docs/character_composition_native_fixture.template.json` is intentionally
unrunnable until real prepared paths, hashes and variant IDs replace every
placeholder. The root creates an isolated `BLENDER_USER_DATAFILES` directory
inside the evidence directory before its fresh-window command:

```text
Blender --python tools/verify_character_composition_native.py -- --fixture ACTUAL_FIXTURE.json
```

## Separate explicit physical worker acceptance

The numeric driver launches no worker. A separate root-invoked
`tools/composition_sync_acceptance.py` first runs that unchanged-B fixture,
then starts serial real `composition.begin_sync` workers only for the exact
allowlisted manifest paths and pre-Sync SHA-256 values in
`docs/character_composition_sync_fixture.template.json`:

```text
Blender --python tools/composition_sync_acceptance.py -- --fixture ACTUAL_FIXTURE.json --sync-config ACTUAL_SYNC_CONFIG.json
```

The numeric fixture's `sessionChecks` must be null. Physical Sync runs inside
the explicit `native.run(fixture, report, after_numeric=callback)` scope while
the actual fresh QA Scene and its rows exist. The callback must not reload a
`.blend`; held native recovery references would become invalid. In an existing
owned native process, call
`composition_sync_acceptance.run(context, config, numeric_report, before_worker=guard)`.
The optional resource guard receives the exact `(context, item)` immediately
before each official worker launch; an exception prevents that launch.
It verifies the actual native work items, authenticates independent foreground
B, invokes the official starter, removes only its redundant owned timer,
archives each completed worker's snapshot/job/log/result directory before
normal disposal, and calls the official poll and collection completion APIs.
It then rereads and hashes the actual nonempty FBX, sidecar, final Link and
stored native receipt. Complete worker values must equal foreground values
and the two genuine A variants. An optional third entry may name an already
registered genuine NewB native row and its actual new Action ID; the helper
never creates one or invents Source provenance.

The helper writes durable evidence and restores the prior native binding;
the explicitly authorized immutable revision/manifest/receipt publication
persists. Its nine pure admission/archive tests pass. It does not import the
FBX back to compare transforms; an independent FBX roundtrip remains a
separate native check. This agent has launched no process or publication.

For the actual third SOURCE HoldLoop and subsequent registered NEW Raise,
`foreground_one(context, item)` produces a detached
`x.validation.character-composition-b-numeric/1` report. It includes the exact
sealed spec, complete canonical numeric values and native provenance:
`originalPreparedWorkId`, `actualWorkItemId`, `newActionId`, `templateLinkId`,
manifest path/hash, model/packet hashes and the actual Custom Action/slot.
It also preserves the literal packet/manifest UUID strings as
`sourcePreparedWorkItemIdExact`, `publicManifestWorkItemId`, and
`publicManifestNewActionId`; native lookup identities remain separate.
The original prepared identity for NEW is authenticated through its native
bind-template receipt and real template Link. Call
`run_one(context, proof['authorization'], fresh_directory, proof, before_worker=guard)`
only after the actual authoring or timing preflight. This remeasures the full
proof before launching. It neither fabricates a two-A proof nor implies Unity
Collect. The root collects the published third Loop before preparing Raise.

After numeric and physical two-A proof, the explicit callback can save a
separate UI seed with
`native.save_qa_seed(context, report, seed_path, physical_report_path=report_path)`.
This uses `save_as_mainfile(copy=True)`, checks Blender success, pins the written
bytes and never reloads. Its `x.validation.character-composition-qa-seed/1`
receipt contains `seedBlend`/SHA, actual Scene/Rig names and exact `rigSceneNames`
memberships (the original Scenes plus the owned copy), target/model identity,
workspace path/SHA, the genuine variant `workItemIds` and native `itemIds`, and
the completed physical report path/SHA. It is stored as `report['qa_seed']`.
The numeric report is written after historical cleanup finishes, so the separate
UI runner requires its final `completed` flag as well as that saved receipt.
Run the UI helper in a separate process against the immutable seed: it authors
B/A and intentionally saves/reopens dedicated evidence files. Numeric cleanup
does not use stale RNA handles from that lifecycle.

The root's explicit `tools/run_character_composition_first_stage.py` coordinates
numeric two-A proof, two SOURCE publications, genuine third Loop authoring and
SOURCE publication, then the QA seed save inside that scope. Its per-worker
hook checks current Windows RAM before each job. It pins all real child configs
and helper hashes; no placeholder packet or ID supplies a native proof.
The later UI entry `tools/run_character_composition_ui_fixture.py` requires the
standalone completed numeric report and immutable seed. Both entries remain
source-only until the root supplies the native serial window and approved
complete runtime projection.

The separate actual NEW Raise window uses
`tools/run_character_composition_raise_stage.py`; its complete config and
immutable first-stage snapshot rules are documented in
`docs/composition_raise_stage_handoff_20261011.md`. It permits only the real
Raise preparation/current Collect receipts to extend the workspace. It keeps
the UI runner's original-workspace hash guard unchanged.
