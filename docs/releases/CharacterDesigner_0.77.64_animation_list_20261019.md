# Character Designer 0.77.64 — native animation list

Animation entries now appear inside one native Blender UIList, with equal
Source/Custom columns and five visible rows. Names use flat cells rather than
raised buttons. Native scrolling and a name filter keep longer worklists
contained; filtering preserves saved order and is independent of the old catalog
search. Clicking a cell retains the existing stable-ID activation/resume path.
Passive row selection and file loading do not switch Actions.

Canonical feature changes are in `addons/character_designer/animation_worklist_ui.py`.
The local release uses a hash-locked projection of accepted .62 plus that UI
module and an initializer version change. The other 203 shipped files are
unchanged. Pending canonical changes, including the .63 custom-group editing
lifecycle, are excluded. The canonical initializer retains those pending edits;
only its version was advanced for this release.

Validation: 75 Worklist UI tests and 20 prepared-rigging UI tests passed.
An isolated trusted Blender 5.2.0 LTS process passed native registration,
unregistration and passive-index/name-filter checks on 30 disposable entries,
plus 16 existing native Rest projection regressions. The first attempted native
test invocation under system Python could not import Blender's API; the proper
Blender run passed. Factory preferences report automatic-script preference off,
but this validation process explicitly used `--enable-autoexec`; no artist
preferences were written. No performance benchmark is claimed.

The release ZIP was built locally. Blender 5.2 and X's deployment copy both
passed `deploy_local.py --check --projection ...` with zero differing files.
The active artist was observed as `X.blend` in Blender 5.2.0 LTS. The ordinary
Refresh Add-on button loaded the new list; Walk_N appeared in both columns.
Ctrl+S then produced Blender's native `Saved "X.blend"` confirmation. Artist
Actions/weights were not edited for testing. The live UI check does not claim
a full saved-file replay or a new weight-array equality audit.

Local evidence: `D:/Blender/Projects/Character/X/validation/animation_list_20261019/`.
Its `release_projection.json`, `native_launch.json`, `native_result.json`,
`live_ui_saved.png` and `acceptance_summary.json` identify the inputs and results.
Changes are local and uncommitted; no commit, push, PR or remote release.
