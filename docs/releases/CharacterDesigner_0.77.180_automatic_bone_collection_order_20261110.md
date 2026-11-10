# Character Designer 0.77.180 — automatic bone collection order

Status: completed local180 delivery. The final projection is complete180
stage180_02. Official ZIP, dual deployment/check and actual artist narrow
adoption passed. Hair2 subsequently deployed complete181 on top of180; this
record retains the180 evidence and does not downgrade the newer disk version.
This task performed no Scene or preferences save.

## Behavior

The daily **Organize Bone Collections** button is removed from Bone Display
and the detailed Body setup panel. Body setup, Hair binding and shared Dress
creation place managed root collections in daily order as part of their existing
operations. Both newly bound Hair and the existing-binding reuse path run the
ordering step. The guarded internal organizer and native/recovery operations
remain available; the existing rig lifecycle is retained.

The new `sort_managed_collections` helper recognizes stored group ownership,
not collection names alone. Managed Body, Hair and Dress collections are sorted
within their existing managed root slots, with the owned Original root last.
Artist roots keep their relative order; nested collections keep their parents.
The ordering step does not create, remove or rename groups, reassign bones, edit
visibility/solo/expansion flags, or change the active collection. It is idempotent
and restores its original root order and active reference if a move fails.

Standalone Hair ownership works without a Body profile. Shared Dress ownership
requires the registry/source/rig references to agree, and ordering runs after
that registration has been committed inside the existing transaction. Multiple
Dress groups retain their relative order. Ambiguous singleton roles or conflicting
ownership refuse before ordering writes.

The helper reads collection and source/registry headers. It does not scan bones,
vertices, animation curves or evaluated poses, and adds no handler, timer or
per-frame invocation. This is an operation-time design property, not a measured
playback-performance gain or a reopened FPS investigation.

## Transaction protection and inherited limits

Hair preflight refuses multiple owned Hair groups before modifying them. Both
binding paths retain the existing collection references, names, visibility and
active collection, including `None`, for failure recovery. This covers an artist
alias on the owned Hair collection and nested groups revealed while selecting
the chains. The existing geometry/weights/modifier/context/motion-identity
rollback remains in place for new bindings; ordering remains the last fallible
operation in each successful binding branch.

Shared Dress retains the old root order before copying its subset. If the later
palette-reference migration fails, rollback removes the new group, restores the
original root order and restores the active collection, including `None`, along
with the existing body-channel/display/context recovery.

Body replaces only its old explicit sorting block. Its original grouping,
inventory checks, transaction, first-layout backup and native ID-reference
encoding remain. Existing initial/explicit `compact=True` organization still
folds old subdivisions and sets managed display flags; this release does not
certify preservation of arbitrary artist groups through that inherited compact
operation. The lightweight sorting step itself preserves their hierarchy,
membership and display. Existing Original/session and ownership guards remain;
no managed switching, rehost or Restore Main Rig proof is added.

## Candidate and verification

Approved baseline: complete179 projection
`validation/action_browser_20261110/stage179_01/projection.json`, SHA256
`0e5e2cf1c856595d4e6838fbaa967c20ce1609c395db231e2e3c7b703846dacb`.

Final candidate: [stage180_02 projection](D:/Blender/Projects/Character/X/validation/bone_collection_order_20261110/stage180_02/projection.json),
SHA256 `d3690505c834cbe1c1241e8e3c87b9918d7b34a85e016fd04b18aeb84b64ae7b`.
It contains 268 files/236 Python: seven changed and 261 byte-exact to179.
The selected delta is `__init__.py`, `README.md`, `bone_collections.py`,
`bone_display.py`, `hair_bones_rig.py`, `limb_ik.py` and `skirt_shared_rig.py`.
Unselected canonical changes are excluded.

- Fourteen focused pure tests passed. Their mocked root collection API exercises
  owned classification, artist/root/nested/active preservation, aliases,
  idempotence, refusal and injected before/after-mutation rollback. This is not
  native Blender proof.
- [Native move semantics02](D:/Blender/Projects/Character/X/validation/bone_collection_order_20261110/native_move_semantics02_result.json)
  passed seven checks in Blender5.2: `collections.move` uses root indices;
  parent links, nested active collection and child storage order survive root
  moves. This is a tiny native API fixture, not Hair/Dress transaction or artist
  acceptance.
- The first native move probe failed an incorrect depth-first storage-order
  expectation. Its original script, result and logs remain retained as
  [native_move_semantics01](D:/Blender/Projects/Character/X/validation/bone_collection_order_20261110/native_move_semantics01_result.json).
  The corrected02 expectation follows observed roots-first native storage and
  does not relabel the first result.
- Stage180_01 is retained with its original projection/delta. Read-only peer
  review identified the Hair reuse/failure-recovery gaps and the active-`None`
  recovery edge; stage180_02 adds the focused fixes. Stage180_01 is not the
  delivery candidate.

Nine focused native groups passed: actual helper ownership and nested/active
preservation, real Hair create/reuse and both failure branches, real shared
Dress post-sort palette failure/rollback then success, Body compact=False and
native build/remove integration, and two real draw methods with LayoutSpy.
The original native_finish01 Hair-new harness missed the existing
HairBonesRigError wrapper; its failure is retained. Derivative02 reran only that
case with the proper exception capture and all original recovery assertions.

Final narrow updater02 passed actual full179 registration warm08 (30 checks)
and actual lazy warm09 (32 checks): active-frame refusal, three fault/rollback
points, adoption and ALREADY zero writes. Full mode authenticated363 functions;
lazy mode337. The plain skirt_shared_rig service remained absent without an
import or write. Earlier source-reader prewrite refusals and their exact helper
snapshots remain in warm_finish180_history01.json; immutable readers were not
changed. Existing migration callback aliases, source annotations/dataclasses and
empty native bl_options are admitted only by their reviewed exact shapes.

Official180 ZIP SHA256:
`5e83b60177eeebf15b2077322f6d39e275ae0a0288aa61bd04622a38723df527`.
CRC, unique268 entries and complete payload hashes passed. The official deploy
and matching check reported268/0 differences at Roaming5.2 and X. Deployment
backup `20261110-214103-a4d5f9ed` retains14 exact179 prior files. Independent
Hair2 handoff also recorded the180 disk state. Later readback correctly found
Hair2's successor181, so no180 redeployment was performed.

Artist51688 actually adopted180: four existing code/doc pairs, one new plain
helper binding and version, eight writes. skirt_shared_rig remained absent.
No RNA lifecycle, header reattachment, Scene writes, sort/generation calls,
full reload or source-signature rewrite occurred. Seven postchecks passed:
bounded raw/cached pose, Action/slot/NLA headers, context, handlers and dirty
state remained exact at frame27/Pose. Physical Bone Display showed the
Organize button absent. The detailed Body panel has LayoutSpy proof only.
Hair2's prior142 missing Hair colors and32 retained Side colors were outside
this task's writes. Root released input to Hair2 for the observed pending
print/Graph-restoration command and its separately owned181 work.

Acceptance: [acceptance180_01.json](D:/Blender/Projects/Character/X/validation/bone_collection_order_20261110/acceptance180_01.json),
SHA256 `902dac1220c98c099287def48adb044054eca9438ed42f858f46d2cf68cfb983`.
It pins source, native, warm, disk, artist and independent handoff receipts.
Owned native/warm workers quit normally and were absent by owner checks.

Inherited179 Action-list/display classification,176 Colors, Body/Hair/Paint
and earlier failure/recovery limits remain in force. No full geometry/weight/
all-key-value scan, solver, FPS or full lifecycle certification is implied.
No bake, export, Unity changes, Git commit/push or publication was performed.
