# Character Designer 0.61.53 — explain partial Capture results

Capture Detection could capture the selected finger successfully and still show
“rebind this finger” in red. That message originated in a shared row-comparison
helper: it described both a stale saved reference and a mismatch between the
current left/right mesh surfaces. Repeating Capture could not fix the latter.

The current-surface comparison now reports a left/right shape mismatch. The
panel distinguishes the captured side, pair warnings and per-side reference
errors, without duplicating the same warning. A partial Capture reports that
the source was captured and opposite pairing needs review. Cached surveys from
older versions refresh their diagnostic wording without repeating detection.

Programmatic reference writes suppress interactive Reverse Bend callbacks.
Previously, resetting a source's flip during recapture could alter the retained
opposite reference's flip/confirmation before pairing failed. Failed pairing now
preserves that metadata as well as the previous record and bone binding. Normal
user Reverse Bend behavior remains paired.

The geometry checks have not been relaxed. Different ring counts are accepted
when they describe the same surface; actual asymmetric surfaces still block
paired bone writes. No geometry is automatically mirrored, smoothed or deleted.

## Verification

Serial Blender 5.2 factory-startup tests: recapture 7, bank 7, clear 5, topology
adaptation 6, internal axis 10, reference cache 5, batch previews 6, Roll targets
3: **49 passed**. This includes stale-to-valid recapture, asymmetric recovery,
opposite metadata retention, normal Reverse Bend, cache reuse and error labels.

A read-only live inspection of Cosha confirmed version .52, valid MIDDLE.L and
unconfigured MIDDLE.R. The pair contained eight left rings and seven right rings.
The added ring was a valid midpoint, but two existing rows had also slid,
changing the actual piecewise surface. An isolated replay of the exported
3436-vertex mesh and 11-face selection reproduced the partial Capture, verified
the new diagnostic, and left geometry and selection unchanged. No character
file was saved. No Git commit or push was made.

Built and deployed .53 to the Blender user add-on and X validation copy: 99
files, zero differences. Previous files were backed up under
%LOCALAPPDATA%/CodexBackups/addon-deploy/20260921-202300-0efd3dc1.
Live .52 → .53 reload and the requested Capture were verified with the unchanged
11-face selection. Capture returned FINISHED, L remained confirmed/valid, and
the R pairing warning now describes the actual shape mismatch. Before/after
artist data, live Shape Key coordinates, selection, rig and other finger records
matched. The original 3D view was restored; no .blend save was performed.
