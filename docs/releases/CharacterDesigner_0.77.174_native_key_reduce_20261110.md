# Character Designer 0.77.174 — unreleased preparation superseded by 0.77.177

**Unreleased preparation, superseded by 0.77.177.** No 0.77.174 release,
deployment or live adoption occurred. The original preparation and check
narrative below is retained, including its then-pending items.

The separate **Simplify Keys** tool and its dedicated **Before / After**
comparison buttons are removed. Their two operators are no longer registered,
and `animation_key_reduce.py` is omitted from the shipped add-on. Existing
Actions, retained versions and reduction metadata are preserved.

For manual reduction, duplicate the editable Action and use Blender's Graph
Editor: **Key > Density > Decimate (Allowed Change)** or **Decimate (Ratio)**.
**Clean Keyframes** handles redundant values. Native curve-error settings are
not a whole-rig position or rotation guarantee.

The common `animation_key_reduce_math.py` remains because controller conversion
uses it to fit newly solved samples. Controller conversion, its quality checks,
and its existing automatic sample simplification option are unchanged. Export
Base Pose retains the same active-Custom/Action-slot eligibility check under
the neutral helper name `_active_custom_action`.

## Scope and source

The complete candidate extends final Colors173 `stage173_06`, SHA256
`25e07d9a6f2d860a383ddfd6367d7607195f59802e7805ce5b3ab07a98e9e4ea`.
Only `README.md`, the literal version in `__init__.py`, and the scoped Worklist
UI removal change; the standalone backend is deleted. The other 264 files are
byte-exact. The result contains 267 files / 235 Python files.

Candidate: `D:/Blender/Projects/Character/X/validation/remove_custom_key_reduce_20261110/stage174_03/projection.json`,
SHA256 `b6ecc87f21dc54c54ff0152e9a26800b5e58ebcdb2084a0053c54f265ff7d863`.
Held canonical Worklist `_active_side` and import-order differences are excluded.
Original backend, retired dedicated tests and edited documentation are backed
up under this release's validation directory; historical releases stay intact.

## Validation and delivery

Related canonical pure checks: 313 passed of 316. The three existing Physics
folding/message failures match the previously recorded171 failures and remain
unfixed. Frozen Worklist/controller-integration checks passed 95/95; the final
rebase changes only the untouched Color Center file, so those exact-input
checks are reused with a separate byte-comparison receipt.

The first private native preflight refused before writes because an inherited
reader did not recognize the new AddonPreferences RNA category. Its failure
is retained. A separate adapter uses the same strict native-owner verification
for that category; the subsequent actual two-operator transaction passed five
rollback phases, foreign-owner and stale-plan refusal, success and zero-write
ALREADY checks, retained backend-module identities, and normal unregistration.
An earlier unreleased Colors parent passed its first public registration but
failed a repeated registration because three child Panels lacked explicit IDs.
That failure remains recorded by the Action Browser task. The final parent
adds those IDs. Final-base cold/repeated registration and official delivery
are pending; earlier first-registration results are not relabeled as repeat
registration proof.

No Scene/preferences save, restart, full reload, animation reduction, keyframe
edit, Unity change or Git upload is included. No performance improvement is
claimed. Detailed receipts live in
`D:/Blender/Projects/Character/X/validation/remove_custom_key_reduce_20261110`.
