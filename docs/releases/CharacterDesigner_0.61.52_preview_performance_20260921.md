# Character Designer 0.61.52 — reduce repeated preview work

Showing five captured finger references previously copied the same body mesh six
times and hashed its topology eleven times. Opening Preview Bend then repeated
five copies and ten hashes even when the reference frames were already valid.

A synchronous FrameReader now shares each object/key/override snapshot and its
topology hash within one refresh. Buffers are freed on success or failure. Bend
uses the validated view frames and still resolves the current bone chain and
requires valid bend evidence. Nothing new runs during viewport drawing.

Topology adaptation stabilizes the cache domain before publishing its first
frame. An unchanged empty warning is not repeatedly written to RNA. Each
finger's coordinates, internal containment, active Shape Key and independent
bend-source validation remain enforced; one invalid finger does not reject the
other four. Persistent viewing caches do not bypass direct validation for edits.

Bone actions on the sole active Edit Armature now reuse its live Edit Bones,
avoiding mode changes and their dependency-graph updates. Multi-object editing
and callers entering from Object Mode retain isolation/context restoration.
Active bone, selection flags and X Mirror survive success or exception.

## Measured CPU work

Blender 5.2, factory startup, one thread, synthetic 640-vertex fixture. These are
individual CPU timings, not real-model viewport FPS or a universal speedup.

| Operation | Before (.51) | After | Mesh copies / topology hashes |
| --- | ---: | ---: | --- |
| Cold five-finger reference refresh | 40.4 ms | 9.2 ms | 6 / 11 → 1 / 1 |
| First Bend build after references are warm | 41.7 ms | 4.1 ms | 5 / 10 → 0 / 0 |
| Warm reference selection, median of 100 | 0.60 ms | 0.50 ms | No revalidation |

The new batch regression tests enforce read counts and verify freed buffers,
cache reuse, topology adaptation, error isolation, separate bend evidence and
non-Basis containment. The Edit Rig tests verify zero mode switches for an
already editing single rig, real Relax operations, exception restoration and
the preserved multi-object path.

Validation: **70 tests passed**, serial Blender 5.2 factory-startup runs:
Edit Rig 6, Relax Bones 13, Align 2, Roll targets 3, reference cache 5,
Definition 10, internal axis 10, topology adaptation 6, lifecycle/retirement 4,
Clear 5, batch previews 6. Warm and cold preview benchmarks also passed.
No character file was saved, and no Git commit or push was made.

The .52 archive was built and both the Blender user add-on and X validation
copy were deployed from canonical sources: 99 files, zero differences.
Previous files: %LOCALAPPDATA%/CodexBackups/addon-deploy/20260921-192709-0b4613b8.
The current Blender window was minimized; live reload/real-model timing has not
been verified for this release. Reload the add-on to use the deployed changes.
