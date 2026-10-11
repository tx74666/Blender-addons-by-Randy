# Character Designer 0.77.21: readable animation workspace entries

Artists can select `Cosha.workspace.json` in Connect instead of navigating the
project UUID and prefab GUID directories. This explicit reference verifies its
workspace, project and character identities, reads the original workspace index,
and returns that index's actual path. It stores no animation catalog. Refresh,
imports and returns continue using the original source path and existing checks.
References are bounded UTF-8 JSON; malformed identities, reference chains,
duplicate keys, unavailable sources and oversized files are rejected.

Connect opens an exchange root's `Animations` directory when present. With a
connected legacy workspace, it derives that root from the saved source path.
Otherwise it uses the configured exchange root. Missing/unavailable readable
directories retain the existing picker fallback. An explicit input path is
preserved, and panel drawing performs no new filesystem scan.

The local readable entries are under:
`D:/Blender/Projects/Character/Animation/UnityExports/Animations/RandomRealm2/Cosha`.
They include `Cosha.workspace.json`, one `Prepared/Walk_N.lnk` and six history
folder shortcuts named by clip and UTC preparation time. Legacy Walk is marked
as lacking a Link manifest. Each shortcut points to its original preparation
directory. Source data, UUID/GUID directories, Link identities and saved
references were retained.

The current source index lists only Walk_N. History entries are browsing aids,
not additional catalog imports. The reference reads the live index; folder
shortcuts record this organization run. `tools/organize_animation_exchange.py`
and `tools/write_animation_shortcuts.ps1` can rebuild entries explicitly after
later preparations. They protect unknown/user-edited entries and check existing
shortcut targets before replacing owned entries. They do not automatically
delete old shortcuts or run a watcher.

## Verification and deployment

- 174 tests passed against the frozen release, including nine reference-reader
  tests and six Connect picker tests.
- A factory Blender 5.2.0 LTS session enabled the installed 0.77.21, connected
  using the real readable entry, refreshed the real catalog, verified Cosha /
  Walk_N and the original saved manifest path, then disabled normally. Objects,
  Actions and Scene counts stayed unchanged. No model/animation import was run.
- Seven Windows shortcuts passed WScript target readback and the writer's
  separate check-only verification.
- The archive and both Blender 5.2 / X deployments contain 157 files. Final
  `deploy_local.py --check` returned zero different files for both targets.
  Before final delivery a concurrent Material Match release advanced both to
  0.77.22. Its archive retains this reader and UI byte-for-byte. The only changes
  from 0.77.21 are the version and two Material Matching C# files; both deployed
  trees were checked against that complete archive, with a final installed
  0.77.22 Connect/Refresh smoke. It was preserved without redeploying 0.77.21.
- The release starts from the exact already-deployed 0.77.20 archive, retaining
  its Material Match change and prior bone-length / Shoes / Scene feedback fixes.
  Only `__init__.py` version, `animation_workspace.py` and Connect's description
  / invoke in `animation_worklist_ui.py` differ. Other canonical pending changes
  remain excluded.

Evidence: X `validation/readable_animation_entries_20261011`. The manifest SHA256
is `39fa6580fd7e7ed4d5bd74e57f54bc0a5d71c5b738b8dc85458005161d3e15ec`.
The first installed smoke had a fixture-only filename typo for the retained
`unity_material_match.py`; after correcting that assertion, the installed smoke
passed. The initial log is preserved separately.

No artist Scene operation/save, Unity Editor operation or FBX roundtrip was
performed. The open artist Blender runtime was not refreshed or claimed to be
running 0.77.21. Use Refresh Add-on or reopen Blender before selecting the new
entry if the open runtime is older. Deployment is local; no Git commit, push,
PR or public release was performed.
