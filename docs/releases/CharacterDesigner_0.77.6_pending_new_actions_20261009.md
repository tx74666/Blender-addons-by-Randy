# Character Designer 0.77.6 — prepared baselines and new Actions

Local release on 2026-10-09 for the RandomRealm COSSA / Carmen animation workflow.
Canonical runtime code was changed first under `addons/character_designer`.
Changes remain local, uncommitted and unpushed.

This release records a complete Source-content baseline while adding a prepared
Action. A fresh unedited Custom is Unchanged and is not re-exported. A real edit
exports the whole Action. Older entries without a reliable baseline remain
Unknown, and an invalid later publication cannot fall back to the Source proof.

Animation Worklist → New Action registers a local Action on the existing
workspace rig with stable creation/work IDs and verified model/Avatar/workspace
provenance. It has no invented Source or Original. Complete immutable FBX and
metadata pairs are published under the workspace's `new_actions/` directory.
Unity collection registers a revision; it does not select an application use.

The frozen release is based on the exact approved 150-file 0.77.5 projection.
It contains 151 files, changing only `__init__.py`, `animation_worklist.py`,
`animation_worklist_collection.py`, `animation_worklist_ui.py` and adding
`animation_worklist_new.py`. The 19 held canonical differences, including the
unapproved Dress export integration, are excluded.

Projection:
`D:/Unity Projects/RandomRealm2/.codex/task-staging/character-pending-crosspreview-20261009/blender-release-0776/release_projection.json`

Projection SHA-256:
`4e2390e1d7e2ea9ed73f9d9deacd47f7a78dd5728f42f9b72fca54ab79d57cbb`

Archive `dist/character_designer-0.77.6.zip` SHA-256:
`c61fd3be76a448250b800e1a97c301a3256551c39286959bb6a492256cc65703`

`tools/build_releases.py --module character_designer --projection ...` verified
the package. `tools/deploy_local.py` with Blender 5.2, the projection and
`--project-addons D:/Blender/Projects/Character/X/addons` updated five files in
each deployment. The subsequent `--check` reported version 0.77.6, 151 files,
zero differences and `RELEASE_PROJECTION_MATCH` in both installed locations.
Recoverable backup:
`C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261009-025727-4b1e8244`.

The persisted Blender 5.2 preferences contain a native enabled
`character_designer` record. Artist PID 27676 and its original start time were
preserved. The artist file/session was not refreshed, reloaded or saved;
its in-memory module version is unverified. Deployment alone does not prove
that the already running artist process uses the new version.

165 focused pure worklist/fingerprint/queue/link/new-Action tests passed before
deployment. Native Blender A/B/C export and Unity import checks use the preserved
private worklist fixture and are separate acceptance evidence. This release note
does not claim those real operations passed before their reports exist.
Exact receipts and subsequent reports live in the task's `blender-candidate/evidence/`.
