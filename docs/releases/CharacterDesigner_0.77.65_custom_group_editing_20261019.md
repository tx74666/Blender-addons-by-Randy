# Character Designer 0.77.65 · Custom group editing

Delivered locally, 2026-10-19: C4 passes 15 native core cases, five native GUI
history cases and full add-on registration lifecycle. Actual inline editing was
verified in an isolated GUI. The package is deployed, the artist refreshed from
`.64` to `.65`, and native Ctrl+S showed `Saved "X.blend"` with a clean title.
No independent native artist readback was run.

**Modeling → Custom Vertex Groups** gains the familiar group controls. `+`
immediately creates an empty `Group`, without a dialog; subsequent names receive
readable suffixes. The name field supports double-click editing, with
the `Custom_` storage prefix kept out of the field. Assign/Remove edit group
membership; Select recalls visible members and Deselect subtracts visible
members while keeping other selected vertices. Hidden vertices stay hidden.

Each row has a lock. Locks prevent Assign, Remove and Clear; selection, rename,
manual ordering and explicit deletion remain available. Up/down changes saved
group order. Search and filter inversion control visible rows, while A–Z and
reverse sorting affect the displayed order only. The dropdown's Sort by Name
changes saved order. Its other actions are Duplicate Group, Clear Group,
Delete Empty Unlocked Groups, Delete All Unlocked Groups, Delete All Groups,
Lock All, Unlock All and Invert Locks. Clear requires Mesh Edit Mode; explicit
Delete All also removes locked groups.

Groups remain independent of deform groups and painted weights. Native
`Custom_` Point/Integer attributes are the catalog and membership source; existing
matching attributes are treated as groups under this reserved namespace. Lock
markers are hidden native integer attributes. Rename keeps their association,
and ordering moves names with their exact integer payloads. The WindowManager
list is a disposable view derived from current native data, not a second Mesh
registry. Its rename callback invokes the normal Undo operator explicitly.
No Mesh registry, topology monitor, timer or Undo handler is added. There is no
falloff control, Geometry Nodes hookup or performance/FPS claim.

C4 retains 203 accepted `.64` files byte-for-byte, including the animation-list
changes. Only the initializer and custom-group module differ in the 205-file
projection. The initializer changes version, property lifecycle and Menu RNA
registration recognition. Paused `body_region`/`bone_display` changes remain
excluded.

- Manifest: `f9d44504bdd43acb03ad5109f20abc9f3f257fc4e7a051a7364e7542f50bd930`.
- Initializer: `0d678e1ed7ab4f5596962d5fbc563538255de8ddf859c209117bc993ca6df047`.
- Custom-group module: `2f0efa83e8b474923748d60dd0809d405cbad26f6bb3fa93c8d1e21afdb3797d`.
- Selected test file: `c39528926d7b525d5b6d0a5e0eee44f5b86dd079253220f61bb1e424314c2bf5`.

The 15 core cases have zero failures, errors or skips. They cover group editing,
locks, ordering/menu operations, filtering, save/reopen and exact preservation of
fixture geometry, painted weights/locks, Shape Keys, UV/pins and foreign
attributes. Full registration after fixture teardown passes repeated register,
unregister/re-register, native group persistence, Modeling-only panel and owned
timer cleanup, with preference membership unchanged. This is disposable fixture
validation on Blender 5.2.0 LTS `fbe6228777e7`, with trusted scripts and isolated
CONFIG. The five GUI history cases also pass with zero failures/errors/skips;
the expected stale-row refusal callback trace remains in stderr. Actual AI
model/effort unrecorded.

The isolated UI check clicked `+` to create `Group.001` immediately, double-clicked
the field to rename it `UI_Renamed`, and exercised Ctrl+Z plus the Edit menu's
Redo. Fixture author hashes stayed exact. The tool's attempted Ctrl+Shift+Z
delivered a second Undo; two menu Redo operations restored the result. This is
not a physical-shortcut Redo success claim. The original UI harness failed on a
read-only region-category assignment; its failed evidence is retained beside
the corrected v2 harness. Quick Setup's Continue wrote preferences only inside
the dedicated isolated CONFIG; the artist's real preferences were not written.

Official ZIP SHA256:
`7e9e957f8ff79ad91926cfeffedb1c7b208f6b656b64a1c48f7d45be43ffd15f`.
Blender 5.2 and X/addons deployment checks each match all 205 files, with zero
differences and two updated files per target. Backup:
`C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261019-064031-3ede065e`.

Live refresh passes the 65 author-input checks, exact custom-group attribute/
catalog preservation and fresh current-output recomputation. Receipt SHA256:
`d4ed4dbb1ae0f6b332b4016dfaf95018beaf2e2467b08c2998142a267a589466`.
The four existing artist groups remain `Strap1_Down`, `Strap1_Up`, `Strap2_Up`
and `Strap2_Up.001`; no artist group was created or renamed for testing. All
native validation jobs owned by this work exited normally; artist PID 27952
remains. Native fixture save/reopen does not substitute for an artist readback.

Earlier Attribute-property feasibility probes failed and were abandoned in
favor of the derived WindowManager view. C1/C3 core cases passed but their full
Menu registration checks failed; those reports remain failures. C2 was unused.
No commit, push, staging or PR was performed for this work.

Completion evidence and limitations:
`D:/Blender/Projects/Character/X/validation/custom_vertex_groups_editing_20261019/FINAL_README.md`.
Final receipts: [acceptance summary](D:/Blender/Projects/Character/X/validation/custom_vertex_groups_editing_20261019/acceptance_summary.json)
and [artist save](D:/Blender/Projects/Character/X/validation/custom_vertex_groups_editing_20261019/artist_save_receipt.json).
