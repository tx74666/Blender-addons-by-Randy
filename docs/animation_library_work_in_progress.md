# Unity animation collections — work in progress, 2026-09-20

Not a release. X's deployed Blender add-on remains **0.61.39**. Do not package or
deploy these Blender changes until the integration gates below pass. No commit
or push has been requested.

## Shelved for the separate finger-eye hotfix

The user prioritized persistent joint monitoring. All animation draft runtime
files and the modified legacy UI test are preserved under
`.codex-drafts/animation-library/` at the canonical repository root. Only the
four edited existing animation runtime files were restored from the verified
0.61.39 archive; new unvalidated modules were moved out of shipped `addons`.
The main addon may now receive a finger-only version beyond 0.61.39. Resume
animation by comparing this draft against that version, not blindly replacing
the whole addon. Unity's validation deployment remains unchanged by shelving.

## User clarification

“Visual Mirror” is not a separate object or subsystem. The requirement is a
visible, correctly mapped bilateral animation. Do not create a mirror skeleton
or force both sides of a walking pose to be identical.

## Implemented draft

- `animation_library.py`: Scene-persistent collections and embedded source Text
  datablocks; stable source/revision identities, per-target native Action cache,
  unattached imports, revision preservation, preview switching/restoration,
  membership-only removal. Uses existing Unity package validation and snapshots.
- `unity_animation_adapter.py`: existing exact mapping first, otherwise
  humanoid roles + configured Setup + hierarchy/bind landmarks, target lengths,
  root conversion, local translation transfer, conservative scale/unknown-driver
  rejection. Existing CD FK control graph routing; disposable evaluation copy
  only, native Action output. **Still needs numerical and visual validation.**
- `animation_library_ui.py` and existing Animation page integration: collection
  list, clip selection, add/read exchange, import selected, use/restore. Existing
  legacy importer operators retained, no longer the main page entry point.
- Unity existing transfer/window extended with Collect and batch send, optional
  Controller/resource scope, Avatar roles, actual weighted-bone evidence,
  project/source/clip IDs and content-addressed collection manifests. Reuses
  the existing safe PreviewScene sampler, not a new animation evaluator.
- Added isolated Unity source/batch validation and Blender library/native Walk
  validation scripts; updated the old UI test to expect the new primary entry.

## Evidence so far (do not overstate)

- Python compileall passed for the modified animation modules/new test.
- Existing `test_animation_runtime.py`: 2 tests passed.
- Unity compiled the new collection types/window and loaded the new validation
  class after an asset refresh; live reflection verified this. Console was 0
  errors/0 warnings after compilation.
- The validation menu was attempted once **before** its new file had been asset-
  imported. It failed with missing-menu diagnostics; no source test ran. The
  class is now loaded. The MCP menu inventory omits these Tools menu paths even
  though their C# MenuItem methods exist; do not confuse a missing inventory
  entry with a passing validation.
- Legacy actual X + old `Cosha_Walk.cdanim.json` baseline rejected mismatched
  character scale. That old file is already Cosha-evaluated, not generic Soldier
  motion; do not treat it as proof of Soldier retargeting.
- No new Blender library/native Walk/GUI tests have run. No new source Walk or
  batch export has run. No successful visual/action/skin comparison yet.
- Adventure remained unsaved. Disk SHA256 remained
  `A3BFA16B37F1CFDF706ECBE9ED80B04D4B3BF0063F84FC0555DD5EADFCE95889`.

## Resource gate

16 GB host repeatedly fell below 2 GB available (as low as 0.7 GB) after Unity
compilation. Do not launch new heavy jobs below 2 GB. Never overlap Unity import,
offline compile and background Blender. The completed Unity compiler servers
were shut down using its SDK `dotnet build-server shutdown`; no live Unity,
Blender or AssetImportWorker was terminated. Reclaim shared Unity ownership
before any new mutation after task handoff.

## Next checks, in order

1. When memory is sufficient, read editor state; verify the new validation class
   is loaded, then execute `Tools/Character Designer/Validation/Source Walk
   (Isolated)`. Inspect `RandomRealm2/Temp/CDAnimationLibrary/walk/result.json`.
   The fixture resolves the *actual Soldier Controller's* Walk by GUID/local ID,
   checks membership, repeat seeking and scene/source preservation. Production
   collection code contains no Soldier/Cosha special cases.
2. Use its manifest with `tests/test_animation_library_blender.py --actual
   <X.blend> <manifest> <output-folder>` in an isolated Blender process. Fix
   numerical/connected-joint/native Action failures before doing batch tests.
   Inspect native skeleton motion and actual mesh deformation/left-right mapping
   separately; the test's computed pose comparisons alone are not visual proof.
3. Run Source Collection isolated menu, test real Walk/Idle/Run switching, native
   Actions, edited revision preservation, second configured target, removals,
   duplicate imports, save/reopen, restoration and native Undo/Redo.
4. Run existing Unity animation/retarget Blender tests and the new synthetic
   library test. Test validated generated CD control graphs, not just plain FK.
   Check unsupported non-skeletal/ShapeKey clip channels explicitly; source-only
   skeletal packets must not silently discard significant non-bone animation.
5. Check registry atomic failure cleanup and modal job cancellation/scene changes.
   Verify GUI layout and interactions; distinguish RNA draw tests/offscreen
   renders from actual interactive GUI checks in the report.
6. Finish docs/version/release/build and deploy **only character_designer** from
   canonical source. RR Helper is managed by another task; do not overwrite it.

## Paths

- Canonical: `D:/MyRepository/Blender-addons-by-Randy/addons/character_designer`
- Blender: `D:/Blender5.2/blender.exe`
- Unity: `D:/Unity Projects/RandomRealm2`
- Source prefab: `Assets/Prefabs/Characters/Soldier/Soldier 4.0.prefab`
- Controller: `Assets/Art/Animation/Locomotion.controller`
- Walk GUID/local ID: `8269a9f8cf495034c817722ac21f309f` / `1657602633327794031`
- Unity companion was deployed for compilation/validation, not released. The
  final Preview Action change-close guard in canonical Window source still
  needs deploying and recompiling before release.
