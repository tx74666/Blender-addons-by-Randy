# RR Helper 0.2.49: inspect conflicts and relink a Unity asset

Copied meshes can retain an exported object's identity even when renamed or
hidden. Export previously stopped with a report that did not provide a repair
entry point. Export Queue now has **Diagnose / Relink** and **Open Export Folder**;
identity-conflict popups offer the same actions, including early Core validation
failures. **Copy Diagnostics** provides readable source, route and conflict data.

Choose the existing named asset folder inside the current Unity export
directory. A confirmation shows the Blender object, Unity asset and any copies
sharing that identity. Listed copies become independent only when explicitly
approved. The selected object retains its artist name and geometry; a saved
export-name override keeps subsequent exports routed to the chosen asset folder.
Native artist renames retain that route. **Use Object Name for Export** explicitly
returns to name-based routing while retaining the stable identity and old alias.

Relinking changes Blender metadata only. Export again to publish the edited
model. It preserves Unity files and GUIDs during relinking. The package reader
checks manifest identity, folder, safe model path, existing nonempty model,
provided SHA-256 contracts and publication markers. Preview and apply recheck
package fingerprints and observed object state; existing all-scene validation
rejects newly introduced conflicts and restores all touched metadata on failure.

This first repair workflow supports Standard independent asset packages.
Managed assemblies/Variants, their members, linked/read-only roots and protected
Core peers require their existing ownership workflow. Choosing a different output
directory is not implicit: change the export folder first. A duplicate cannot
take over an actual Core owner's identity; the dialog identifies the Core object
to select instead. Stale copied Core markers are cleared from non-Core roots.

## Validation

- 17 pure tests cover package contracts, explicit copy plans, stale previews,
  aliases, protected owners and metadata rollback.
- 16 exporter feedback/UI checks cover actionable conflict popups, Core failures
  and unchanged Standard/Modular export choices.
- Eight isolated Blender 5.2.0 LTS checks pass: actual Core pointers and mesh data
  preservation; copied identity separation; real staged Standard publication and
  manifest SHA with a small stand-in FBX writer; model.meta GUID preservation;
  bound native/group rename; native duplication cleanup; route and ownership
  refusal; stale plans and Use Object Name rollback. The disposable process exited.
- No production Unity assets are written by these tests. Unity runtime behavior
  is not tested by this release.

Evidence: `D:/Blender/Projects/Build/WIP/Validation/unity_asset_relink_20261004/`.

## Local deployment and live verification

The 0.2.49 archive contains 22 shipped files. Both the Blender 5.2 user add-ons
copy and the verified active `D:/Blender5.2/5.2/scripts/addons_core/` copy match
canonical sources. Builder6 loaded 0.2.49 through the existing refresh mechanism;
the loaded module path and all shipped source files were checked.

The native UI check opened Diagnose / Relink, selected the existing named Floor
asset folder in the directory picker, inspected the pairing confirmation and
canceled it. The original Elevator selection was restored. Artist object names,
mesh data pointers/counts, transforms, identities, queue, Core and output route
match the pre-check snapshot. A read-only pairing plan also left the scene
unchanged. Refresh performed its normal cleanup of the add-on-owned
`RR_IconPreviewCamera`; the initial strict snapshot failure is retained alongside
the final verified report. Builder6 was already dirty and was not saved by this
feature check. No production pairing or export was applied.

Changes remain local. No Git commit, push, PR or remote release is included.
