# RR Helper 0.2.45: expandable Ring Group masks

Ring Group no longer needs manual internal interface editing to combine five
or more masks. Select an expanded, editable Ring Group in the Shader Editor:
its header **+** adds one scalar Mask input, and **−** removes the last input.
The group keeps one shared Shader. A fresh group starts with two masks, so
three plus clicks provide five. Mix Shaders keeps its separate Mask / Shader
pair controls.

Existing input identifiers, values and retained cables survive changes. The
helper stages an independent native group before swapping only the selected
instance. Removing an input removes its cable, preserves upstream nodes, and
supports Undo/Redo. At least one mask remains. Customized graphs, animation,
read-only owners and changed interfaces receive validation rather than an
unsafe rebuild. Header controls also work with Show Options disabled.

## Arc Mask replaces standalone Ring Mask

Node Library 0.2.1 exposes five assets in four bundles. Arc Mask 0.1.1 is the
single circular-mask asset: Sweep Angle 360 makes a full ring; other sweep
angles make arcs. Its Texture color tag remains brown/orange. Add Ring uses
Arc Mask at Start 0 / Sweep 360. The original radial binary remains unchanged
under `node_library/dependencies/`; the embedded `.Arc Mask Radial` group is
hidden from the normal Group submenu.

Explicit legacy-node migration is available in the runtime helper and was
tested independently. It preserves instance controls, presentation and exact
external socket connections, including repeated socket names. It rejects
modified or animated radial implementations and rolls back on failure. It
does not run automatically on load, update, save or render.

## Validation

- 13 mask-slot regressions passed, including five-input expansion, independent
  instances, retained links/defaults, removal and native Undo/Redo.
- 16 header-overlay regressions passed for Ring Group and Mix Shaders,
  including Show Options false, drawing and add/remove dispatch.
- 34 Ring/Arc workflow checks passed, including seven actual Cycles samples.
- Nine legacy migration regressions passed, including Math clamp and instancer
  UV customization, duplicate socket names and injected-failure rollback.
- All 276 registration and refresh lifecycle checks passed.
- Arc Mask 0.1.1 passed four metadata and recursive graph-equivalence checks,
  including native save/reopen. Its calculations match the preserved 0.1.0
  baseline with 38 shader samples; those samples were not rerendered.
- Ring Group's complete graph factory functions and contract constants remain
  AST-equivalent to the accepted 0.2.44 ZIP. Its native binary is unchanged.
  The mixer calculation is LF-normalized byte-equivalent. Editing provenance
  is recorded in `node_library/validation/ring_group_editing_20261002.json`.
- The lightweight library verifier passes for all five assets/four bundles.

The 20-file `dist/rr_helper-0.2.45.zip` matches canonical source. SHA256:
`a6d98bff773360cf18adf88826c91cee20645d6150df978e42cd28e5880ea69f`.
All three local add-on copies pass read-only deployment checks. Arc Mask's
installed native asset also passes its check; the catalog and unrelated
assets were preserved. The known standalone Ring Mask asset was hash-checked,
backed up outside the library, and removed from the installed library.

## Live verification limit

The user stopped Computer Use with Escape before this release's live refresh,
migration or header clicks. No production Builder6 node was replaced, no
production input was expanded, and Builder6 was not saved by this work.
The running session may therefore still show its cached old nodes/add-on.
Use **F3 > Refresh Add-on**, refresh the Nodes asset library, then select
Ring Group to display its header controls. Current-file Ring Mask migration
remains pending a later authorized desktop session.

Background Blender processes exited normally. The heavy and desktop windows
were explicitly returned to Pool. Evidence and installation backups are under
`D:\Blender\Projects\Build\Recovery\ArcOnly_20261002`; add-on deploy backups
are under `%LOCALAPPDATA%\CodexBackups\addon-deploy`. Changes are local; no
commit or push was performed.
