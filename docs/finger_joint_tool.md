# Finger tools (0.61.62)

The Joint Topology & Weights feature has been retired at the user's request.
Prepare Joints, joint sliders, Move Centers + Bones, Add Support Rings + Apply,
local weight generation, subdivision target planning, ring overlays and their
background monitor are removed. Legacy Prepare Rings/Create 3 Rings entry points
and their registered state are also removed, rather than merely hidden.

Existing mesh topology, Shape Keys, weights, normals and bone transforms are not
changed when updating or unloading the feature. Old saved custom properties or
unlinked source meshes are left inert; no restore/delete operation is invoked.
Historical implementation details remain in the versioned release notes.

The compact Fingers panel retains Basic Setup, Align Active Finger, Relax Bones,
Bone Roll calibration and Preview Bend. Align handles the current non-thumb finger.
The Bone Chain actions occupy two rows: Align Finger / Mark above Align Joints /
Relax Bones. The mark clear icon sits beside Mark.
Relax Bones uses the actual Edit Mode bone selection without requiring Basic
Setup, retaining each chain's endpoints and curve. With X Mirror enabled it
reflects one source result onto complete existing counterparts, deduplicating
both-side selections. The active selected bone chooses the source; otherwise L
is preferred. Missing counterparts are reported, never created. Bone actions
retain their validation and rollback.
Manual topology editing and Mirror Selected Region remain separate tools.

Basic Setup still detects finger surfaces and can adapt its saved reference to
local loop additions/removals. Detection uses read-only traversal helpers in
finger_range.py; it does not generate loops or prepare topology snapshots.

Reference and bend guides display one selected side. The small double arrow
switches L/R, with L as the default. The master eye hides both guide types and
retains cached lines. Bend previews are explicitly requested and cached; drawing
callbacks do no BMesh work, topology planning or full mesh fingerprinting.
Ordinary mesh edits leave these guides frozen at their saved locations. There
are no finger reference/bend dependency-graph listeners: sliding, adding or
deleting edges cannot trigger topology adaptation, mesh validation or reference
error writes. Even cold display after load/undo/redo decodes saved paths and
normals only. Mode and active Shape Key changes do not reinterpret the guides.
The displayed line is a saved reference, not a validation of current geometry.

Capture and explicit Recheck own reference changes. Actual bone and mesh actions
retain their direct validation and rollback; they cannot use a display frame as
a geometry proof. Preview Bend uses saved reference directions and builds bone
guides on request. Unload, undo, redo and file loading cancel pending previews.
Bone operations already in single-armature Edit Mode reuse the live edit data
without leaving/re-entering Edit Mode; selection and X Mirror are restored.

See [0.61.50 release notes](releases/CharacterDesigner_0.61.50_retire_joint_topology_20260921.md).

Capture, Start/End, Recheck, Clear, bend direction, Align and Roll calibration
operate on the current side only. Clearing removes that side's saved guides,
errors, bone binding and marks, preserving the opposite side. Capturing one side
does not create or replace an opposite reference. Calibrate All means all captured
fingers on the current side; Calibrate Selected filters bone selection to that
side. Armature X Mirror does not expand these actions. Relax Bones retains its
separate selected-chain/X Mirror behavior described above.

Left/right differences do not block work on either valid side. The header can
show a small non-red `L/R differ` badge from the last explicit detection, without
popups, background polling or fresh geometry checks while editing. The badge is
a saved observation, not a live symmetry test. Opposite-side errors cannot color
the current finger tab red. Mirror Selected Region remains a separate manual
step when the artist decides to synchronize the mesh.

## Explicit loop marks

Bone Chain includes one `Mark` button, `Align Joints to Marks`, and a small clear
button. On the captured active finger's Basis mesh, select one or both complete
internal joint loops and click Mark. Both guides are bright yellow. Their root-to-tip
order is automatic; there are no numbered or color-specific controls. A new
single loop replaces the nearest existing mark when both are already set;
select both intended loops to replace the pair explicitly. Equal-distance
replacement asks for both loops instead of guessing. Mark shows a check when
both loops are stored.
Clear removes only this finger's current-side marks, including with X Mirror on.
No edges, faces, Shape Keys or weights are created or changed. Mark validates
the current selected cycles using saved direction/range only as spatial hints.
It does not compare current geometry to old Capture topology, coordinates, ring
IDs or errors. Closed, nondegenerate transverse loops can be marked after
sliding, reshaping, inserting or dissolving rings without recapturing. Partial,
crossing or clearly wrong-finger selections are refused before any mark changes.

The existing three-bone chain keeps its root, tip and Roll. For Index, Middle,
Ring and Pinky, the loop section planes intersect the root-to-tip line, keeping
the chain straight even if the loop centroid is offset from its internal axis.
Thumb intersects its existing curved chain instead, retaining bend direction.
Align Joints rebuilds its local proof from the current marked loops and adjacent
finger surface, never historical face/ring indices or old surface equivalence.
New joint positions must be inside the current finger. Redistributing joints
along the actual existing head-to-tail bone spans retains those paths, even
when the fixed root precedes the sleeve's artificial root cap. A gap or bent
corner is not treated as existing straight coverage. Changed paths still require
current interior proof; a changed proximal segment may include up to three
complete adjacent quad bands, without traversing the palm or whole character.
Warnings name the affected bone and distinguish an unverifiable local root
from a verified surface exit. Writes and binding updates
roll back together if any validation or commit fails. This edits rest joints;
Roll calibration remains a separate explicit action.

Align Joints changes the current chain only, regardless of Armature X Mirror.
Marks belong to their captured side: no reflected guides are synthesized and an
unmarked side never borrows marks from its opposite. Missing or incompatible
opposite surfaces are irrelevant to the current side's alignment.

All bright-yellow guide coordinates are saved explicitly on Mark. Display and master
eye changes use cached coordinates and retained GPU buffers; they do not read
geometry, resolve bones, create timers or attach a dependency-graph monitor.
Each finger's two loops share one GPU batch. Panel status reuses a bounded cache
of immutable marker flags, and rebuilding display records parses metadata once.
Selection, load and undo rebuild only the tiny saved display records. Unrelated
vertex reindexing is recovered on Align. If a marked loop itself is moved or
deleted, re-mark it; saved guide locations are not continuously tracked. This
feature uses existing Basis loop sections, not subdivision-target generation.

## Explicit mirroring after editing

The small mirror icon in the Basic Setup header calls the existing Mirror
Selected Region with finger-reference synchronization enabled. Select the mesh
region that should be copied first. This mirrors geometry and existing weights,
then updates proven opposite guides and yellow marks for fully covered fingers.
Partial regions still mirror normally; unavailable or partially covered saved
references stay unchanged. The result reports how many references were synced
and left unchanged. One explicit census refreshes the saved L/R difference badge;
there is no live comparison or background listener. Existing mirror boundary,
attribute, Shape Key and custom-normal preservation checks still apply.

The separate mirror icon in the Bone Chain header copies the current finger's
rest bone positions and bend axes/Roll to its existing opposite named chain.
It requires no mesh symmetry or old Capture proof and supports the actual chain
length. Source/unrelated bones, parenting, connections and selection stay intact;
missing or ambiguous counterparts are not generated. Transformed mirror planes
reflect bone axes rather than simply negating a numeric Roll value.

Both actions are explicit Undo operations. Mesh-sync metadata failure rolls
back mesh and metadata together; bone-write failure rolls back all rest bones
and the target binding. Preview refresh errors after a successful commit do not
claim that committed changes were reverted. No new persistent visual overlay is
introduced by either action.
