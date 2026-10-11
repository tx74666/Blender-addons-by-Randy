# Composition control and export audit

Read-only source audit, 2026-10-11. This is a staging note, not deployed runtime
code or a native validation result. The pending Character Designer 0.77.24
runtime projection and artist scene were not changed. The human reference is
`C:/Users/Randy/Downloads/Character_Designer_Composition_Codex_Reference_v1.txt`.
The shared composition contract must be frozen before implementing persistence
or publication fields.

## Actual source paths

* `addons/character_designer/unity_animation_controls.py:48` validates the full
  owned constraint/driver graph through limb and extension recovery records.
  Its `ControlPlan` is an exact full-packet importer/baker plan, not a mask or
  an interactive composition ownership plan. It forces every limb switch to
  FK, redirects torso/toe sources, and neutralizes Root, bend, eye and foot
  inputs. Reusing all its switches or keyed channels for B would modify A's
  domain.
* `limb_ik.py:3720` exposes validated per-limb `chain`, `target`, `pole`, solver
  and optional orientation mechanisms. `limb_ik_fk.py:115` returns IK/FK/BLEND
  from the actual owned switch. `limb_ik_fk.py:400` converts desired armature
  poses using native Rest and the evaluated parent frame; its FK and IK
  matchers start at lines 465 and 537. They perform a no-pop mode transfer,
  not a generic inverse of composition C.
* `torso_controls.py:52` validates `sources`, `controls` and `bend` from its
  saved graph. Build lines 304-338 show that the shared bend rotates all
  recorded spine segments. `spine_ik_fk.py:178` validates the optional IK
  branch and its exact relationship to the torso record; `mode_for_rig` at
  line 87 has the same FK/IK/BLEND distinction.
* Enhanced limb targets and poles are explicitly parented to Master, checked
  in `limb_ik.py:3464`. Direct targets/poles use the exact Root-control allowed
  parent at lines 3489-3490 (or no parent). The native arm chain is parented
  under the body, not necessarily under the target's input frame. A blend of
  control inputs is therefore not automatically a native upper-body mask.
* `character_setup.py` contains explicit HIPS/HEAD mapping plus name-based
  candidates. It does not provide a complete authoritative Humanoid upper-body
  map. Composition must receive exact validated native identities from the
  shared contract; shoulders, chest, fingers and scope must not be guessed.
* `animation_export.py:37` rejects Edit/Tweak mode and nonpositive clip length,
  resolves the selected slot, and snapshots `{rig, action}` plus admitted Dress
  roots with `libraries.write`. `animation_export_worker.py:138-173` recursively
  checks parent/constraint/driver inputs and rejects external animated sources.
  At lines 367-386 it disables NLA, selects one Action/slot and samples the
  full control graph. Unkeyed inputs remain at snapshot values. This does not
  isolate B if the snapshot was made while C was displayed.

## Reliable channel plan

The proposed read-only plan takes the exact native B mask, the fully validated
rig inventory and an explicit editing mode policy. It returns channel keys as
`(RNA data_path, array_index)`, control identities, affected native identities,
mode property keys, coupled groups and external-parent boundaries. RNA paths
are obtained from actual PoseBones/constraints, not constructed from display
names or prefixes. The plan must reject duplicate writers and partially owned
coupled groups.

* FK arms: native TRS of the exact recorded upper/lower/end chain, with the
  corresponding validated arm switch fixed to 0. Leg switches are not B inputs.
* FK spine: validated source-to-`controls`/`fk_controls` map, plus supported bend
  rotation. A shared bend is permitted only if every affected recorded spine
  source belongs to B. The validated spine switch may be fixed to 0.
* Shoulders and fingers without owned redirects: declared native identities,
  after proving their authored transforms have no competing graph writer.
* IK arms: actual target/pole inputs; optional roll-decoupled orientation inputs
  must be explicitly classified as effective authoring inputs rather than
  hidden solver/display mechanisms. Their influence and end-rotation behavior
  are taken from the exact rig record, including Auto Align/Manual semantics.
* IK spine: validated chest and shape inputs when the complete affected spine
  chain is B-owned. FK bend/controls are not simultaneously authoritative.
* Fractional IK/FK is preview-only for the first direct-edit contract. The user
  may explicitly enter a supported full-weight FK/IK authoring state; the tool
  must not silently switch modes.
* Root/Master, Hips, legs, Foot, Dress, Hair and eyes are outside the default
  B ownership domain. Head participation and any exception require explicit
  semantic contract identities, not a broad upper-body name filter.

Ordinary native parent inheritance still belongs to A outside the B boundary.
For a supported direct local transform write, `convert_local_to_pose(...,
invert=True, parent_matrix=current_evaluated_parent,
parent_matrix_local=protected_parent_rest)` is the established conversion.
Reject shear/nonfinite/singular transforms. This is a parent-space conversion,
not C-A world-matrix subtraction.

## Preview and IK boundary

A simple FullWeightOverride NLA stack is feasible for closed, fully authored FK
input groups. B must supply its entire permitted property/component ownership
at every B time; omitted B properties would otherwise continue to come from A.
There must be no duplicate active-Action playback of B, transition above the
editable strip, unsupported blend mode, animated layer weight or ambiguous
time remapping. The current implementation has no composition entry point.

For existing IK inputs whose Master/Root frame differs from the native mask
boundary below Hips, one-rig NLA control blending cannot be claimed equivalent
to Unity native upper-body local Override. A viable IK-capable design solves B
independently and composes its native local results under A's declared boundary.
Supported displayed input transforms can be mapped through that explicit
boundary frame into the independently authored B frame. The B solver receives
only B inputs; C is a display result. No C sample is written back into B.
This route needs native pose and parent-space tests before acceptance. Until
then, direct composition editing should offer the proved FK route or a clear
Edit Action/effective-controller route instead of inverse IK promises.

Blender's documented Full Stack Tweak keeps other NLA tracks visible, but upper
transitions can prevent keyframe remapping. Current API documentation exposes
`isolate_action` and `use_upper_stack_evaluation`; exact Blender 5.2 polling,
slot and Undo behavior still need isolated validation, not an assumption.

* https://docs.blender.org/api/4.5/bpy.ops.nla.html
* https://docs.blender.org/manual/vi/5.2/editors/nla/editing/strip.html

## B-only export admission

Keep the existing external animated dependency guard unchanged. A composition
snapshot must not silently detach, clear or exempt an animated weapon, object,
constraint target or driven datablock. Static reference assets can remain only
when exact ownership/binding and direction of dependency are admitted; an
animated environmental dependency must be migrated into B with its own proof
or reported unsupported.

Use a disposable B evaluation rig/data copy, complete native Rest/parents and
the exact validated source control graph. Assign only B's chosen slot, NLA off,
REPLACE/weight 1. Reset out-of-domain transforms and every unkeyed owned input
to an explicit canonical reference, never the current C snapshot. Retain graph
helpers necessary to evaluate B but do not give their channels automatic edit
ownership. Same-rig pointers must resolve to that isolated evaluation rig.
Any display-only boundary rebase, composition driver or temporary visual weapon
attachment must be removed by its exact session record or excluded before this
snapshot; do not use a prefix scan or weaken dependency validation.

Sample B independently. Keep the full bind skeleton for the existing exporter,
but attach the exact native application mask/role to the composition return
identity. Lower-body structure in the FBX is not permission to apply those
channels. FullWeightOverride effective native transforms must be identical
when only A, A phase or current C pose changes.

Hold Pose should materialize a positive-duration constant Action in the
isolated export path (two equal samples at distinct times), with no synthetic
motion and no change to the logical B draft. Source A, Source Actions, other
slots and domain-excluded B Custom curves must remain exact. Sync failure keeps
B_edit and its session persistent.

## Cheap tests before native acceptance

* Exact role resolution and current mode selects only real effective channels;
  hidden solver/display bones and partial coupled groups reject.
* Curve copy/writeback touches the selected B slot and exact whitelist only;
  other slots and all domain-excluded B Custom curves remain unchanged.
* Canonical B isolation is invariant to two unrelated A snapshots and phases.
* Foreign animated parent/constraint/driver dependencies still reject.
* Hold Pose has positive time and equal pose endpoints.
* Interrupted Sync retains B_edit and restores Action, slot, NLA, time and
  selection; no source Action is mutated or falsely marked Edited.

Native acceptance remains outstanding: real Cosha + Walk + actual gun/Hold,
upper-body and finger edits, supported IK controller behavior, Undo, save/reopen,
isolated export invariance, Unity mask/Rig application and Raise-to-Hold timing.
