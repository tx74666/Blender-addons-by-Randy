# Character Designer 0.77.8 — native Pending/new-Action proof

Local, uncommitted and unpushed release on 2026-10-09. Runtime fixes were made
in the canonical `addons/character_designer` sources first. This supersedes the
local 0.77.6/0.77.7 rollout; their frozen packages and failure evidence remain.

Native Blender 5.2 exposed two new-Action issues: repeated registration compared
fresh RNA wrappers by Python identity, and the complete temporary output pair
exceeded Windows MAX_PATH. Registration now compares the stable work-item ID.
Only immutable file labels use a short random nonce, with eight collision retries
and exclusive publication; the full work/new-Action UUIDs are unchanged. The real
fixture paths measure 215/226 characters for final FBX/metadata and 242/253 for
temporary publication. Longer Windows workspaces fail explicitly before export.

The authorized current Unity `CharacterAnimationTransfer.cs` provider and its
new `CharacterAnimationReadableMesh.cs` helper are included, preserving the
existing formal Unity APIs and complete owned mesh copying. Their Unity runtime
verification belongs to the root owner; this Blender subtask does not claim it.

Projection SHA-256:
`74f1645ef67514c9a900f952cc2eb8451a94b0ab45a8bdfe0badfe6e86f76628`.
The approved 152-file projection changes four entries over 0.77.7: version,
new-Action module and those two provider files. All other files inherit the
approved base. Held Dress exporter differences remain excluded.

Build and local deploy/check tools passed for Blender 5.2 user installation and
`D:/Blender/Projects/Character/X/addons`, each reporting zero differing files.
Recoverable backup:
`C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261009-033445-7e88e068`.
Exact archive/canonical/installed hashes:
`D:/Unity Projects/RandomRealm2/.codex/task-staging/character-pending-crosspreview-20261009/blender-candidate/evidence/deployment-receipt-0778.json`.

Real factory-startup Blender checks proved untouched Walk_N A stayed Unchanged,
only edited Run_N B exported a complete Action, and a newly created local C
returned a complete 39-sample skeletal FBX/metadata pair with no Source/Original.
The successful B publication was retained through C's repair; it was not
re-exported during continuation. Repeated scan/export was a no-op. A/B manifests,
source packets, model, previous immutable outputs and artist X.blend hashes stayed
exact. Source/property IDs and all Action pointers/slots/receipts survived actual
idle add-on unregister/re-register with an unsaved local draft Action.

Combined successful evidence (including original A/B native checks and preserved
resolved failures):
`D:/Unity Projects/RandomRealm2/.codex/task-staging/character-pending-crosspreview-20261009/blender-candidate/evidence/native-abc-complete-20261009-0335/pending_abc_native_qa.json`.
The C continuation completed in 31.1 seconds, with worker-launch memory 1.25 GiB
available / 92% usage. Eight focused new-Action tests pass after both fixes;
the preceding worklist suite passed 165 tests before these narrow corrections.

Artist PID 27676 and start time were preserved. This subtask did not save, reload,
refresh or otherwise control the live artist session. Persisted enabled state
and deployed version do not prove its in-memory version; root separately verifies
the registered Panel runtime and idle state before any live refresh.
