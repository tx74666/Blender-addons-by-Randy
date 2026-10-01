# RR Helper 0.2.39 — shader mixer and Empty child coordinates

Local release prepared on 2026-10-01 for Blender 5.2. The owner handles GitHub
Desktop Commit and Push; no commit, push, PR or published release was created.

## Empty Origin

After moving an assembly Empty without moving its parts, old Parent Inverse
translation could remain in the child transforms. This kept the world geometry
correct but left misleading Location values such as -8.1 on first-floor parts.
The earlier conservative normalization skipped any constrained child, including
the elevator doors' world-space rotation constraints.

Normalization now handles verified world-space constraints and world-space
constraint readers. It retains the Parent Inverse 3x3 matrix, including shear,
and rebases only the translation into the child Location. World transforms and
evaluated geometry are checked before accepting the change. A mismatch restores
the original values. Local/custom-space dependencies, unknown constraints,
animations, drivers and modifier references remain guarded. This is not a
promise that every animated or constrained rig can be rebased safely.

The same correction runs in the Empty Origin workflow, including repeated moves
to the cursor or active-object origin. Ordinary geometry origin rules remain
unchanged. Blender's Location channels are still relative to their parent.

The live Builder6 elevator now has these local Z values:

| Part | Z |
| --- | --- |
| Hub_Elevator_Bottom | 0.001 |
| Hub_Elevator_Casing | 0 |
| Hub_Elevator_Door1.L / Door1.R | 0 |
| Hub_Elevator_Door2.L / Door2.R | 8.1 |

The upper-floor 8.1 is real height. All six parts' evaluated world vertices,
all scene object world transforms, and all four door constraints were preserved.
Builder6 was saved to `D:\Blender\Projects\Build\WIP\Builder6.blend` after
the installed add-on was refreshed to 0.2.39. No Unity scene or prefab was edited.

Recovery and live evidence:
`D:\Blender\Projects\Build\Recovery\EmptyAllParts_20261001`.
The pre-change copy is `Builder6_before_remaining_channels.blend`; final
coordinates, saved state and loaded add-on path are recorded in `live_result.json`.

## Mix Shaders

The standalone Rings toggle and Ring Stack panel were removed from Textures.
Legacy graphs, settings and layer data are retained for saved-file compatibility.

Use Shader Editor **Shift+A > Mix Shaders**, or search for **Mix Shaders**.
Select the node and use **right-click > Add Shader Slot** or **F3 > Add Shader
Slot** to expand it. Each slot has a Mask and a Shader. Shader 1 covers later
slots; Base Shader remains below them. Existing Ring Mask and material shader
nodes stay outside the group. Surrounding links are not changed automatically.
See [shader_mixer.md](../shader_mixer.md) for the complete workflow and limits.

This is an add-on runtime graph helper, not a new personal library asset.
The existing Ring Mask asset and its catalog are unchanged. The native Add menu
and search result were verified in the running Builder6 window without inserting
a test node into the user's material.

## Validation and deployment

- Empty Origin: 67 Blender tests passed.
- Mix Shaders: 20 Blender tests and 8 actual shader render samples passed.
- Registration lifecycle: 146 checks passed, including restricted registration,
  refresh, menus, handlers, failure rollback and legacy Ring Stack retention.
- Unity's Blender exporter runner: all 8 suites passed. Its deployed lifecycle
  test was updated from the canonical test to recognize the new mixer handlers;
  the previous test was backed up in the recovery directory.
- Personal node library: all 3 assets and 2 bundles passed the lightweight
  consistency check. No asset computation or library files were changed.
- Release ZIP verified: 18 files. All three deployed RR Helper copies matched
  the canonical source with `tools/deploy_local.py --check`.

Installed copies: Blender 5.2 user add-ons, Blender 5.2 `addons_core`, and Unity
RandomRealm2's `Tools\AssetPipeline\Blender\addons` copy. Live Blender loaded
the `addons_core` copy. Tests used isolated background factory-startup processes.

Package: `dist/rr_helper-0.2.39.zip`.
SHA-256: `af8502f3d020d930e3c4f249aac2832370432f223a8ded0e7dd2e8630e03a5bd`.
