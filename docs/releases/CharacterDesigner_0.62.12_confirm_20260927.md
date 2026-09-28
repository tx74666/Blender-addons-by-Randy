# Character Designer 0.62.12 — enable Confirm after reapplying calibration

The user's Arms Apply succeeded, but an older confirmation signature made the
status query return REVIEW before checking the new applied signature. Confirm
was disabled even though there were no remaining changes or errors. The status
query now returns READY for an applied current candidate before considering a
historical confirmation. It does not delete history or automatically confirm.

The operator's draw callback was also reused by Blender's last-operation panel
and unconditionally displayed an excessive-displacement warning. It now draws
that warning/acknowledgment only for an actual pending oversized APPLY. Preview,
Confirm and successful Apply show no false warning. Invoke/execute signature
checks, acknowledgment thresholds, transaction and Rest guards are unchanged.
The Setup panel gives one instruction for READY: Applied. Confirm to continue.

Actual Blender 5.2.0 LTS checks, separate serial factory-startup processes:
- Calibration integration: 30 tests passed, including confirmed → input change
  → Preview/Apply → READY → explicit Confirm, and later input invalidation.
- Display/cache: 15 tests passed, including action-specific warning rendering.
- Arms/wrist integration: 9 tests passed.
- Independent read-only audit verified the two failure paths.

Live read-only evidence before deployment: Arms applied signature matched its
current plan, old confirmed signature differed, changes/errors were empty,
needs_acknowledgment was false, but state was REVIEW.
After hot refresh to 0.62.12: READY, without changing native Rest, pose, mesh,
weights, Shape Keys, finger records, calibration records or object inventory.
No Apply or Confirm was executed on the user's original. Original .blend was
not saved. Both local deployment targets match all 110 package files.

Full animation acceptance was not repeated for this status/UI fix (未验证).
Local uncommitted work; no push.

Logs and install evidence:
`D:\Blender\Projects\Character\X\task_artifacts\body_calibration_20260927\confirm_0_62_12`

Deployment backup:
`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260927-230929-312ae286`
