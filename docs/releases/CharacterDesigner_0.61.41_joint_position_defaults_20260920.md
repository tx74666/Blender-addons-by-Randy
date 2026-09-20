# Character Designer 0.61.41 — joint position defaults and active finger

## Changes

- The Joint Topology & Weights header now appends the active finger identity,
  even when several Basic Setup pairs are highlighted for viewing.
- Two compact icons beside Prepare restore default joint positions (loop-back)
  and save current positions as defaults (file-check). Hover explains their scope
  and shows reset values. Shift-restore uses the prepared automatic baseline
  without deleting the user's saved preset.
- Defaults belong to the character and digit pair, not a global shared slider
  value or separate left/right configurations. They persist through save/reopen
  and Release. Reprepare never overwrites current positions or custom defaults.
- Existing automatic values still come from valid bone junction projections
  along the uninset topology span. No new universal anatomical proportion is
  guessed. An unavailable chain/count uses the existing topology estimate, with
  its origin/reason retained and identified in the tooltip.
- Reset/save affects positions only. Marker count, widths, spacing, weights,
  bones, mesh, Shape Keys and other fingers remain unchanged. Source and both
  side recipes must validate before any replacement; overlaps, changed sources,
  missing/revised references and invalid presets fail without partial updates.
- Both actions keep the persistent preview eye intent and use the existing
  coalesced rebuild. Neither panel drawing nor dragging gains a new scan.

## Verification actually run

All Blender checks used separate `--background --factory-startup
--disable-autoexec --threads 1` processes, sequentially, not the user's live scene.

- Joint defaults: **6 passed**, including per-pair isolation, side sharing,
  single-joint thumb, legacy initialization, failure atomicity, unchanged geometry,
  source/revision guards, persistence, reprepare/release and preview eye intent.
- Eye/monitor lifecycle: **11 passed**.
- Complete finger workflow: **12 passed**.
- Packed proof/drag regression: **6 passed** (including the prior release's
  corrected stale-reference expectation for edited weights/Shape Keys).
- Shared preview cache: **5 passed**.
- Multi-selection: **5 passed**.
- Python compileall and git diff whitespace checks passed (Git warns about its
  existing LF/CRLF policy).

Total: **45 automated cases**. No new GUI/native mouse or real-input latency test;
no claim that the new button layout has been visually inspected in the live UI.
Save/reopen tests wrote only disposable synthetic fixture files. Bundled brush
asset relative-path warnings in those fixtures are not plugin assertion failures.

The saved `D:\Blender\Projects\Character\X\X.blend` was also loaded in a disposable
process with embedded scripts disabled. Its bone-derived defaults, normalized
to each existing topology span, were approximately:

| Finger | Joint 1 | Joint 2 |
| --- | ---: | ---: |
| Index | 0.537 | 0.761 |
| Middle | 0.503 | 0.778 |
| Ring | 0.511 | 0.774 |
| Pinky | 0.462 | 0.751 |

Thumb used the .34/.68 **topology estimate**, not a claimed anatomical result.
The actual reason was `thumb.01.L: named chain is outside the captured finger
range; check character/rig identity.` The inspection prints this reason; no
bone/range guard was relaxed to manufacture a successful match. Existing unsaved Blender
edits are not included in these numbers. All five candidate position sets passed
the current paired topology-plan checks; this is not a skin-deformation or weight
acceptance test. The source mesh fingerprint and original blend-file SHA256 stayed
unchanged. No model file was saved or overwritten.

## Deployment

Local release only, using the canonical build/deploy scripts. Unity animation
drafts remain shelved and are not part of this release. No Unity/Adventure work,
no user application closed or restarted, and no git commit or push.

Build verified `dist/character_designer-0.61.41.zip` (104 files). Blender 5.2's
installed add-on and X's validation copy each updated three runtime files, and
`deploy_local.py --check` reported zero differences for both. Previous files are
backed up in `C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260920-212602-0389ba74`.
