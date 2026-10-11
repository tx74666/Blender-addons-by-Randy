# Composition reference receipt keys — 2026-10-11

The actual isolated n3 run reached official pre-copy FK Match, fresh adoption
and both prepared B imports, then stopped in `prepare_reference` before a worker
or Sync. Blender rejected a nested IDProperty key containing a 64-character
packet SHA-256: property names are limited to 63 bytes. Historical native state
was recovered by the isolated driver. This record does not claim successful
native evaluation of the subsequent candidate.

Canonical `animation_composition.py` now stores each reference receipt under
`sha256_` plus RFC 4648 Base32 encoding of all 32 digest bytes, without padding.
The result is 59 ASCII bytes. It is deterministic and reversible, retains all
256 bits, and does not use a truncated prefix or an Action name as identity.
The JSON receipt and linked Action owner proof retain their original full
hex SHA-256, packet path, native slot, complete motion hash and cache hash.
Packet DTOs, public IDs and source files are unchanged.

The canonical APIs are:

```python
key = animation_composition.reference_receipt_key(full_packet_sha256)
receipt = animation_composition.reference_receipt(rig, full_packet_sha256)
```

The accessor returns `None` only when that encoded key is absent. An invalid
digest, malformed receipt JSON, wrong schema or different full packet SHA
raises `ValueError`. `find_reference` still independently requires one exact
owned linked Action, matching packet path/proof, exact native slot/motion,
cache bytes SHA and native library path. Corrupt or ambiguous existing reference
ownership never triggers reimport through the normal matching-reference path.
There is no fallback to an unencoded or shortened property key.

The writer is `prepare_reference`; the runtime reader is `find_reference`.
Action ownership uses the fixed short `REFERENCE_OWNER` key with a JSON value
and therefore does not require digest-key encoding. The independent session
UI helper is another consumer: it must call the public accessor and retain its
cache/library/Action proof checks. Agent2 owns that canonical tool change.
Earlier immutable tool snapshots and runtime projections remain unchanged and
must not be reused with a mismatched reader/writer contract. Root will freeze
a new complete candidate and new tool/config pins before another explicit window.

`tests/test_character_composition_reference_receipts.py` executes actual
canonical `prepare_reference` and `find_reference` with a strict 63-byte nested
property-key seam, mocked native library boundaries and real owned temporary
cache bytes. Nine tests cover full-bit round trips, last-bit distinct digests,
malformed digests/receipts, exact reuse, cache/motion/slot/library/path tampering,
ambiguous native ownership and corruption without cache replacement. These
cheap checks do not claim Blender RNA persistence, actual FBX, playback,
screenshots or save/reopen acceptance. No Blender/worker/deployment/artist
operation is performed by the tests or this source-only repair.
