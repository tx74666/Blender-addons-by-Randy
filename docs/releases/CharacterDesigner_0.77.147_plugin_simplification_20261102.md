# Character Designer 0.77.147 — plugin simplification

Fresh and repeated registration now use one ordered runtime-hook list. Repeated
registration retains the existing Finger properties and their native descriptors.
An enable failure can finish its owned cleanup without trying to discover scene
objects through Blender's restricted registration context. Normal playback and
disable behavior retain their existing recovery policy.

Worklist Scan processes one row between timer returns and checks for intervening
row, scene and input changes before publishing a completed scan. Cancellation
releases its temporary guard. A dense individual row and final fresh checks can
still occupy a tick. Attached-route admission reuses its one fresh workspace
read, and Rest drawing uses one rig lookup per draw.

Groups and Parts name sorting use a shared minimum-swap permutation plan while
retaining their existing native data-swap operation. A reversed list of 100 names
requires 50 swaps instead of 4,950. This is an operation count, not an artist
wall-time benchmark or a new partial-write/Undo guarantee.

The complete 145 wrist reset and 146 FK releases are inherited. Wrist-reset
latency measurements belong to the separate 145 acceptance; this release does
not reopen the closed playback-FPS investigation. The source delta is nine
changed files and one new pure helper, with 244 inherited files unchanged.

Evidence lives in X `validation/plugin_simplify_20261102`: the component source
acceptance, final projection, registration checks and scoped-update receipts.
Original failures and fixture limitations remain recorded. Source/package
approval, disk deployment and live adoption are separate facts. The narrow live
update retains the two existing operator tooltips and all registered classes,
RNA, keymaps, handlers and timers; fresh enable uses the complete new source.
