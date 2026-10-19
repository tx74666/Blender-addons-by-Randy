# Character Designer 0.77.70 — Body-derived Unity export name

Unity Export no longer asks for a separate Name. The object selected as
**Character Setup > Body** provides the model name: Body `Cosha` exports as
`Cosha.fbx`. The mesh datablock name, Main Rig name and old filename override
do not override this choice. Existing filename RNA remains hidden for saved
data compatibility; no migration rewrites the old value.

Model export and Unity Material matching share the same validated name source.
A pending material picker cancels if its Body model name changes. It ignores
changes to the obsolete filename field. Generic filename validation used by
animation export is unchanged. Missing, wrong-scene or wrong-character Body
references are refused, and export requires the Body in the bound inventory.

The per-rig asset UUID, folder and output ownership checks are retained.
Foreign/unowned files, external changes and publication races remain protected;
Unity `.meta` files are never replaced. Renaming Body changes the next target
name; it does not silently rename or migrate previous Unity files or GUIDs.

Validation: 14 native Blender 5.2.0 LTS (`fbe6228777e7`) cases, including four
existing scope/publication/rollback/report regressions; 26 frozen Python panel
and Material-match tests; and six full registration lifecycle checks passed.
Tests use isolated fixtures and temporary output directories. No FBX worker,
actual Unity project, artist scene or real preferences were operated.

Accepted projection: X `validation/unity_body_export_name_20261019/candidate01`.
It contains accepted 0.77.69 plus four reviewed runtime files, excluding pending
Objects selection work and unrelated source changes. Installation and live UI
adoption status are recorded in that directory's parent acceptance summary.
Local source/installation only; no staging, commit, push or publication.
