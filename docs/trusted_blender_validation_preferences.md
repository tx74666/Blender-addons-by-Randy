# Trusted Blender validation and preference isolation

Every agent-owned validation process needs a new CONFIG directory, including GUI
and background tests. Use `tools/run_trusted_blender_validation.py`. Its
`BLENDER_USER_CONFIG` override directs native preference saves into the evidence
folder, preserving the artist's original profile.

`--factory-startup` changes in-memory settings; it does not change the preference
write destination. Disabling automatic preference saving also does not block an
explicit Save Preferences action. Neither option substitutes for CONFIG
isolation.

## Launch modes

Use three distinct, new output filenames inside X. The record stem determines
the fresh sibling directory `<record-stem>_config`; occupied directories and
resolved paths outside X are rejected.

```powershell
python tools/run_trusted_blender_validation.py `
  --script tests/ACTUAL_TEST.py --background `
  --stdout D:/Blender/Projects/Character/X/validation/ACTUAL_RUN/stdout.log `
  --stderr D:/Blender/Projects/Character/X/validation/ACTUAL_RUN/stderr.log `
  --record D:/Blender/Projects/Character/X/validation/ACTUAL_RUN/launch.json
```

Replace the example script and output paths with the actual new run. Omit
`--background` for an owned GUI test. Default mode adds `--factory-startup` and
starts with an empty isolated CONFIG.

For a saved-profile startup check, add `--user-startup`. The launcher copies
`userpref.blend` and optional `startup.blend`, verifies their bytes, and records
their source paths, sizes, and SHA256 hashes before launch. An explicit
`--user-config-source` selects the read-only source directory when needed; it is
not the child's write destination. Native preference saves then affect only the
copies. There is no writeback step.

Both modes enable scripts for the authorized trusted X workflow. Scripts loading
these files must use `use_scripts=True`. This isolation rule does not change the
user's existing authorization for trusted local scripts. Script arguments follow
`--`; they are not extra Blender command-line options.

## Evidence and boundaries

Keep the launch record and logs with the run. Check `config_isolation.directory`,
its mode and copied-file pins before interpreting a native preference save. The
launcher isolates CONFIG only: installed scripts, add-ons, and other resource
paths remain available. It is not a general filesystem sandbox. Validation must
not explicitly write the real preference path or modify the artist's session.

The launcher tests simulate a child preference save and check that source bytes
remain unchanged. The recorded light suite passed five cases, with one Windows
directory-symlink case skipped for missing privilege; this is not a native
shortcut-behavior test.

## 2026-10-18 incident and recovery

An owned factory GUI had retained the normal CONFIG destination. The saved
contaminated profile contained that test process's `gui_temp/51692` path, while
the same process's native log recorded a real preference save. The profile had
eight default add-ons and lacked the saved Pie Menu on Drag override. A later
Character Designer enable operation added only that module to the already
contaminated baseline. The earlier process responsible for the first overwrite
is not proven.

Recovery used the pinned October 6 healthy profile, preserving its stored
27 add-ons, eight modified keymaps, 37 saved keymap items, add-on property hashes,
keyconfig properties, extension repositories, and Auto Run setting. The only
asset-library exception removed the previously authorized BlenderKit cache
library. The restored file was then read once in the artist session: all 27
modules registered, RR Helper was enabled, Pie Menu on Drag was true, Undo Steps
was 256, eight modified keymaps were present, and both BlenderKit Window bindings
were present. The profile bytes and authored scene pose stayed unchanged during
that read.

A separate background staging readback lost two compiled BlenderKit bindings
despite exact saved storage. That failed report remains retained; it did not
prove saved binding loss, and the live check found both bindings. Physical
long/drag-Tab behavior was not exercised. Do not equate restored configuration
and registered keymaps with testing every add-on feature or gesture.

The detailed evidence index is
`D:/Blender/Projects/Character/X/validation/pole_preferences_recovery_20261018/README.md`.
No scene geometry or floor-placement behavior caused this preference-profile
overwrite. Preference recovery and rig/Pole acceptance are separate records.

The restoration hash is a time-specific snapshot. A later normal native autosave
retained all 27 modules, eight saved maps/37 items, keyconfig/Pie Menu on Drag,
and comparable UserDef inline fields while changing BlenderKit authentication
properties and one repository's flag/token state. The read-only
`preferences_post_live_persistence.json` records those differences without
credential values. Do not reset current preferences merely because the file no
longer has the restoration-time hash.
