# RR Helper 0.2.52: Font-only search and Smooth by Angle rebind

**Bind Existing Surface Text** previously searched every object, including the
mesh being edited, and rejected Smooth by Angle because it is a Geometry Nodes
modifier. Existing Font now searches only editable `FONT` objects, with current
names and case-insensitive matching. Converted text meshes, curves, empties and
the edited mesh are excluded. The existing name-based operator API is retained.

Select the larger continuous face region, use Bind Existing Text to Selected
Faces, choose the intended Font, and confirm. The new sampling mesh represents
the expanded selection. Text content, font, pose, materials and authored
Shrinkwrap/Solidify settings are retained; expanding a region does not move the
lettering automatically. Original/Mirrored remains an explicit binding choice.

## Shading and geometry verification

The bundled Smooth by Angle asset is accepted without applying or removing it.
Its name is not used as proof: supported pass-through/shading geometry operations
are checked through the active group output and nested groups. Switch branches
are inspected, and generators or hidden instances are rejected. On a private
tagged clone, each active Nodes modifier is then evaluated separately with later
stages disabled. Exact vertex coordinates, edges, face/loop order and the selected
face tag must match with the stage on/off. Shading normals and sharp/smooth flags
may differ. Evaluated visibility is checked to detect driven switch overrides.

This retains selected-face correspondence and works with a trailing shading
modifier after Mirror. Viewport/render parity and the other existing supported
stack rules remain. Verification is repeated at rebind/export, so later graph
changes cannot silently invalidate a binding. The source mesh, node group,
modifier values and source visibility are not changed by the proof.

Failure recovery also fixes a pre-existing borrowed-IDProperty bug. Previous
arrays and nested groups are copied as owned rollback values before annotation;
native ID references are retained. Rejected binds restore the old metadata and
Shrinkwrap target, and remove the new sample even if restoration itself raises.
This prevents corrupting old array metadata or crashing on repeated failures.

## Validation and local deployment

All 63 checks pass in a disposable Blender 5.2.0 LTS factory process: 13 new
rebind/shading tests and 50 existing Mirror, editable text, UV/export and naming
checks. New cases cover the actual bundled Essentials Smooth by Angle, arbitrary
shading names, Font search, preserved author settings and nested metadata, fake
smooth modifiers, position writes through an otherwise allowed attribute node,
unrealized instances, cancelling geometry changes, graph changes, render parity,
full rollback and real expanded-region FBX export/import.

The first isolated run exposed the old rollback-array bug and crashed its own
test process. Its log/backtrace and the final successful result are retained in
`D:/Blender/Projects/Build/WIP/Validation/surface_text_rebind_shading_20261005/`.
No authored scene was opened or saved for either test run. The final process
exited normally and no task-owned Blender process remains.

The 25-file `rr_helper-0.2.52.zip` package was built with repository tooling and
deployed to Blender 5.2 user add-ons and the verified Builder6 installation at
`D:/Blender5.2/5.2/scripts/addons_core/`. Both final deployment checks report zero
differences; the previous installed files have deployment backups.

Use **Refresh Add-on** when convenient to load 0.2.52 in the current session.
The live Builder6 scene was not changed or refreshed, and the separate Blender
5.1 session was not updated. Live dialog appearance and the user's current
geometry remain unverified; no new Unity editor/runtime import is claimed.
Changes are local, with no commit, push, PR or remote release.
