# Refine Symmetry / Repair Mirror Symmetry

Character Designer 0.70.2 provides a coordinate repair tool in
**Modeling → Refine Symmetry** and **Weight → Refine Symmetry**.
Use it when topological mirror correspondence is still valid but Basis
coordinates have drifted away from mesh-local symmetry about zero.

## Quick use

Select the destination vertices in Edit Mode, choose **X** and **Left → Right**
or **Right → Left**, then click **Refine Symmetry**. Left means local +X and
Right means local -X. Selecting both vertices of a pair also works. The source
side remains fixed; only selected destination coordinates move. For Y/Z the
same positive-to-negative direction convention applies.

**Selected Region Only** starts enabled. **Average** repairs both sides and
requires both vertices selected. **Preview** shows the planned movement without
modifying the model. **Advanced** contains Analyze and optional tolerance
controls. Refine does not require running Analyze first.

## Analyze and preview

1. Make the mesh active, preferably in Edit Mode, and select a local region.
   **Selected Region Only** starts enabled.
2. Open **Advanced** and click **Analyze**. It reports matched vertex pairs, misaligned pairs or
   center vertices, unmatched vertices, centerline candidates and maximum
   mirrored Basis error. This does not modify coordinates or flush an Edit
   Mode buffer into the stored mesh.
3. **Select Misaligned** changes selection only. Paired problem vertices on
   both sides are selected so Average can repair them together.
4. Choose a mode and click **Preview**. Orange points show the current Basis;
   green points and displacement lines show the proposed result. Click
   **Clear Preview** to remove the overlay. A changed mesh, Shape Key,
   selection or setting invalidates the pending preview.
5. Click **Refine Symmetry**, then inspect the validation report.
   **Ctrl+Z** restores the previous coordinates and Shape Keys.

Turning off Selected Region Only explicitly includes the whole mesh. With
that option enabled, no unselected or hidden vertex is moved. Average
requires both members of every affected pair selected; otherwise it stops
before writing. A directional repair can read an unselected source vertex
but writes only selected destination vertices. An empty selected region
does not trigger a whole-mesh repair.

Analyze also requires a nonempty local selection when Selected Region Only
is on. Disable that option explicitly to inspect the entire mesh; an empty
scope is never reported as a clean model.

## Repair modes

Left means the positive local axis side and Right means the negative side;
object transforms do not change this definition. With X selected,
`M(x,y,z)=(-x,y,z)`; Y/Z negate only the selected component.

| Mode | Coordinate result |
| --- | --- |
| Left to Right | Keep Left and set Right to `M(Left)` |
| Right to Left | Keep Right and set Left to `M(Right)` |
| Average | Set Left to `(OldLeft + M(OldRight))/2`, and Right to its mirror |

Average minimizes the combined squared movement of the two vertices.
Only topology-qualified centerline vertices already within Centerline
Tolerance are snapped to the chosen local axis plane at zero; other near-center or off-plane vertices are
not guessed into the centerline. The configured tolerances stay below
Blender's ordinary spatial mirror lookup threshold.

The ordinary lookup threshold is 0.00002 mesh-local units in
[Blender's mirror implementation](https://raw.githubusercontent.com/blender/blender/main/source/blender/editors/mesh/mesh_mirror.cc).
Correspondence comes directly from Blender's native Topology Mirror through
Select Mirror on a disposable mesh containing the same ordered topology.
Binary-coded selections read the native table in `vertex_count.bit_length()`
calls. Only immutable Python indices are cached, keyed by full connectivity;
the disposable scene, mesh and object are removed after the lookup. The
artist's mode, selection, settings and Edit buffer are unchanged by Analyze.
There are no generated persistent helper assets.

Native unmatched vertices, nonreciprocal matches, pairs on the same side of
the plane, and native self-pairs outside the tiny center tolerance are left
unchanged. No custom topology algorithm or nearest-vertex fallback substitutes
for Blender's result. Native topology matching can be ambiguous on repeated
structures even when geometric matching works; it is not a guarantee that
every vertex has a counterpart. Remaining unmatched vertices are reported
visibly even when Advanced is collapsed.

## Shape Keys and protected data

For each affected vertex in every key, the transaction records
`OldDelta = OldKeyPosition - OldBasisPosition`, then writes
`NewKeyPosition = NewBasisPosition + OldDelta`. Independent left/right
expressions keep their original asymmetric deltas. Key names, relative-key
relationships, values, animation, masks and the active key remain intact.
The Edit Mode shape layers and active edit coordinates are updated together
so leaving and reentering Edit Mode cannot replay old coordinates.

Mesh, object and Shape Key datablock identities are retained. No vertex,
edge or face is added, removed, merged or reordered; indices, face loops,
UV coordinates, vertex groups, weights, existing rig relationships and
modifier targets are preserved. The tool does not rebuild through a Mirror
modifier, delete a half or replace topology. Linked/read-only data, shared
meshes and locked Shape Keys must be made editable before repair; Analyze
can still inspect data that is readable.

Unmatched and ambiguous vertices remain unchanged. There is no nearest
vertex fallback for correspondence. Post-write spatial lookup is only a
validation step: every targeted pair must have the correct unique ordinary
mirror counterpart in the selected axis with Topology Mirror disabled. A missing or coincident
ambiguous counterpart causes a complete coordinate rollback. Validation
applies to the proven target pairs, not to unmatched parts of the mesh.

Analyze always uses Basis, even when another Shape Key is active. Ordinary
mirror is geometric: an independently asymmetric non-Basis key can still
have different live coordinates by design. Preserving those expressions
does not imply that every expression will itself become bilaterally
symmetric; use Basis when inspecting the repaired base mesh.

## Validation

`tests/test_refine_symmetry_blender.py` covers read-only Object/Edit Analyze,
Preview, selection scope, all repair modes, centerline tolerance, asymmetric
and relative Shape Keys, protected attributes, stale plans, Object/Edit
rollback, native ordinary mirror lookup, UI registration and native Undo.
`tests/test_refine_native_pairs_blender.py` compares the native table with an
independent vertex-by-vertex oracle for X/Y/Z. It also checks immediate native
transforms in the same Edit buffer with Topology Mirror disabled, connectivity
cache invalidation, native failure cleanup, directional selected-only repair,
asymmetric Shape Key deltas and full rollback.
`tests/verify_real_refine_symmetry_blender.py` appends only a saved character
into an isolated factory scene, verifies protected data and all key deltas,
and confirms that the source blend file remains unchanged.
