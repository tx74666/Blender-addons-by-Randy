# RR Helper 0.2.53 — Surface Text backlight

Select a bound editable Font in Object Mode and open **Surface Text → Configure
Backlight**. Enable the effect and set the rear wall gap, text thickness, color
and strength. The two distances use metres and are independent. **Unity Halo
Width** controls the imported Unity wall glow.

The Font stays editable. The helper adds a private rear emission material and
keeps the original front and rim material slots, including per-character styles.
Cycles can render the rear emission lighting the wall. Disabling restores the
previous Shrinkwrap/Solidify placement. Shared Font data and materials are not
edited. Configuration failures restore the previous geometry, materials,
per-character assignments and metadata.

Backlight is opt-in. The v1 export descriptor carries enabled state, world rear
gap, RGB color, strength, halo width and the rear material slot. Text configured
with backlight also exports its actual world offset after Font depth scaling,
including after disabling. Older unconfigured descriptors keep their previous
contract. Reconfigure after changing the Font depth scale or placement modifiers.

The Unity integration maintains a bounded per-object glyph cache and a separate
rear emission material. Its projected wall halo follows the selected curved
surface and preserves glyph counters. This halo is a visual layer; it does not
provide dynamic illumination to nearby objects. Use the same font file to create
a TMP Font Asset for matching edited text. Glyphs are cached on demand, including
digits and punctuation; there is no fixed A–Z catalogue.

## Validation

- Blender 5.2.0 LTS, factory-only: 73 affected Surface Text checks passed before
  the final rollback and scale fixes. All 12 final backlight checks then passed,
  including multi-material character restoration and Font Z scale 2 round-trip.
- Real FBX bridge exports with backlight enabled and disabled confirmed the
  requested body, 0.16 m thickness and settings. Source `.rrblend` hash unchanged.
- An isolated curved-wall example exported successfully and was rendered in
  Cycles; no user scene was opened, saved or refreshed.
- Raw reports and the example live in
  `D:\Blender\Projects\Build\WIP\Validation\surface_text_backlight_20261005`.
- Unity compilation, shader/visual checks and deployment status are recorded in
  the accompanying project's `Docs/SurfaceTextBacklight.md` and task evidence.
- Unity 6000.5.9f1: all 12 backlight/cache/visual checks and all 83 existing
  workflow checks passed in isolated PreviewScenes. The dirty Adventure scene,
  its file hash and selection were preserved. Both Blender 5.2 deployment copies
  match all 26 published files; the live Blender scene was not refreshed.

This release is a local source/deployment update. Git commit and push remain
with the user.
