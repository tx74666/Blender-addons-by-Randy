# Character Designer 0.77.178 — daily working motions

Status: complete178 code/package/dual disk and actual scoped code adoption passed.
It extends Animation2's approved177 after Colors176. Its artist classification
attempt refused before any metadata writes because the detached X scene retained
a legacy Main Rig marker. That known display-only compatibility issue was fixed
in179; actual classification, physical menus and native save subsequently passed
there. Earlier175 and private176-based178 candidates remain separate evidence.

[Actual178 code adoption](D:/Blender/Projects/Character/X/validation/action_browser_20261110/artist_browser178_code01.json),
[zero-write classification refusal](D:/Blender/Projects/Character/X/validation/action_browser_20261110/classification_refusal01.json),
[completed179 delivery](D:/MyRepository/Blender-addons-by-Randy/docs/releases/CharacterDesigner_0.77.179_daily_actions_compatibility_20261110.md).

The ordinary Action Editor header and unmanaged Animation panel now share a
compact working-motion selector. The selected working Action has a short label;
explicitly archived stages, linked Sources, Inputs and checkpoint containers are
outside the daily list. All opens the original native Action/slot controls.
The current Action remains visible and unknown new local motions stay available.
Explicit display groups prefer the current version or a selection made in this
session. Ambiguous groups remain visible; names and numerical suffixes do not
prove ownership or determine a latest version.

Display classification writes five dedicated string properties only. It does
not rename Actions, replace legacy ownership records, add ID references, delete
data, change keys, reset poses or write NLA settings. Ordinary native transforms,
key insertion and curve editing continue on the selected Action. Existing
conversion/variant services are unchanged. On successful daily-list selection,
the old and new editable working motions are retained with Fake User so a later
save cannot discard an unassigned working motion. The operation copies no Action;
its failure path restores the original binding and both prior retention flags.

Default header drawing reads the current Action and display metadata only.
Opening the menu enumerates Action/slot headers; selection additionally checks
actual other-armature Action/slot and NLA consumers. There is no key/curve scan
in header drawing and no added periodic handler or timer. Owned transient
animation operations keep their selection guards. Native Rest and frame/mode
state are preserved when ordinary selection is available.

The product delta changes animation.py's existing runtime register,
runtime unregister and Animation panel draw bodies, plus the new
animation_action_browser.py module. Its two RNA classes, transient WM view
property and narrow native Header classmethod wrapper are registered by the new
module; existing package classes are not reconstructed. Native All remains the
complete data-management path. Source-only backups and old failed validations
are retained under validation/action_browser_20261110.

Final code and disk verification:

- Complete177 baseline projection SHA
  `1145f99b89cc7e4d63bd709d149458d45663b6d975a7820d6f4b1aa678f15e1d`.
  Final178 `stage178_02/projection.json` SHA
  `11632f4eb7bf6563b01a5a62a388ea2aaead7a5a22f9772ee716c531d8e8bdb9`:
  268 files/236 Python, three changed files, one new file and 264 inherited exact.
  The first178 candidate and its accidental README blank lines are retained;
  final02 has no extra blank lines and preserves177's existing newline bytes.
- Official ZIP SHA
  `6be1cf05a1c5c3b9760ee9d73c72870f6c99e5b725564c51360d14b3115d96f3`.
  Independent ZIP CRC and all268 payload hashes match the final projection.
  Roaming Blender5.2 and X each match all268 files with no missing, changed or
  extra source files; only Python caches are excluded. Deployment backup:
  `C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261110-105649-0dc1b145`.
  [Disk receipt](D:/Blender/Projects/Character/X/validation/action_browser_20261110/disk178_01.json).
- Known native binary is `D:/Blender5.2/blender.exe`, Blender5.2.0 LTS.
  The artist process path matches that executable. This is path/disk evidence,
  without a whole-runtime body or artist-state audit.

Validation:

- 41 focused pure/module/integration tests and four existing unmanaged UI tests.
- 54 tiny native checks on the final browser source, including real RNA/slots,
  retention, native Rest, metadata preservation, consumer refusal and rollback.
  Three services in that fixture are explicit stubs; the layout spy is not a
  native UILayout or artist screenshot proof.
- Genuine complete177-to-final178 warm adoption, injected post-registration
  failure/rollback, fresh success, ALREADY and retained-motion selection passed45
  checks with real source admission and services. Adoption changed three existing
  code/doc pairs, registered two new RNA classes and retained the SourceSignature
  reference. Its updater used zero Scene APIs; ALREADY made zero writes. No artist
  Main or Scene/preferences save was involved. Owned worker44556 exited normally.
  [Final warm receipt](D:/Blender/Projects/Character/X/validation/action_browser_20261110/native_adoption178_final177_02_result.json).
- Sixteen pure component methods exercise the actual transaction and owner-reader
  entry points. Their transaction `_check` uses an explicit independent mock;
  they do not substitute for the genuine final warm admission above.
- The obsolete175 warm failure identified a raw-docstring comparison issue and
  later an immutable reader without AddonPreferences support. The new178 updater
  matches the exact raw docstring and checks that native owner locally; immutable
  readers were not changed.

Retained failures and limits:

- Native01 crashed while binding a newly created fixture before loading the
  product module. Genuine single-bone fixtures and progress journals were added;
  the exact earlier crash cause remains unproven.
- The old175 complete-package test passed first registration but failed repeated
  integrity checking on inherited Colors Panel identifiers. Colors' owner fixed
  these separately; that result was not relabeled.
- Complete178 public repeated registration kept the same handler identities and
  counts but reordered legacy callbacks. Original exact-order failures remain.
  Independent complete177 native comparison passed15 checks and confirmed the
  inherited reorder. The final178 explicit relative-baseline test passed377 checks:
  callback rosters and the exact inherited sequence transformations matched;
  repeated browser/animation-runtime registration kept every handler exactly
  unchanged. This does not claim global package order-idempotency or playback
  performance. Genuine scoped warm adoption kept all handlers exactly unchanged.
  [Final cold receipt](D:/Blender/Projects/Character/X/validation/action_browser_20261110/native_package178_final01_result.json),
  [177 control](D:/Blender/Projects/Character/X/validation/action_browser_20261110/native_baseline177_handlers01_result.json).
- Actual178 artist code adoption passed. Its classification attempt made zero
  metadata writes and preserved the legacy marker when it refused. Actual179
  later classified seven local Actions, passed the physical daily/All menu
  checks and saved the current X scene normally. These later results do not
  relabel178's refusal or certify managed switching. No Unity or Git upload is
  part of this change.
