# Character Designer 0.77.88 — animation latency

Retire and Sync now paints processing feedback before its synchronous validation
starts. A stale Back button cannot replace a started, completed or failed sync
result with a cancellation message. The existing confirmation token is still
revalidated against current data; animation retirement and creation use the
same backend. No persistent RNA fields were added.

Control baking prepares invariant channel destinations once per bake and
coalesces only redundant post-redirect solver updates. Required parent reads,
exact matrix/shear checks, errors and cleanup remain in place. A private paired
test of the actual 297-sample Idle produced identical float64 bits for all
1,896 channels / 563,112 points, result metadata and protected author data.
Measured control-bake wall time was 52.7175 → 30.8321 seconds, with native
updates reduced from 3,565 to 2,080. This combines metadata preparation and
update coalescing; it does not isolate their individual benefit.

Worklist reference decoding also avoids redundant recursive traversal. Bone
display separately throttles an unchanged ownership refusal for at most one
second, without publishing ownership success or changing display. Display,
data or user-count changes trigger immediate reconsideration. This module's
own cleanup paths clear refusals; some external restore paths clear only the
existing display cache, so an unchanged request may still wait up to one second.
Current-reference and Rest validation remain required.
The X2 saved-baseline ABBA observation improved background frame-set mean from
332.14 to 127.28 ms, with all pose, evaluated mesh and Shape Key witness hashes
unchanged. Slow frames remain. This is not a GUI FPS measurement.

Both measurements are ordered observations on a shared computer. Profiling
adds overhead. The import comparison measures the control-bake subphase,
not the complete Sync operation, cache publication or displayed GUI response.
No stable frame-rate target or universal latency improvement is claimed.

The complete 215-file release input extends approved 0.77.87 by exactly six
files; its other 209 files, including the approved body-region implementation,
are unchanged. Build, deploy and check must use:

`D:/Blender/Projects/Character/X/validation/sync_latency_20261022/projection088/projection.json`

Projection SHA256: `d021a2b10add011d6a7d83638eb97552e6a362696f56fdecace8e75b568914f7`.
The separately paused canonical body-region candidate remains excluded.

Evidence: X `validation/sync_latency_20261022/` and
`validation/idle_playback_20261022/`. Installation, runtime update and cleanup
receipts in those directories determine the delivered state; this source note
alone is not an installation receipt. Local release work does not commit or push.
