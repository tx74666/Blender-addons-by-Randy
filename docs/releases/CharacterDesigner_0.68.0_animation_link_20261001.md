# Character Designer 0.68.0 — single Walk Link

Accepted on 2026-10-01. This release adds **Animation > Link / Import** and
**Sync to Unity**, with one linked Cosha Walk validated through Blender editing,
immutable FBX publication, Unity Sync Preview and private Apply/Restore.

The exact linked Unity FBX is imported into an independent Blender editing scene.
The Link retains the rig, Action, slot and frozen source identity across save/reopen.
An existing same-name rig is preserved. Each successful Sync publishes a new FBX
and sidecar before advancing the manifest; failures retain the previous candidate.

Animation export evaluates the snapshot's object transform before recording the
reference skeleton. FBX frame 0 contains true Rest; all real motion samples begin
at frame 1. Unity checks the static hierarchy with animation disabled, verifies
the current source Avatar and actual Humanoid clip, then excludes the reference
frame from playback. Import errors reject the candidate and warnings are recorded.

Validation:

- Fourteen pure Link contract tests and registered Link/Sync UI checks passed.
- The linked model's 217 bones and all 59 source samples passed import checks.
  The forearm edit preserved both loop endpoints and saved Link/Action/slot identity.
- Native FK, controls and reloaded-library-snapshot FBX regressions passed all
  samples, static/reference transforms, timing, feet and root motion.
- Revision 4 exported 60 real motion samples, duration `0.9666666984558105` seconds,
  reference frame `0`, playable frames `1…60`, and effective FPS `61.034480751482185`.
- Actual Unity Sync Preview passed. Isolated playback measured a forearm local
  rotation difference of `30.0121689°` and repeat matrix error `0`.
- Private Apply assigned the same owned override to Player and Setup while
  preserving an unrelated Custom animation. Restore reverted only Walk, including
  when the recorded source hash was stale. Two footstep events and loop/root policies
  remained intact. Duplicate Sync and invalid manifests preserved the candidate.
- Production assets, scene dirty state and selection were preserved. Original
  `X.blend` and production `Cosha.fbx` hashes stayed unchanged.

Persistent evidence:

- X: `task_artifacts/animation_roundtrip/link_acceptance_20261001/blender_acceptance.json`
- X: `task_artifacts/animation_roundtrip/link_acceptance_20261001/reference_frame_acceptance.json`
- Unity: `Logs/CharacterTuning/AnimationLink/verification.json`

Production Cosha remains on Default. The edited demonstration Walk is a candidate
for explicit review and Apply. This is one Walk workflow; an animation library,
batch system, general retargeting and a new authored-Action acceptance are outside
this release's integration scope.

The current Cosha Avatar has translation DOF disabled. Unity warns that shin/foot
translation tracks are discarded during Humanoid conversion; this is not a
lossless joint-translation return. The existing Avatar configuration is preserved.

Local package: `dist/character_designer-0.68.0.zip` (121 shipped files).
SHA-256: `3e2b111a31aece6d3374ea0c5e844a44345e3e6ac4c8137e322cd1c8389e39f1`. The Blender 5.2 installed copy and X validation
copy both passed module-only deployment `--check` with zero mismatches. The current unsaved Blender session is preserved; installed files do not
reload an already running add-on. Save normally and reopen Blender to activate
the new buttons. No Git commit, push or published release is performed.
