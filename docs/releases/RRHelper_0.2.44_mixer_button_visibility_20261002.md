# RR Helper 0.2.44: Mix Shaders button visibility

With Show Options disabled, the Mix Shaders header and sockets remained
visible but its + / - controls disappeared. The overlay incorrectly treated
that presentation setting as a reason to disable slot editing. The controls
now remain available on the active selected, expanded, editable mixer.
No shader calculation, socket-expansion algorithm or saved asset changed.

Select the mixer and look at the right side of its green title bar. Plus adds
one Mask / Shader pair; minus removes the final pair. Existing Undo behavior
and retained-link/parameter safeguards are unchanged. Collapsed, unselected
and read-only nodes still suppress these optional editing controls.

## Validation

- All 12 UI regression tests passed, including a real selected group with
  Show Options false, overlay drawing and add/remove dispatch. Source state
  and the hidden-options setting remain unchanged.
- All 276 restricted-registration and refresh lifecycle checks passed.
- In a separate unsaved GUI fixture, controls were visually present inside a
  Frame with Show Options false. An actual plus click changed two pairs to
  three, preserved all six original cables and upstream/default values, and
  kept Show Options false. The fixture exited normally without saving.
- Minus and Undo were not repeated as GUI inputs in this follow-up; their
  implementation is unchanged from the earlier accepted GUI sequence. The
  new UI regression verifies both dispatch paths with hidden options.
- In the user-owned Builder6 window, selecting the actual Mix Shaders node
  displayed + / - at the right of its header. This was a visual check, not a
  live module-version or bytecode audit. Production sockets/links were not
  edited, and no file save or export was performed.

The 20-file `dist/rr_helper-0.2.44.zip` matches canonical runtime source.
SHA256: `d0bd4bbc4e50ad132b173a8c8903854d03166fa2d86b3d7d79b10147aa89a380`.
All three local add-on destinations pass the deployment read-only check;
Sync-RRHelperAddon -Check also passes.

The native node library stays at 0.2.0. The complete mixer core source is
LF-normalized byte-equivalent to the accepted 0.2.43 package. Asset binaries,
interfaces, catalogs and existing render evidence remain unchanged. Helper
version and UI-only provenance were recorded separately, and the lightweight
library verification passes for all six assets and five bundles.

Evidence directory:
`D:\Blender\Projects\Build\Recovery\ShaderMixerVisibility_20261002`.
This includes UI/lifecycle logs, the disposable GUI status, helper-equivalence
metadata and the reproduction fixture. Heavy tests were coordinated with Pool;
the heavy and desktop windows were returned after completion. User-owned
Builder6 and X processes remain open. Changes are local; no commit or push.
