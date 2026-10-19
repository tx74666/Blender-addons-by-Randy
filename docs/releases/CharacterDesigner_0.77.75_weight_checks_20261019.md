# Character Designer 0.77.75: checks for unweighted and distant bone influences

Artists can select a problem mesh without knowing which bone caused it. The
Weight page's **Weight Check** panel has two buttons:

- **Find Unweighted** locates source vertices whose weights sum to exactly zero
  over this rig's Deform bones. Utility groups do not count. Any positive Deform
  weight, including very small values, remains existing paint. Object/Pose
  results enter visible vertex Edit selection; Weight Paint uses its vertex
  mask. A mesh with no gaps keeps its original mode, masks and selection.
- **Inspect Bone Influences** shows used bones and their distant or disconnected
  influence vertices in a native table. A row can locate the suspect source
  vertices and activate the relevant bone/group. Inspection needs no selected
  bone and does not modify weights.

Distance uses each vertex's world position against the Rest head/tail segment,
with a warning threshold of twice the bone length. It checks vertices inside
continuous main regions too; a tiny positive-weight bridge does not conceal a
far endpoint. The table separates distant vertices from stray islands, reports
Blender-unit distance and bone-length ratio, and identifies the reference main
mesh. Distance is a diagnostic hint, not proof of an unwanted influence or
permission to erase it. Same-rig character references help distinguish a hand's
main pinky region from pinky weights on shoes.

Advanced **Clean Weight Islands** and **Fill Unweighted** operators remain
separate in F3 search. Repairs preview counts, revalidate the exact native
identity and data, and write only planned target memberships. Existing valid
paint and helper/locked weights remain exact. Fill follows world-length mesh
edges from original valid seeds; it does not jump across air gaps or disconnected
parts. Cleanup cannot fill a gap by reintroducing the removed bone. Ambiguous
main islands, unavailable donors, required locked changes or stale state refuse
the transaction, and injected write failures restore exact memberships.

This release is based on the complete accepted 0.77.74 projection. Only the main
version/import/class list, add-on README and two new weight-check modules are
included. Existing compact Rig/Mesh export UI, range selection, Body-based export
naming, update authority and full/partial export guards remain inherited.

Validation evidence is kept in
`D:/Blender/Projects/Character/X/validation/weight_cleanup_20261019`. The raw
initial failed native and GUI fixtures remain separate from corrected runs.
The accepted candidate passes 34 pure tests, 15 native test groups with no
skips, two complete register/idempotent-register/unregister cycles, and private
save/open weight readback. An isolated hidden GUI shows the actual native
inspection table, cancels it through its own window's Escape event and confirms
visible Edit selection of the two true gap vertices. This uses no desktop input.
Native Edit round-trips may reorder attribute storage; validation compares
every named attribute's type, domain and cell values and records storage order
separately. The artist scene, its preferences and formal Unity assets are not
opened or modified by these isolated fixtures. No character is re-exported.

Limits: editable source topology is inspected, rather than generated Mirror
vertices. Inspect from Object, Pose or Weight Paint mode; Find also supports
existing Mesh Edit mode. A spatial warning cannot by itself establish intended
anatomical binding. Whole components without a valid local donor require an
explicit binding/painting decision. This release does not repair the current
Cosha scene or prove an artist-session Unity export end to end.
