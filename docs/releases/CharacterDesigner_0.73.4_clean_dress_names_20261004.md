# Character Designer 0.73.4 — clean existing Dress names

Date: 2026-10-04, Asia/Shanghai. Owner: X / Character Designer. Model and
reasoning effort: unrecorded. Changes, package, deployment and artist save are
local; no commit, push, PR or published release was made.

Existing Dress bones and generated Hook labels retained a random ownership token
after helper objects had received readable names. Version 0.73.4 removes that
token from validated owned bones and Hooks, including existing shared-main-rig
setups. Examples:

| Before | After |
| --- | --- |
| `SK_Dress_857c5a_DEF_08_01` | `SK_Dress_DEF_08_01` |
| `SK_Dress_857c5a_Waist` | `SK_Dress_Waist` |
| `Control SK_Dress_857c5a_Hem_07` | `Control SK_Dress_Hem_07` |

New Dress generation reserves a readable source prefix and uses `_02`, `_03`,
and subsequent numbers when that prefix is occupied. Internal ownership UUIDs
remain stored as metadata. Existing object/data/collection cleanup continues.
Refresh and file-load cleanup run once after scene data becomes available;
repeating a completed cleanup is a no-op. No new panel or per-frame handler is
introduced.

## Protection

Cleanup uses exact recorded ownership and planned names. Blender's native bone
rename updates native constraints, Hook subtargets, bound vertex-group names
and supported animation paths. Typed saved-record updates retain Dress layout,
active Original recovery, local pose corrections, Bone Display snapshots and
attachment recovery. Arbitrary prose and custom Hook labels are retained.

Preflight refuses conflicting bone or bound-mesh group names, linked/overridden
data, multi-user armature data, unsupported Actions, active incompatible recovery
operations and animation/driver references to renamed Hook labels. Refusal does
not force an approximate mapping. Failed post-write validation reverses native
names, Hook labels and the exact old saved-record text.

The operation writes names and name selectors only. It does not move vertices,
change Shape Keys, UVs, weight values/group indices, Rest bones, authored pose
channels, rig relationships or colors. It does not choose a symmetry repair
direction or perform an Original / Controls transfer.

## Validation and limits

Canonical source compilation and 15 Blender 5.1.0 native checks passed: four
Dress-name transaction tests, seven generated-name regressions and four deferred
cleanup/lifecycle tests. The four transaction tests cover native refs and
animation, bound-mesh group collision refusal, injected failure rollback,
active Original corrections and Palette recovery across save/reopen.

On the isolated X input saved on 2026-10-03 at 22:57, cleanup renamed **115 bones
and 48 Hook labels**. Raw protection passed across 628 objects, 598 meshes and
four armatures. Before save, evaluated position error was zero across 71 meshes,
and evaluated matrix error was zero across 2,420 bones. Repeating cleanup
produced zero events.

Save/reopen completed, but the original strict overall report remains **failed**:
four extra-ID `use_extra_user` flags changed from True to False on native
save/reopen. These belong to three images and the Front reference object's data.
Reopened structured model/reference state contains no other differences. The
review records these exceptions without replacing the original failed report.
Full real-scene post-reopen evaluated-world and byte-exact recovery-text checks
did not run after that assertion; dedicated native tests cover those paths.
This isolated input is older than the later live artist baseline.

Evidence remains in [X validation](D:/Blender/Projects/Character/X/Validation/skirt_names_20261003):

- [Four transaction tests](D:/Blender/Projects/Character/X/Validation/skirt_names_20261003/native_names_4_run3.log), [seven regressions](D:/Blender/Projects/Character/X/Validation/skirt_names_20261003/generated_names.log), [four lifecycle checks](D:/Blender/Projects/Character/X/Validation/skirt_names_20261003/names_lifecycle.log).
- [Original strict report](D:/Blender/Projects/Character/X/Validation/skirt_names_20261003/real_saved_names_run5.json), [scoped review and limitations](D:/Blender/Projects/Character/X/Validation/skirt_names_20261003/real_saved_names_review.json).
- [Live save facts](D:/Blender/Projects/Character/X/Validation/skirt_names_20261003/live_save_result.json).

No GUI latency, FPS or memory benefit was measured in this naming task.

## Deployment and artist save

The 135-file `character_designer-0.73.4.zip` was built. Package SHA256:
`90c47a2a38de602c5097086557eb1a646b3c50ffd127116df03963e5db7dbb1d`.
Blender 5.1 user installation, Blender 5.2 user installation and the X project
validation copy match canonical source (`--check`: zero different files).
Blender 5.2 received deployment verification only, without a new native test run.

The active artist window uses **Blender 5.1.0**. It was saved before Refresh,
with a byte-exact pre-change backup at
`X/Validation/skirt_names_20261003/before_live_change/X_before_names.blend`.
That backup is the 2026-10-04 00:06:50 live baseline, 32,316,581 bytes, SHA256
`1131f4021db5805e52e3bbd39127e6fb50d24141f9cafb5b778732d11b4b748e`.

Native Refresh completed. The Outliner showed the cleaned `SK_Dress_DEF_08_01`
bone, its cleaned `SK_Dress_Waist` parent and matching bound weight-group name.
The temporary search was cleared and the Rig panel restored. Original remained
selected with Body and Dress visible; no test rotation or mode transfer was
applied to the artist scene.

Native File > Save displayed **Saved "X.blend"**. The final artist file is
`D:/Blender/Projects/Character/X/X.blend`, saved on **2026-10-04 00:29:06.453 +08:00**,
32,315,483 bytes, SHA256
`26a6eb77bc91984092f81d60c2b8e830e7fa571de1a373894ccb53e473361de5`.
This live result has UI and disk save evidence, not an additional full background
readback of the final artist file. The isolated validation copy was not saved
over the artist file. All task-owned background Blender tests have exited and
the foreground/input boundary has been released.

Further investigation should target a newly generated unreadable name,
ownership/collision refusal, changed linked/animated setup or a new reference
failure. Reuse this evidence instead of repeating all earlier scene checks.
