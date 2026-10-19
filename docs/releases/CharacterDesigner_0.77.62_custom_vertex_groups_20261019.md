# Character Designer 0.77.62 · Custom Vertex Groups

Local delivery status, 2026-10-19: 13 selected native cases and full lifecycle
pass; the official package is deployed and the artist refreshed to `.62` with
65 author-input checks and exact custom attributes/current corrections. Artist
state was saved by native Ctrl+S, followed by a fresh clean-title observation
and stable disk hash. No independent native artist readback was run.

**Modeling → Custom Vertex Groups** provides named selection groups independent
of deform vertex groups and painted weights. `+` creates an empty group; select
vertices in Edit Mode and use Assign. Remove writes them out of that group;
Select replaces the selection with visible members and switches to vertex mode.
Hidden vertices remain hidden. `−` deletes the group.

Membership is a selection flag (Assign `1`, Remove `0`), with no falloff, weight
transfer or automatic new-vertex interpolation. The reserved native `Custom_`
Point/Integer namespace is the catalog itself; existing matching attributes are
listed, with the prefix hidden in UI labels. Other types/domains stay outside
the catalog. New avoids existing attribute names, supports readable Unicode
labels, and creates no duplicate Mesh registry, Mesh property or runtime handler.

Frozen C4 has 205 files, retaining 203 accepted `.61` files exactly. The only
initializer changes are version/import/new Modeling panel and two class-list
entries; register/unregister and all other existing functions remain AST-exact.
Manifest SHA:
`4e85071450995f5b2f4097d07723d8f5fd3cbdca319aa758243d508f0b7afb6a`.
Unaccepted `body_region`/`bone_display` changes remain excluded.

Eleven native core checks pass with zero skips, failures or errors, including
save/reopen and strict author geometry, weight/lock, Shape Key, UV/pin and
attribute preservation. A separate full-register phase after fixture teardown
passes repeated register/unregister/re-register, native data persistence,
Modeling-only panel and class/timer cleanup without changing preference membership.
Both native GUI operator history checks pass, including Edit New/Delete catalog
and membership restoration together and Assign Undo/Redo with author inputs
unchanged. No physical-keyboard Undo/Redo coverage or performance/FPS improvement
is claimed.

Earlier UV fixture failures remain preserved separately from the real Edit New
Undo bug found in C3: its duplicate Mesh registry survived when the BMesh
attribute was undone. C4 removes that duplicate state and keeps catalog/members
in native attribute data. The final GUI history checks pass while that failed candidate remains preserved.

Validation is on Blender 5.2.0 LTS build `fbe6228777e7`, using trusted scripts and
fresh isolated CONFIG directories. Actual AI model/reasoning effort unrecorded.
No Git operation, commit or push. Evidence and completion status:
`D:/Blender/Projects/Character/X/validation/custom_vertex_groups_20261019/FINAL_README.md`.

Official ZIP SHA `93096d47cc0766e94f8a46f072434fb7f1c4b4046a1080dbd4d1ed53a516a376`; both Blender 5.2 and X installation directories match all 205 files with zero missing/extra/changed shipped files. One initializer and one new module are deployed per target. Backup: `C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261019-022818-045f3f61`; both old initializers match accepted `.61`. The latest current artist state was saved through native Ctrl+S; fresh title-clean and stable disk/copy hashes confirm completion. No Saved message bubble was captured or claimed.

Artist disk/copy SHA `2d74a5f4b8872b5a87b5496da382c41b807b2c54dc63ab75db9744f15316b0ad`, 34,487,240 bytes, local save time 2026-10-19 02:57:18. Original ImagePaint layout restored; the Modeling panel was visible with no group/membership created in the artist scene. Only the existing artist process remains. Receipt scope distinguishes actual Ctrl+S/title/disk evidence from the fixture native save/reopen check; no artist native readback is claimed.
