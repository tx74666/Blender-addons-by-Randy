# Character Designer 0.77.93 — reuse source-Rest None within one validation scope

**Local delivery completed, 2026-10-23.** Complete native transactions, the SaveCopy supplement, both official deployment checks and the protected artist .92→.93 update passed. Final receipts and exact scope are recorded below. No GUI speedup has been established.

`limb_ik._validation_source_rest` previously resolved the same absent source-Rest
view repeatedly during one synchronous Forearm validation. It now reuses a
successfully resolved `None` for the same armature inside the existing
`validation_native.read_scope`. Two plain helpers use the original module
globals and `_ACTIVE` ContextVar; scope exit releases the entries. Explicit
non-None inputs still win. Non-None views and exceptions are not memoized, and
no positive Rest or ownership proof survives across callbacks. Existing
structural, Rest, ownership and reference checks remain. Animation, deformation,
material and display quality are not reduced. The [candidate pins](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/candidate_pin.json)
identify the exact two product sources.

The background ABBA used the fresh pre-.92-update SaveCopy, SHA256
`9aa537a48ae7fcbb3e8bb236f2dbe93d9281a08e87ead5d042648881eabe4b4a`,
with complete approved .92 runtime sources. Its serialized metadata is .91;
that does not describe the benchmark runtime. Across 128 samples, combined
baseline/candidate complete `frame_set` means were 52.9227→49.5961 ms, 6.29%
lower in that shared-host ordered observation. Forearm preparation was
16.0463→12.8274 ms. Every sample separately confirmed six resolver calls and
actual `control_rest_view` calls falling from six to one, all returning None
inside the active scope, with no exceptions. Limited pose, evaluated mesh
positions and Shape Key witnesses matched, as did the experiment's start/end
authored-input hashes. Each phase still performed a strict owner proof on
stop, outside sample timing, with the same four refusals.

These nested inclusive spans must not be added. Explicit background playback
component state plus `frame_set` is not native Play, a GUI event loop or an FPS
measurement. See the raw [BG result](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/native01/result.json),
SHA256 `199230a05beffd25248f5255387cc0e80f59a150c5f73c65db43017ed48a1c3a`.

The separate private native-Play ABBA kept MATERIAL shading, layout and quality.
It excluded six initial warmup seconds and frames 1–10 in each phase, then
measured the same frames 11–30, 80 fixed-frame samples total. Frame-pre→post
means in A0/B0/B1/A1 were 190.4334/167.6900/144.1044/138.9252 ms; corresponding
POST_PIXEL changed-draw observations were 2.2730/2.6097/2.9147/3.0621 Hz.
The six-to-one per-scope mechanism and the experiment's correctness, draw
completion and cleanup checks passed. After each phase stopped and settled at
frame 30, the limited witnesses for 346 pose bones, all 69 scene meshes and all
Keys matched. This does not prove complete model equality on every playing frame.

A1's complete interval and draw observation were faster than B, showing clear
order drift. **No whole-frame or GUI speedup is attributable to this candidate
from that run.** Draw callbacks do not measure physical presentation or artist
FPS. Actual visibility and occlusion were not independently observed; incomplete
resource readings do not exclude memory confounding. See the raw [GUI result](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/gui01/result.json),
SHA256 `17343c55ca5d679128cec4f97efbc40cc331280389d6eb707a1c05c9b3370f79`,
and the [product acceptance](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/acceptance_summary.json).
That product-stage receipt retains its historical unreleased status; it is not
the final .93 integration receipt.

Product pure tests passed 43 cases each on CPython 3.12.8 and 3.13.13. A retained
frozen-.92 failure isolates an `ast.dump` representation difference between
interpreters; only the exact test assertion accepts the two independently
derived known hashes. Runtime bytes and other assertions are unchanged. See
the [3.12 receipt](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/pure_compatibility_py312.json),
[3.13 receipt](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/pure_compatibility_py313.json)
and [AST compatibility evidence](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/ast_hash_compatibility.json).

The frozen [complete projection093](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/projection093/projection.json)
has 215 files, including 183 Python files compiled successfully, SHA256
`4b7310c1b9cc6a107591dacce993a542882e8cf1feef5be910931814faa18de4`.
It extends approved .92 by exactly `limb_ik.py`, `validation_native.py`, the
literal initializer version and package README; the other 211 files are
byte-identical. Approved `body_region.py` remains
`09543e30a254824919c14e0ddab96ed0c7ab5ef79329651dfe24668b0b8ab101`.
The separately paused canonical Body-region candidate is excluded. Use this
complete projection for build/deploy/check, as verified by the
[independent projection review](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/runtime093/projection_review.json).

The [local ZIP](D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.93.zip)
is 1,656,417 bytes, SHA256
`c531f14a6c5931a77bda2229f72a06d990b15d81326169c3a93e0f6bd6872d12`,
with 215 payload files matching the projection. The [build receipt](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/build093_receipt.json)
retains the pre-deployment state at build time. Subsequent official
[deployment](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/deploy093.log)
and [check logs](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/deploy093_check.log)
confirm 215 files and zero differences at both Blender 5.2 user add-ons and X
add-ons. Previous deployed files are retained in
[20261023-053023-62463227](C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261023-053023-62463227).
No Git commit or push is included.

The definitions-only installer is pinned to
`580c3e83738d0e609c39370a5c53219bab3ff355012fd362956c6482eaa960ca`.
Its 11 pure transaction tests, six failure boundaries and bundled 3.13 static
source/stdlib audit pass; [pure receipt](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/runtime093/pure_runtime93_final01_result.json),
[test output](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/runtime093/pure_runtime93_final01_stdout.log)
and [3.13 static receipt](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/runtime093/static_acceptance313_580c3e.json)
remain distinct from native acceptance. It changes the original resolver's
`__code__`, adds two authenticated helpers to the original native globals, and
updates literal version plus exactly three source-signature rows. It preserves
the runtime module docstring, ContextVar, read_scope wrappers, original
function/default/closure objects, classes, handlers, observable timers and cache
identities. Native builtins, relative-import aliases and contextlib templates
are authenticated; foreign/mixed or active/busy states refuse. Rollback removes
only owned helpers, retains unknown bindings and independently restores known
metadata. Trusted capture cannot reconstruct same-shaped substitutions made
before capture. See the [independent installer review](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/runtime093/installer_review.json).

Final local delivery is accepted: 50 complete-X native checks, six rollback boundaries, 11 installer pure tests, eight boundary pure tests and the native SaveCopy boundary supplement passed. The artist update passed six outer and six installer checks. Three unchanged full author guards preserve the scene apart from literal version. The first two attempts refused before code execution. The first original identity leaf was not recorded; the second recorded only the display `_serial` path, without its original before-value. Neither missing fact is reconstructed by later success. The native supplement and successful third attempt prove their own two source-authenticated transient state categories: an empty `_SAVE_RESUME` list rebind and an unchanged original exact-int display generation or a strict increase. The successful artist display generation was 13→14 (delta 1). All other raw runtime fields and module bindings remain exact; there is no blanket exclusion of globals. The proved postcopy state establishes a fresh authenticated capability for the unchanged strict installer. These lifecycle allowances do not apply to the code transaction.

Fresh dirty SaveCopy: [backup](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/artist93_03/artist_latest_before93.blend), 40,573,066 bytes, SHA256 `6fec6250817a630951a3fa48e7a465e805232d96a74dea86023bd500be870839`. No independent serialized reopen is claimed. The formal artist file was not written by this code update: 40,573,690 bytes, SHA256 `46d2a21786f0cde99f28b28249a907767047b90bd74919c499035a0cda2e2d08`. Preferences and full reload were not used.

Event-loop context is recorded exactly as `{"active": "Dress", "dirty": true, "file": "D:\\Blender\\Projects\\Character\\X\\X.blend", "frame": 49, "mode": "OBJECT", "pose_positions": {"CoshaRig": "POSE", "Strap2.001": "POSE"}, "scene": "Scene", "selected": [], "subframe": 0.0}`. The task SDK stopped normally, its module was removed, port 55617 had zero listeners at the OS checkpoint, owned validation processes exited and artist PID40512 was preserved. These are checkpoint facts, not continuing monitoring. Background 6.29% is the bounded ordered result above; no GUI or current artist FPS benefit is claimed.

[final acceptance](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/acceptance_summary093.json) / [native50](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/runtime093/native01/result.json) / [SaveCopy boundary supplement](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/runtime093/boundary_native01/result.json) / [v3 review](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/runtime093/outer_v3_review.json) / [artist runtime](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/artist93_03/runtime.json) / [SDK cleanup](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/artist93_03/sdk_cleanup.json) / [OS checkpoint](D:/Blender/Projects/Character/X/validation/forearm_rest_scope_20261023/final_os_cleanup093.json). Final summary SHA256 `cc1416338b77557c3c7ce2a8d4515a4bc402ba3799ac31f1e8702b568135ee8a`. Actual model and reasoning effort are unrecorded. No Git commit/push or Unity operation is included.

<!-- cd093-final-summary-sha256: cc1416338b77557c3c7ce2a8d4515a4bc402ba3799ac31f1e8702b568135ee8a -->
