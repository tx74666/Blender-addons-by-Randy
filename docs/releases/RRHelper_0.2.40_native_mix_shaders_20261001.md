# RR Helper 0.2.40 — Native Mix Shaders asset

Mix Shaders is now a personal node-library asset beside Ring Mask in the existing
**Textures** catalog: **Shift+A > Textures > Mix Shaders**. The separate root Add
menu entry and standalone Rings sidebar are removed. Ring Mask and its catalog
UUID remain unchanged.

The native Shader Node Group exposes Base Shader, Mask / Shader pairs and one
Shader output, using Blender's Shader color tag. Select it and use **right-click
> Add Shader Slot** to expand. Earlier slots cover later slots; Base remains at
the bottom. The node is inserted without changing material output links.

Expansion stages a copy, preserves old interface identifiers, socket values and
external links, and clears asset marking and fake-user state on the new instance.
The library template and other instances stay unchanged. RR Helper maintains
hidden per-instance connection gates: empty Shader inputs pass through, while a
connected black shader still covers. Saved native graphs render without the
add-on; connection-state updates and slot expansion require RR Helper.

## Verified locally

- 21 canonical Mixer tests and 8 actual shader-render samples passed.
- 146 real addon_utils registration, refresh, rollback and disable checks passed.
- The saved asset passed 13 checks, including 9 three-Ring-Mask render samples.
- Three ordinary-Python deployment protection tests passed.
- Library verification passed: 4 assets / 3 bundles, with catalog, asset,
  generator/dependency source hashes and saved evidence agreeing.
- The asset was deployed and checked in
  `D:/Blender/Helper/Asset-Libraries/Costom/Nodes`. The catalog bytes and all
  pre-existing library asset hashes stayed unchanged.
- RR Helper's 18-file user, addons_core and Unity AssetPipeline copies were
  deployed and checked against canonical source with zero differing files.

Archive: `dist/rr_helper-0.2.40.zip`, 18 files. SHA256:
`A849975139B841D15B821BFAAEB3387C2DD5A70BDC7E52E4B66C28824A308305`.
Evidence and deployment backups are under
`D:/Blender/Projects/Build/Recovery/RingMixerLibrary_20261001/`.

## Verified in Builder6

The running Shader Editor's **Shift+A > Textures** menu now shows **Mix Shaders**
and **Ring Mask** together, with no standalone root Mix Shaders entry. This
existing session needed a visible All Libraries Asset Browser scan, another
All Libraries refresh, and a fresh Add menu opening.

The unused `Ring Stack Hub_Base` datablock was removed after a recovery backup.
The working `Builder6.blend` was saved successfully (`FINISHED`, `dirty=false`).
Existing material graphs, object coordinates, selection and active object stayed
unchanged; the legacy group remains absent.

The background asset report retains `ui_search_tested=false` because it ran in
an isolated process. Actual live menu, cleanup and save evidence is recorded
separately in [live_completion.json](D:/Blender/Projects/Build/Recovery/RingMixerLibrary_20261001/live_completion.json).
The add-on upgrade itself does not automatically delete legacy graphs or data.

Local source, package and installation changes only. No Git commit, push, PR or
published release.
