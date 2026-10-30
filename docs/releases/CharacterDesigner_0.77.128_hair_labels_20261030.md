# Character Designer 0.77.128 — Hair selection wording

Check Selected Bones replaces Import Pose Selection: selected Hair bones check
their corresponding rows in the list. Select Checked replaces Checked to Pose
Selection: checked list rows select their Hair bones. The operator search name
and enum help use the same wording. This release changes strings only, with no
handler, identifier, scope, selection or native-data behavior change.

The complete approved127 package is retained with three explicit changed files
(UI labels, version and README). Exact-byte replacement and dry-draw checks plus
an independent AST review cover the wording. Existing127 native/Undo protection
receipts remain applicable. No new Blender process, live refresh, scene or
preferences save, Unity or Git action is needed for this disk-only release.
