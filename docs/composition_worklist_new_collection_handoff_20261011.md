# Composition Collection and new Action handoff — 2026-10-11

This is canonical source work for the 0.77.25 draft. No deployment, Blender
worker launch, artist mutation, native validation or Unity mutation was
performed by this reviewer. The approved 0.77.24 worker/base files remain the
validation baseline. The user owns Commit and Push.

Collection compares three independently authenticated states: authored B
curves, canonical B export inputs, and the full composition plus protected A
metadata. Seeking, soloing and selecting A do not edit B. An A/configuration
change can require a new manifest while `b_edited` stays false. Unknown proof
stays Unknown; it is never seeded during Scan or converted into a fabricated
unchanged receipt. Mandatory measured composition/export metadata and exact
physical output hashes remain publication requirements.

Ordinary `All`, `FingersLeft`, `FingersRight` and `FingersBoth` rows retain their
native fingerprint route. An explicit composition DTO/role or
`UpperBodyOverride` uses the separate B route; unsupported scope rejects.
Ordinary A work copies resolve their own current per-row workspace.

## Prepared Add entry

Generic Add and serial Add Prepared dispatch an explicit B to
`animation_composition.add_work_item` before the ordinary importer can change
the rig. The current catalog/Link route and full composition identity must
agree. The same explicit Composition Library must already be connected;
otherwise Add gives `Connect Composition Library to this workspace, then Add
Prepared` and retains the existing library and artist Actions. This entry does
not automatically replace or connect a library.

The composition importer preserves `_collection` locking and the caller's
`activate_new` choice. Serial Add leaves the artist's selected Action, row and
Scene intact. Manual Add activates Custom only after exact packet, local DTO,
current Link/model and native scope admission. An existing incomplete B row
is rejected and retained; Add never repairs its provenance or rebinds it as an
ordinary row. Readonly A companions (`compositionReferenceSource` or
`sourceOnly`) are excluded from the ordinary catalog and Add Prepared queue,
and a direct Add rejects them before import. Create A Work Copy remains their
explicit editable route. Full prepared B Work Item IDs are retained.

## Actual A work-copy origin

`animation_worklist_new.register(context, origin_template=...)` accepts exactly
`workspace_manifest`, `workspace_identity`, `clip_key`, `link_manifest`,
`source_packet` and `source_packet_sha256`. It re-reads the real ordinary A
catalog/Link, current model and exact full `All` packet. The companion Entry
and Link must carry `compositionReferenceSource=true` and the same actual B
owner asset GUID. That unique current B owner's sealed DTO/packet must refer
to these exact A GUID/local-ID and packet bytes. Entry `linkId` must equal the
actual JSON Link UUID, while `linkGuid` identifies its actual Unity asset.

Registration creates fresh full `newActionId` and `workItemId` values. The
new manifest is ordinary `All`, with no claimed Source baseline or inherited
companion readonly flags. `workspaceTemplateLinkGuid` stores the actual A JSON
`Link.linkId` UUID hex; the C# resolver authenticates it through the current
catalog's Link asset rather than substituting the global B template.

The local Action's `character_designer_animation_new_origin_template_v1`
freezes the origin template and exact Link identity. Native package refs pin
the original A packet, and the authenticated 0.77.24 motion-data proof keeps
translation recovery. Failure restores prior keys/fake-user and removes only
the owned newly created row/manifest. Sync remains explicit; the original
readonly A Source and Link are retained.

## Sealed new B template

A local new B may differ from its sealed prepared packet only in
`compositionId`, `editWorkItemId` and `editNewActionId`. Initial registration
uses a private typed candidate Action/rig/slot proof plus one real prepared
template row. Its readonly native Source, saved slot/cache SHA, current Link,
workspace/model and exact packet/DTO are authenticated before scope admission.
Passing only text IDs or a dictionary creation proof is insufficient.

`authenticate_composition_bind_template(context, rig, spec, packet,
creation=None)` revalidates a persisted new row, manifest, native local Action
and slot, plus its typed readonly Source/rig receipt. The Action key
`character_designer_animation_new_b_template_v1` stores this exact template
evidence. The public template field is the actual source B JSON Link UUID,
so a B using A2 never falls back to a global A1 template. The proof does not
add A identity, new IDs or view state to the structural B content fingerprint.

## Pure evidence

- Collection/New regression: 92 PASS (51 legacy Collection, 17 composition
  Collection, 8 legacy New, 16 composition New).
- Canonical `tests/test_character_composition_new_templates.py`: 13 PASS,
  including actual A origin, strict full packet/owner checks, per-action B
  template, native source/slot/rig tampering and registration rollback.
  The actual serialized New B manifest also preserves `newActionId` and
  `workItemId` exactly as its normalized hyphenated composition DTO strings;
  native row/folder IDs remain compact UUID hex.
- Canonical `tests/test_character_composition_prepared_add.py`: 19 PASS,
  exercising actual Worklist Add, the actual composition importer and the
  serial Collection queue with native boundaries mocked. A valid full sealed
  B commits its local DTO and paired A reference before returning with the
  artist binding retained. Exact prepared IDs, owned locking, activation flags,
  readonly A rejection and missing/swapped B routes are checked.
- Canonical Collection regressions: 48 PASS after updating only the native
  Action `.get` and per-row workspace mock interfaces. Canonical Worklist state
  regressions: 15 PASS. Composition session tests
  separately cover strict existing-row native admission and Add's lock/activation
  flags; their count belongs to the session owner.
- Canonical `tests/test_character_composition_weapon_ownership.py`: 13 PASS,
  including explicit UUID ownership, external links/references, artist assets,
  exact units and importer mutation rejection before geometry.
- Geometry: 9 PASS. Physical dependency guards: 8 PASS. These are separate
  components; avoid counting them again in an aggregate already including them.

Native ID boundaries, importer behavior and scope evaluation are mocked in
the pure suites. The native two-A measured isolation, Full Stack/Tweak,
save/reopen/recovery and actual Cosha/Blaster acceptance remain pending the
owner-controlled fresh idle window and current memory read. No FPS or paired
performance claim is made.

Detailed source hashes and commands are retained in X staging's
`prepared_add_route_report.json`, `collection_new_action_pure_report.json`,
`weapon_source_review.json` and this
handoff's sibling `composition_worklist_new_collection_manifest_20261011.json`.
