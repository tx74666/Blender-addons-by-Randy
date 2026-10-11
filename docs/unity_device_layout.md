# Unity device layout references

RR Helper imports a Unity device layout snapshot into Blender to help position
building geometry around devices. The imported collection is a reference and
stays out of RR Helper model exports.

## Default screen appearance

Imported device screens now use a plain, opaque gray material. They retain the
screen size, position, orientation, and device identity from the snapshot. The
screen does not show Unity text, buttons, or a captured UI image, and its material
does not emit light.

Screen preview PNG files are not needed by the Blender import. An older snapshot
can still provide the screen dimensions and transform after its screen preview
PNG has been removed. This only applies to the screen preview: textures referenced
by ordinary device hardware materials are still loaded and must remain available.

## Import or update

1. Open **RR Helper > Unity Layout** in Object Mode.
2. Set **Snapshot** to the Unity device layout JSON file.
3. Choose the building **Core Object**, or use the existing reference object.
4. Click **Import / Update**.

Importing the same reference identity updates its managed collection. Matching
device roots retain their Blender object identities; screen dimensions and
placement follow the updated snapshot. Existing managed screens become gray when
the layout is updated. Artist objects and other device hardware textures remain
independent of the screen appearance.

For screens already in the scene, **Blank Imported Screens** replaces only the
managed screen overlay materials. It does not require a new Unity snapshot or
reimport, and keeps their current geometry, placement, identity, and selection.
The action supports Undo. Shared original materials and images are preserved;
it does not change an artist object that happens to use the same data.

Use **Hide References** to hide the imported reference collection. Updating the
layout does not save the Blender scene or export a model.

For screens already imported with text or buttons, click **Blank Imported
Screens** in the same panel. This replaces only the material on RR Helper's
managed screen overlays. It does not need the original snapshot or screen PNG,
and preserves screen objects, meshes, transforms, and selection. Hardware and
artist materials are untouched; old shared materials and images are retained.
The action supports Blender Undo.

## When a Unity device changes

Export a new layout snapshot from Unity, then choose that snapshot and click
**Import / Update** in Blender. This is an explicit update; RR Helper does not
watch Unity or automatically modify the Blender scene in the background.

Changes to text and buttons do not need to be reproduced in Blender. Update the
snapshot when device geometry, screen size, or placement changes.
