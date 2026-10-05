# RR Helper 0.2.54 — Wire-only Surface Text samples

Surface Text's persistent sampling mesh now draws only as a wire overlay. It
does not cover the wall, cast shadows, contribute reflections or enter light
probe captures in rendered views. Keep viewport Overlays enabled to see the
helper lines. The wall and Font retain their original materials.

Click **Refresh Add-on** to repair existing editable sampling helpers. The same
repair runs once after opening a file or importing Blender data. Only objects
marked as owned sampling helpers are changed; names, geometry, transforms,
selection, visibility eyes, materials and text bindings are preserved. Save
normally when you want the repaired display flags stored in your scene.

Sampling helpers are excluded from asset geometry and icon preview lists.
Unity export still creates and exports an independent sampling mesh with the
existing manifest identity, then removes the transient mesh. A helper renamed
to its own reserved export alias is rejected with a rename message, so the
source cannot be lost or silently omitted.

## Validation

- Blender 5.2.0 LTS, factory-only: all 10 display/export regressions and all 15
  existing editable Surface Text contract checks passed. They include a real
  Shrinkwrap targeting the hidden sample, material/selection/reference
  preservation, linked helpers, FBX sampling alias round-trip and cleanup.
- Cycles and Eevee each rendered a 96 px fixture three times. A plain WIRE
  helper darkened the wall (maximum pixel difference 0.8941); the repaired
  helper matched the wall-only render exactly, even with `hide_render` reopened
  to test the renderer visibility flags independently.
- The registered load handler repaired an old helper in a disposable saved
  file. The deferred Refresh repair was idempotent, and unregister removed its
  handler and timer. Geometry, materials and identities remained unchanged.
- Evidence lives in
  `D:\Blender\Projects\Build\WIP\Validation\surface_sample_display_20261005`.
- Built `dist/rr_helper-0.2.54.zip` with the repository release tool. Both verified
  Blender 5.2 installation copies match all 26 published files. The Unity
  AssetPipeline's portable Blender add-on copy was also synced and checked;
  no Unity asset compilation or scene edit was required.
- Live Blender 5.2 (Builder6) was not remotely refreshed or saved. The current
  session picks up the installed version with **Refresh Add-on**.

This is a local source/deployment update. The user's live scenes are not opened,
saved or refreshed by the validation, and Git commit/push remain with the user.
