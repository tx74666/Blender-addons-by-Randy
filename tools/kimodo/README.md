# Local runtime helpers

These two original scripts are the maintained source for Character Designer's
separate local Kimodo runtime. They are not installed into Blender's Python.
The model checkpoints and isolated virtual environment remain outside this repo.

After editing and validating these sources, deploy both scripts to the configured
Kimodo root (currently `D:\Applications\Kimodo`). The backend intentionally finds
`models/`, `runtime/llm2vec-model/`, and `prompt_encoder.py` relative to itself.
The Character Designer add-on reads the root's `character_designer_runtime.json`.

`requirements-lock.txt` records the tested Windows/Python 3.12 environment.
PyTorch uses its CUDA 12.1 wheel index, and MotionCorrection uses the pinned
Windows wheel; do not assume a plain PyPI installation reproduces these binaries.
The installed runtime's `SETUP.md` and manifests record upstream hashes and paths.
No checkpoint, proprietary plug-in, or upstream source is vendored here.
