# Character Designer Unity companion

Version 0.59.0 adds **Tools > Character Designer > Animation**: choose an existing
character prefab and clip, preview/play/pause/scrub, then send evaluated motion to
Blender's Character Designer Animation page. The original scene, Avatar and
Controller are preserved. See [animation workflow](ANIMATION.md).

The forearm correction component below is independent of this animation entry.

Character Designer 0.58.0 includes this companion. From the add-on repository,
stop Unity Play Mode and install it once with:

```powershell
python tools/deploy_unity_runtime.py --project 'D:\Unity Projects\RandomRealm2'
python tools/deploy_unity_runtime.py --project 'D:\Unity Projects\RandomRealm2' --check
```

Use the path of your own project. The installer writes `Assets/CharacterDesigner`,
preserves Unity `.meta` GUIDs, and refuses to overwrite companion files edited
outside the installer. `--check` reports differences without changing files.
The bundled folder can also be copied to `Assets/CharacterDesigner` when installing
from the Blender release archive without the repository's deployment tool.
Unity 6 and the Collections package are required. Runtime code and Editor code
use separate assemblies. The optional PlayMode test assembly is excluded from
normal player builds.

In Blender, confirm forearm calibration and use **Miscellaneous → Unity Export**.
The exporter writes the rest FBX, artist BlendShapes and a `.forearm.json` beside
it. In Unity the companion verifies the FBX hash, imports the saved calibration,
and builds **`<Character>.Runtime.prefab`** next to the FBX. Use that prefab as
the character root for the project's Animator/controller integration. Re-exporting
updates its generated data. Customize a prefab variant rather than the generated
runtime prefab. No scene is opened, saved or changed by the importer.

If the FBX arrived before this companion, reimport the FBX, then select it and use
**Assets → Character Designer → Rebuild Runtime Prefab**.

The component runs in LateUpdate after Animator. A custom IK script which runs
later must call `ApplyNow` after its final bone writes or use an earlier execution
order. Disabling/removing the component restores its original shared mesh. Each
instance owns a private clone; imported mesh assets, weights, animation and artist
BlendShapes are retained. Exported source and destination fingerprints prevent
stale calibration from silently attaching to a changed model.

To remove the exported calibration, remove it in Blender and export again. The
exporter writes an empty calibration sidecar as a removal marker; the importer
rebuilds its owned runtime prefab with ordinary skinning. Keep using an artist
prefab variant so a generated-prefab refresh does not overwrite your gameplay edits.

This transfers the *additional correction*, not Blender's Python handler. Saved
loop ratios, bounds and transitions are evaluated once at export; Unity evaluates
the actual signed wrist-relative twist on every update. Source-cage deltas are
propagated through measured Subdivision stencils, then converted through the
actual Unity skin blend. A reserved additional UV channel keeps correspondence
through FBX vertex reordering and splits at UV/normal seams. Existing UV channels
are preserved. Do not compress or regenerate that ID channel.

Current scope: ordinary Armature-first linear skinning, fixed Subdivision after
it, relative artist Shape Keys and positive uniform bone scale. The inherited
calibration range is ±120°. Unsupported/singular poses retain ordinary skinning
and report a diagnostic; they do not accumulate offsets or leave one side partly
updated. Local geometric normal/tangent adjustments retain the artist's original
surface directions as their baseline; this is not exact Blender custom-normal
reproduction.

Blender normally skins its cage before Subdivision, while an exported Unity mesh
is subdivided before skinning. Their ordinary skinning can therefore differ. The
verification compares the actual **correction-on minus correction-off** mesh
delta independently from that existing baseline difference.

The importer owns the generated Runtime prefab and Forearm data assets. Artist
calibration remains authoritative in Blender; adjust it there and re-export.
Shader conversion, missing skin weights, Humanoid retargeting, animation export,
cloth and an entire character controller are separate parts of the project.

Validation distinguishes actual mesh deformation from the control display. The
release was checked with numerical math cases, synthetic skinned meshes, real
Cosha FBX/sidecar cases, and an Animator PlayMode test. Real-model comparisons
measure correction-on minus correction-off on the evaluated skinned mesh across
left/right twists and changed arm/root poses. These checks do not establish exact
Blender rendering, custom shader equivalence, or compatibility with every project's
later-running IK and physics scripts; validate those after integrating the prefab.
