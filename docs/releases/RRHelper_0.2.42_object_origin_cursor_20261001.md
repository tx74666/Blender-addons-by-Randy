# RR Helper 0.2.42: Object Mode origin to cursor

Apply to Selection was disabled for ordinary objects in Object Mode because
the existing selection resolver accepted only Edit Mode elements or Empties.
It now accepts selected Mesh and Curve objects in Object Mode and moves their
origins to the world-space 3D Cursor, keeping the visible geometry in place.

## Behavior

- Object Mode, Mesh / Curve selection: move each origin to the 3D Cursor.
- Object Mode, Empty selection: retain the existing cursor / active target
  behavior and child protection.
- Edit Mode: retain the selected mesh elements / curve points behavior.
- Bottom rule and Point Bookmarks keep their existing behavior.

The Object Mode branch preserves the object's linear transform, including
shear under nonuniform parents. Child transforms and local channels remain
unchanged. All shape keys are transformed with the base data; shared or linked
geometry is copied only for the selected object. Editable single-user geometry
keeps its original datablock identity.

The operation stages geometry before committing the change and verifies world
transforms and evaluated geometry. A batch failure restores all affected data,
origin locations and child parent inverses. Controlled object transforms and
unsafe dependencies produce an error rather than silently changing geometry.
Origin-dependent modifiers such as an own-origin Mirror may require choosing
an independent modifier reference before the origin can move safely.
Geometry Nodes / particle instances are rejected before staging, because a
mesh-only geometry comparison cannot establish that generated instances stay
in place. Ordinary Geometry Nodes passthrough remains supported.

## Validation

**Passed isolated Blender 5.2 validation, packaging and local deployment.**

- 17 Object Mode regressions: edge-only Arc, production operator and UI
  availability, parent shear, selected parent / child batches, unchanged
  descendant channels, shared and linked geometry, all shape keys, Bezier
  handles / NURBS weights, safe modifiers and Geometry Nodes passthrough,
  instance rejection, complete staging / commit rollback and temporary-data
  cleanup.
- 67 existing Empty and Edit Mode origin regressions.
- 170 canonical add-on registration / refresh / disable checks.
- All eight `Invoke-ExporterContracts.ps1` stages passed: exporter (72 tests),
  bookmark lifecycle, registration (170 checks), preview selection (82 checks),
  icon lighting (15 tests), lighting integration (61 checks), standard export
  transaction (29 tests), and editable surface text (15 tests).
- All three `deploy_local.py --check` commands and
  `Sync-RRHelperAddon.ps1 -Check` passed with no source mismatch.

The first pipeline run exposed an outdated lifecycle test in the Unity mirror:
it expected one legacy menu callback and a runtime index for native v2 Mix
Shaders. That test was synchronized with the already validated repository
version, including Ring Group registration / removal assertions, and the
complete eight-stage runner then passed. Its previous source and both run logs
are retained for recovery.

Evidence is under
`D:\Blender\Projects\Build\Recovery\ObjectOriginCursor_20261001`.
No validation opened or changed the working Builder6 scene. **The running
Builder6 UI has not been refreshed or clicked by this task**; UI availability
was checked using the production draw method in an isolated scene.

## Source and deployment

Runtime source remains in `addons/random_realm_builder_exporter`.
[rr_helper-0.2.42.zip](../../dist/rr_helper-0.2.42.zip) contains 19 files and
matches the canonical source. Its SHA-256 is:

```text
54d67cef14d33c93dc4ae592ba0341fb16480b1a4b9ae1b77f8f4aeb12a109c6
```

Local deployment copies match the repository in the Blender 5.2 user add-ons
directory, Blender 5.2 `scripts/addons_core`, and the Unity pipeline mirror at
`Tools/AssetPipeline/Blender/addons`. Installation backups are retained by
`tools/deploy_local.py`. This change does not modify Unity scenes, export assets
or personal node-library assets. Character Designer 0.69.0's archive and
checksum entry were preserved.

An existing Blender window must use **F3 > Refresh Add-on** to load the updated
Python code. No Git commit or push is performed by this task.
