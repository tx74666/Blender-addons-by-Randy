# Character Designer 0.77.173 — Unified colors

Bone Display now has a compact Colors entry. It opens the shared Body, Hair and
Skirt editor in Properties > Armature Data; the window button opens the same
editor separately and pins it to the character. Changing the active object does
not retarget a pinned editor.

Normal colors use direct native RGB swatches. Advanced reveals Selected and
Active colors. Hair has Front, Side, Back and Other groups, using existing saved
strand categories and exact source/bone ownership. This display feature neither
reconstructs removed Hair history nor changes topology, bone counts or physics.
Presets apply global defaults or the built-in soft scheme; Restore returns the
colors captured before the palette's first application.

The existing character Object JSON remains the only saved color scheme. Native
computed swatches use the same guarded transaction and rollback as the earlier
palette. Old five-group schemes are read into a detached compatible view; their
UUID, colors and first recovery records remain intact until an explicit edit.
Add-on Preferences stores default Normal colors for new character schemes and
derives highlighted variants. Changing these defaults does not recolor existing
characters or initialize an unconfigured character.

## Delivery record

Extends the frozen complete 172 Rig Setup projection with four reviewed existing
files and one new module; all other 263 package files remain exact. Final pins,
focused native checks, official package/deployment, live adoption and UI outcome
are recorded in `validation/color_center_20261110/acceptance173.json` when complete.
No Git upload is part of this delivery.
