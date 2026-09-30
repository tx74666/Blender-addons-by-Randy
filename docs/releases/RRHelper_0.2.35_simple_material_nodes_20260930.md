# RR Helper 0.2.35 — Native nodes for simple material imports

Material → Shader now imports a simple Surface implementation as ordinary nodes
in the current Shader Editor material. Import Shader still takes one click after
choosing a material, without a confirmation dialog or a new mode switch.

Direct imports cover a single native shader or texture node, and one texture
feeding one shader (for example Image Texture → Principled BSDF or Wave Texture
→ Principled BSDF). Frames and reroutes do not make the network complex and are
copied with their relative layout. More complex networks and nested node groups
keep the existing reusable Shader Group workflow and native Shader color tag.

The direct copy includes node settings, socket values and internal links, shares
the same image datablocks, and leaves the imported output unconnected. Existing
target nodes, links, selection and active node remain unchanged. Partial failures
remove only the newly copied nodes. Importing into the source material itself is
blocked for direct copies so its graph stays unchanged.

A current simple graph takes precedence over a group made by an earlier import.
That old group and its users remain untouched; Refresh can still update it in
place when explicitly requested. Repeated simple imports use the current source
values without making new group datablocks. Standalone group generation remains
available through the internal API.

Validation on Blender 5.2.0 LTS: all 45 material/shader tests and 63 registration
lifecycle checks passed in isolated factory scenes. The native-copy tests cover
BSDF values, shared images, Wave settings, sole texture outputs, framed/rerouted
graphs, repeated copies, unchanged old groups and four partial-failure cases.
Existing group creation, reuse, refresh and reference-safety tests remain covered.

Package: `dist/rr_helper-0.2.35.zip` (15 files). All three deployment checks passed
with zero differing files: Blender addons_core, the user add-ons directory, and
RandomRealm2's AssetPipeline copy. Test logs are saved locally under
`D:/Blender/Projects/Build/Recovery/MaterialShaderSimple_20260930/`.

The running Builder6 session was refreshed with Refresh Add-on and returned to
the Texture page. No material nodes were inserted into the user's working scene
as part of testing, and the working blend was not saved by this update.

Local source and deployment only; no Git commit, push or release publication.
