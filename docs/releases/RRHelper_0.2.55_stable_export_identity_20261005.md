# RR Helper 0.2.55 - Stable export identity

Work period: 2026-10-05 through 2026-10-06.

Ordinary Standard assets now retain their export identity and package route
when the artist renames the Blender object. The first Standard export preparation
records the route, even if a later FBX write fails; later exports reuse it.
The manifest's `sourceObject` continues to
follow the current artist name and the current exported FBX node. Geometry,
materials and artist names are not changed to preserve the route.

Keeping `manifest.id`, the package directory and `model.fbx` location stable
lets Unity's existing Standard publisher update the same Prefab and model
assets with their existing `.meta` GUIDs. Unity's Prefab display name and asset
path may therefore retain the original package name while Blender shows the
new artist name. This is a route-preservation contract, not a guarantee that
renamed FBX child nodes or Mesh subassets retain their internal fileIDs.

## Ownership and copies

Export ownership is stored persistently through the Scene, outside the custom
properties duplicated with an authored Object. An unlinked, data-only Empty
carrier stores muted, zero-influence `COPY_LOCATION` constraints whose native
weak targets identify the original Objects. The carrier has no collection
link, evaluated scene presence, rendered geometry or asset output. It does not
move the authored Object or prevent native deletion. Saving the `.blend`
normally persists the ownership record; deleted owners leave a tombstone so
a remaining copy cannot silently inherit their identity.

Copies receive independent identities automatically only when an owner is
known. Older files with a shared stable ID and no established owner remain
blocked by identity diagnostics until the artist chooses the original.
Names, geometry and export histories are not used to guess an ambiguous owner.
Previous-ID aliases that still claim another live asset remain conflicts.

## Resolve an older shared identity

1. Click **Refresh Add-on** to load the installed runtime.
2. Select the original object that should retain the existing Unity asset.
3. Open **Export Queue > Diagnose / Relink** and click
   **Keep This Object's Identity** once.
4. Export again when ready. Save the Blender file normally when the association
   should be retained on disk.

The button appears for an editable independent object with a nonempty shared
stable ID. It immediately keeps the selected original's identity and old
route, assigns independent identities to the copies, and supports Undo. It
checks the native Object UID before applying the change. The existing folder
relink workflow remains available for choosing a different asset association.

Managed-group and linked/read-only ownership guards remain in place. The
actual scene Core can be retained as the selected original, but the operation
does not detach a protected Core peer or redirect the scene's native Core
pointer. A failed repair rolls back metadata and ownership together.

The user selected **Hub_Wall_Circular_A** as the original to keep its previous identity and
**Hub_Glass_Circular_A** as the copy to make independent. Live Blender RPC was unavailable,
so this task did not rewrite or save the live scene. The user still needs the
Refresh, Wall selection and one-time button action above.

**Use Object Name for Export** remains an explicit choice to adopt a new
package route while tracking the identity and previous name. The default
retained-path GUID guarantee does not extend to this deliberate route change;
Standard does not automatically migrate Unity asset paths.

Deleting an object ends its ownership. A new Object with the same name gets a
new stable ID. If its proposed folder still contains a different identity,
export stops before writing files; rename the new object or deliberately choose
the old Unity asset through the existing folder relink workflow. A matching
display name alone cannot grant permission to replace a different asset.

## Validation and delivery state

- Persistent ownership tracking: **19 checks passed**, including native
  deletion, save/reopen, renamed routes, and recreated-name overwrite protection.
- Identity repair: **8 checks passed**.
- Linked/shared registry: **5 checks passed**, including native Scene.copy
  with a shared carrier, conflicting live owners across Scenes, read-only
  linked constraints, preflight refusal without partial writes, and rollback
  that preserves the original injected exception.
- Existing exporter regressions: **55 checks passed**, including real FBX/UV,
  source snapshots, Standard publication and Modular variants.
- Export feedback: **16 checks passed** in the final run.
- Final syntax and scoped whitespace checks passed. The final native runs
  total **87 checks**; export feedback adds **16 pure checks**.

The official `tools/build_releases.py --module random_realm_builder_exporter`
built `dist/rr_helper-0.2.55.zip`: **27 files, 241,748 bytes**, SHA256
`70580e3b5a6cb339bebc203cc36ee04c7ece39cf68a825e8ff1d62f3008a6181`.
Compared with frozen 0.2.54, the runtime changes are confined to `__init__.py`,
`rr_builder_constants.py`, `rr_export_identity_ui.py`, and the new
`rr_export_identity_tracking.py`.

On 2026-10-06, official deployment and `--check` verified **27 shipped files,
zero differences** at both Blender 5.2 targets. Existing non-source backup files
were retained and recorded separately; they are not importable Python modules.

- `C:/Users/Randy/AppData/Roaming/Blender Foundation/Blender/5.2/scripts/addons/random_realm_builder_exporter`
- `D:/Blender5.2/5.2/scripts/addons_core/random_realm_builder_exporter`

The Unity AssetPipeline portable add-on was synced with
`Sync-RRHelperAddon.ps1` and its `-Check` passed. Deployment backups are
`20261006-003452-3c96ac16` and `20261006-003456-cc087940` under
`C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy`.
Raw logs and the delivery fingerprint live in
`D:/Blender/Projects/Build/WIP/Validation/export_identity_tracking_20261005`.
All task background Blender processes exited; the two existing user GUI
processes were retained. There was no new performance or memory benchmark.

Unity Standard routing was inspected read-only. No Unity source was modified,
no Unity compilation was started, and no production Unity reimport was
performed. The user's live Blender scene was not refreshed or saved by the
validation. Git commit and push remain with the user.
