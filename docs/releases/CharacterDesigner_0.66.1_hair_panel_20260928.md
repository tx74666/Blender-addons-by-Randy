# Character Designer 0.66.1 — Compact Hair Bones

Hair Bones now displays the source, one strand-selection/refresh button, captured
count, Bones per Chain and Bind Hair to Character. Removed the From Selected Tips
entry, local rig override UI, duplicate rig/head labels and general instructional
lines. Existing remove/legacy cleanup and Edit Source actions remain contextual.
Selection-limited scripting and old clear-capture operators remain compatible.

The missing Bind button was caused by stale capture detection setting the strand
count to zero, followed by drawing both bone count and Bind only for nonzero counts.
Those controls now stay visible, with Bind disabled until capture and target are
valid. A stale capture presents Refresh Hair Strands, which actually rediscovers
the current mesh and replaces capture metadata after all validation passes. Failed
discovery/validation preserves the old record; it never clears the record first.

New bindings read Main Rig and Head from Character Setup, ignoring the retired
local override even if an older session still stores it. Existing attachment
validation and mismatch reporting continue to use the saved binding.

Validation on Blender 5.2.0: Hair UI/operator suite (7 checks), saved accessory
references (3), persistent groups (7), and an isolated 0.66.0→0.66.1 hotload followed
by the Hair UI suite. Coverage includes a real topology edit, refreshing from two
to three strands, actual binding/removal, failed refresh preservation, hidden
override precedence, save/reopen and registration lifecycle. Test processes ran
serially. No artist mesh, weights or bones were changed by these tests.

Live diagnostic confirmed Hair is unbound, its capture is stale and Character
Setup selects CoshaRig. Evidence is in
`D:\Blender\Projects\Character\X\task_artifacts\hair_panel_20260928`.
Both deployments and the 114-file release package were verified. Guarded live
hotload preserved artist data, selection, context, dirty state and the on-disk
blend file. The X panel was visually inspected: Refresh Hair Strands, one concise
stale-capture message, Bones per Chain and the disabled Bind button are visible;
the redundant controls and instructions are gone. The live capture remains stale
until the artist explicitly refreshes it.
Local source/deployment only; no commit or push.
