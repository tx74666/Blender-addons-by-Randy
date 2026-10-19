# Character Designer 0.77.76 — Weight Cleanup

The Weight panel now repairs the selected bound Mesh through two actions.
**Fill Unweighted** immediately fills vertices with exactly zero effective
Deform weight. **Clear Bone Influences** opens a native table of the Mesh's
actual influencing bones. Every **Keep** checkbox starts checked; the artist
unchecks unwanted bones and confirms **Clear**. An explicit **Selected Vertices
Only** option limits the write scope.

Clearing removes only excluded Deform memberships, retaining Vertex Group
definitions and surviving raw paint. Only vertices made truly empty by that
clear are filled, from original allowed weights along the same Mesh's edge
graph. Existing empty vertices remain for the separate Fill action. Helpers,
locks, Shape Keys, modifiers, Rest and other Meshes are preserved. The tools
never infer anatomical correctness or select bones for deletion from distance.
A missing connected donor, required locked change or stale dialog refuses the
complete operation. A completed operation is one native Undo/Redo step.

The full release retains all 212 files from accepted .75, changing only
`__init__.py` (version), `README.md`, `weight_cleanup.py` and
`weight_cleanup_core.py`. Existing advanced Find/Inspect/selected-bone island
operators remain in F3 search. Apply repairs in Object, Pose or Weight Paint
Mode; Mirror-generated vertices are not source indices.

## Validation and deployment

- Frozen pure tests: **44 passed**; native Blender tests: **25 passed**, none skipped.
- Full add-on registration/idempotent registration/unregistration twice and
  trusted private save/reopen weight readback passed.
- Isolated native GUI verified actual Keep checkbox edits, Escape cancellation,
  native Return confirmation, only explicit bone removal, local new-gap fill,
  and one-step Undo/Redo. Initial mouse-confirmation attempt was not accepted;
  the traced Return-confirmation derivative passed on identical product code.
- Initial lifecycle harness compared Python wrapper identity with `is`;
  the accepted derivative compares every native group definition. The runtime
  was unchanged. Both initial reports are retained.
- Blender **5.2.0 LTS**, build `fbe6228777e7`, trusted auto-execution enabled.
  Each owned process had a fresh CONFIG and TEMP, saved no preferences, used
  no desktop input, and exited. No current artist file was opened, repaired,
  saved or refreshed. Unity and formal exports were untouched.
- Official release build and deployment completed. Both Blender 5.2 user and
  X project copies contain **212 matching files, zero differences**. The
  current artist needs an add-on refresh to use the new panel. Changes are
  local; no commit or push was performed. Model/reasoning effort: unrecorded.

Acceptance:
`D:\Blender\Projects\Character\X\validation\weight_clear_20261019\acceptance_summary.json`
SHA256 `d8f17add814430c23fa647616566a006bcec2cee5aba0b7bb2c7095be8a51186`.

Complete projection:
`D:\Blender\Projects\Character\X\validation\weight_clear_20261019\candidate01\projection.json`
SHA256 `d996509a477333a2e9d3cffa7e2ee53994e8bc7c43e0acc5b9657a18f35903ca`.

Archive: `dist/character_designer-0.77.76.zip`
SHA256 `bf074bc2237e5ce9b0d7fa032dbd20362e903a6b94fba707af09782e74ce6aef`.

The next check is an explicit artist choice of unwanted bones after refresh.
Investigate a new reproducible donor failure or changed source; this release
does not claim a performance improvement or automatic repair of the live Shoes.
