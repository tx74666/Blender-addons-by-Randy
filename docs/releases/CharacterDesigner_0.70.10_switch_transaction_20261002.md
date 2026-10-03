# Character Designer 0.70.10 — Original switch transactions

Original / Controls was recalculating Forearm Twist output for intermediate
constraint and channel states during a synchronous pose transfer. It also forced
another scene evaluation after the final pose had already been validated.
This release defers those intermediate corrective callbacks and calculates
output once from the final evaluated pose. The display completion only redraws
for this verified transfer; other display callers retain their existing update.

The nested runtime guard preserves its caller's state and always restores on
exception. The explicit final corrective calculation is inside the existing
mode transaction's rollback range. If that calculation fails even after doing
real output work, the full pose, constraints, display, session and attached Dress
references/owner are restored, then the recovered mode's output is recalculated.
No global initial refresh, pose matcher, rig validation or cache invalidation
rule was relaxed. No additional handler or timer was added.

## Measurement and boundaries

Serial Blender 5.2 background runs used the same saved X.blend, factory startup,
two threads and profiling overhead. The harness never saves the loaded scene.
Cosha has 184 native bones and 236 total bones; two no-edit roundtrips are measured.

| Two roundtrips | 0.70.9 | 0.70.10 candidate |
| --- | --- | --- |
| Original, first / next cycle | 0.238 / 0.160 s | 0.181 / 0.151 s |
| Controls, first / next cycle | 0.178 / 0.172 s | 0.167 / 0.164 s |
| Forearm calculations | 16 | 4 |
| Full inventory checks | 22 | 10 |
| Synchronous display-completion evaluations | 4 | 0 |
| Total switch time | 0.745 s | 0.662 s |
| Protected content after each roundtrip | Unchanged | Unchanged |

The deterministic benefit is removal of repeated work. These small samples are
affected by system load and are not click-to-screen timings or a promise of
instant switching. The native Bone Collection eye changes visibility, while
Original also transfers a pose for direct native editing and retains recovery.

X validation evidence: `original_switch_0709_followup_optimized.{json,log}` and
`original_switch_0710_followup_optimized.{json,log}`. The latter preserves 0.70.9
in its metadata because the candidate was measured before the version bump;
the final package is 0.70.10. Original/Controls still need 3/2 pose updates.
Mesh coordinates, all Shape Keys, groups/weights, topology, Rest/parents, pose
channels and display hash identically after the real-model no-edit roundtrips.

Cosha's existing `Captured loop positions are stale; capture the arm again.`
error remains visible and invalid corrective output remains paused. Performance
work did not recapture or repair it. Valid corrective fixtures also preserve the
existing pause while Original deliberately suspends owned Body constraints;
returning to Controls restores valid output from the new final pose.

## Verification and deployment status

- 15 Original-mode tests, 3 new runtime-batch cases, 7 forearm-cache cases,
  6 forearm-review cases and 11 bone-display cases pass: 42 checks total.
- New cases cover nested guards, final output, artist Shape Key preservation,
  cached invalid proof and recovery, native authored return, both ordinary
  rollback paths, and final-runtime failure across Body/Hair/Dress references.
- The final-runtime exception regression runs the real corrective update before
  throwing, then verifies complete recovery and a subsequent successful switch.
- Built and verified `character_designer-0.70.10.zip`: 130 files, 965150 bytes.
  Installed Blender 5.2 and X validation copies at 21:57:21 Asia/Shanghai;
  deployment `--check` reports zero differing files for both destinations.
  Previous files and deployment record are retained at
  `C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20261002-215721-e8a0cc92`.
- Current X window was only observed, not refreshed or switched by this phase.
  Its new runtime refresh, interactive check and artist scene save remain pending
  input coordination. The latest verified X.blend save is 20:15:12 from 0.70.8;
  isolated background tests never saved it. Source deployment is not proof that
  the running window has loaded the new version.
- Changes are local. No Git commit, push, PR or publication.

The optimization ledger is maintained in the existing Codex Console database:
`D:\Codex\資料庫\电脑与工作环境\Blender性能優化台帳.md`. It contains ownership,
verified model/effort provenance, evidence links, known limits and next triggers.
Other UI redraw candidates are recorded as unmeasured, rather than implemented
without a profile.
