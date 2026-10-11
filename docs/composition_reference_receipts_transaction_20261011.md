# Composition reference receipt transaction — 2026-10-11

The earlier source-only receipt-key repair used a lossless 59-byte key but
still had two transaction gaps: preparation could skip receipt validation
when its linked Action was missing, and failure after writing a receipt could
leave it pointing to a deleted new cache. The prior `a4b0de5e` candidate and
its immutable manifests are retained as historical source evidence, not as
an accepted native candidate.

Canonical `animation_composition.prepare_reference` now validates the full
packet SHA receipt before checking Actions or creating anything. A malformed,
wrong-SHA or orphaned existing receipt rejects preparation. A matching Action
without its receipt also rejects through the existing complete native ownership
checks. Stored `None` is malformed; only an absent encoded key means missing.
The lossless Base32 encoding and public DTO/ID values are unchanged.

Creation records whether the inventory property originally existed and takes
an independent plain copy of its complete contents. Successful preparation
restores the rig, context and data fake-user flag and removes its temporary
imported Action before publishing the new receipt. The removed Action variable
is cleared immediately, so a later publication failure cannot inspect its
invalid RNA. The write-attempt flag is set before assigning the IDProperty.

If that assignment partially writes and then fails, recovery restores the
exact prior inventory first, including the distinction between an absent
property and a present empty inventory. Remaining rig/context restoration,
owned Action removal, owned new cache deletion and fake-user restoration run
independently. Secondary recovery failures are attached to the primary
exception rather than replacing it. Existing reference caches, unrelated
receipts and Actions are retained.

`tests/test_character_composition_reference_receipts.py` has 15 passing focused
tests. They execute the actual canonical preparation/reuse paths with a
recursive 63-byte property-key limit, invalidated removed-Action seams and real
temporary cache files. Added cases cover no-Action malformed/wrong-SHA/valid
orphan rejection without mutation; matching Action without receipt; malformed
inventory; absent, empty and existing-other inventories; persistent context
restore failure; native remove failing before or after removal; partial
publication followed by persistent recovery failure; and final publication
order. Syntax compilation also passed. This correction does not claim a full
regression rerun, Blender RNA persistence, native save/reopen, FBX or screenshots.

The session helper API remains `reference_receipt(rig, full_packet_sha256)`;
its canonical `ebdf46d1` reader and the Raise runner/helper are unchanged. The
old `e0406325` and `bbcfcd63` manifests remain immutable. Root must freeze a new
complete runtime projection and matching configuration before another explicitly
authorized native window. No Blender/worker/build/deployment/artist operation
was performed for this source-only correction.
