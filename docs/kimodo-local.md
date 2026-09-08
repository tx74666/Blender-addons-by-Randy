# Kimodo local animation

The Character Designer Animation page uses a separate Python process, configured
by `character_designer_runtime.json` in the chosen Kimodo folder. It never imports
PyTorch into Blender. The installed folder on Randy's machine is
`D:\Applications\Kimodo`.

## Components

- [NVIDIA Kimodo](https://github.com/nv-tlabs/kimodo): Apache 2.0 code;
  [SOMA RP v1 model](https://huggingface.co/nvidia/Kimodo-SOMA-RP-v1) uses NVIDIA's
  Open Model License. Model terms are separate from code licensing.
- [Aero-Ex Kimodo fork](https://github.com/Aero-Ex/kimodo): Windows-compatible
  runtime and prebuilt MotionCorrection wheel.
- [Community NF4 encoder](https://huggingface.co/Aero-Ex/KIMODO-Meta3_llm2vec_NF4):
  approximately 4.67GB download; SOMA motion model is approximately 1.13GB.
- Character Designer supplies the local job UI and its own FK body transfer.
  Blaze Puppeteer is not required or installed by this setup.

The model downloads are pinned and SHA256-checked; each model folder contains a
`download-manifest.json`. Runtime dependencies are isolated from Blender's Python.
Generation uses offline model loading. No paid API, credit system, or hosted prompt
encoder is configured.

## Adapter contract

Configuration contains absolute `python`, `backend`, `working_directory`, and
`outputs` paths. Blender invokes `python backend --request request.json` without a
shell. The request contains `prompt`, `duration`, `seed`, `diffusion_steps`, and
`output_dir`. Status JSON lines use `loading`, `progress`, `error`, or `done`.
Only final `done` contains the BVH `path` (inside that job's output folder) and
`fps`. A successful process exit and a valid BVH are both required before import.

For an uncached prompt, a separate NF4 encoder process writes one small embedding
and exits. Then a motion process loads the SOMA model using the cached embedding.
This avoids keeping both models alive simultaneously. Cancel terminates the job's
process tree on Windows. Jobs and logs are retained in the outputs directory.

## Use and limits

Start with two seconds and the default walking prompt. Import Preview is explicit;
generation itself does not edit the Blender scene. Body transfer creates a unique
Action and retains the previous Action. It calibrates anatomical rest directions
to account for T Pose versus A Pose, then scales movement to the character's proportions.
It does not clear rig constraints or
key the character's hair chains or independent skirt rig.

This is the initial text-motion workflow. Custom pose/path constraints, IK-control
retargeting, hand/finger transfer, and foot-contact cleanup are separate future
work. A generated clip still needs visual review and normal animation editing.

The RTX 3060 Laptop has 6GB VRAM and the machine has 16GB RAM. A first prompt needs
most of the GPU for the NF4 encoder. Close unused GPU-heavy projects after saving
them if the runtime reports insufficient memory. Cached prompts have lower peak
memory requirements. Do not replace failed text encoding with a dummy vector and
describe the result as text-conditioned generation.

## Validation on 2026-09-08

- Nine Blender 5.2 retargeting checks passed, including T-to-A pose calibration,
  scaling, root motion, restoration after saving/reopening, and rig protection.
- Runtime process/result validation, BVH import, UI page routing, and simultaneous
  RR Helper/Character Designer registration passed.
- Actual local unconditioned generation completed: 2 seconds, 60 frames at 30 FPS,
  100 diffusion steps and native motion postprocessing; diffusion/export took 7.23s.
- Its real X transfer was saved separately at
  `D:\Blender\Projects\Character\X\outputs\animation\kimodo_motion_smoke.blend`.
  The original X file hash is unchanged. All 22 mapped body directions match the
  source after calibration; the 52 hair bones and skirt attachment are preserved.
- This transfer still lifts toe joints about 8–11mm above their original height.
  Foot-contact cleanup remains necessary for a finished animation.
- The text encoder package and local model configuration import correctly, but
  actual first-prompt encoding remains unverified because available RAM/VRAM is
  below the runtime's loading threshold. No claim of text-generation readiness is
  made from the unconditioned test. The prepared walking request is in the runtime's
  `verification/first_prompt_request.json`.
