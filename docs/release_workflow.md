# Focused release workflow

This workflow reduces repeated preparation and reporting. It does not change
runtime code, validation standards, scene permissions or performance settings.
The current approved baseline and safety rules live in `AGENTS.md`.

## Choose checks from the actual behavior changed

| Change | Focused verification |
| --- | --- |
| Documentation only | Check facts and referenced paths; no Blender or FPS run. |
| Drawing text or layout only | Relevant UI/draw checks and any required visual check. Do not repeat whole-scene author scans solely for wording. |
| A checkbox starts, pauses or resumes a solver; defaults or deferred mode application change | Relevant service tests, targeted native state transitions, refusal/rollback and owned cleanup. Preserve authored data within the actual affected scope. A UI label does not make this a display-only change. |
| Animation math, rig state or authored-data writes | Existing component-specific native checks and preservation witnesses. Do not weaken guards or substitute a fixture for artist acceptance. Performance investigations require their own current authorization. |
| Release delivery | Official build, deployment to the verified Blender version and X when applicable, then the same deployment command with `--check`. Disk deployment and live runtime refresh are separate facts. |

Reuse completed checks when the inputs and relevant state have not changed.
Repeat or broaden checks for a new failure, changed input, or an unresolved
concern; record the reason. Keep all previous failures and fixed-hash harnesses.

## Prepare a complete candidate without gathering paused source changes

Use `tools/prepare_release_projection.py` instead of writing another one-off
freeze script. It reuses `tools/release_projection.py`, which also verifies
release inputs for the existing builder and deployer.

Supply the approved projection and its recorded SHA256, an entirely new output
directory, and exact package-relative files reviewed for this change:

```text
python tools/prepare_release_projection.py --module character_designer --baseline <approved-projection.json> --baseline-sha256 <recorded-sha256> --output-dir <new-stage-directory> --changed-file <reviewed-existing-file> --expected-version <package-version>
```

Repeat `--changed-file` for existing files and `--new-file` for genuinely new
files. Include `__init__.py` explicitly when changing the literal version; the
tool never edits it. With no overlay files, the preview inherits the complete
baseline. The default is read-only preview. Add `--apply` only after reviewing
the selected delta to create a new candidate with `projection.json` and
`prepare_receipt.json`. No existing directory is overwritten or removed.

The tool verifies the complete baseline file set and hashes, overlays only the
explicit list, validates Python syntax/version and verifies its generated
projection through the existing resolver. Unselected canonical changes remain
excluded. It rejects unsafe paths, aliases and contaminated inputs. A candidate
still needs the relevant validation and release decision; this tool neither
approves it nor builds, deploys, refreshes Blender or generates a live installer.

After acceptance, pass that complete projection to the existing official build
and deploy commands. Keep their checks and rollback protections unchanged.

## Finish once and keep the record small

1. Prepare source, focused checks and update artifacts independently of the
   artist UI. Parallelize independent work; serialize actual file/Editor conflicts.
2. At the final short UI window, freshly identify the artist and current loaded
   code. Use the reviewed narrow update and its own state-preservation gates.
   Unknown state is a refusal, not permission for a full reload or an old installer.
3. Record the source/projection pin, actual changed files, checks and limitations,
   disk deployment, observed live refresh, save state and owned cleanup once in
   a concise acceptance entry. Link original receipts instead of printing arrays.
4. Update only the current-release block in `AGENTS.md`; put the detailed release
   narrative in its release note and evidence. Historical raw records remain
   accessible through the archive links. Do not append another full release story
   to the agent entry point.

Existing trusted-script authorization avoids repeated script-permission prompts.
Request a brief input window only when real foreground competition requires it.
This workflow does not authorize scene/preference saves, application shutdown,
monitoring, Git upload, Unity changes or reopening the closed FPS investigation.
