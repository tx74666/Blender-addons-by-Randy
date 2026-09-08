"""Local Kimodo job runner. Text encoding and diffusion never share a process.

Public CLI: python character_designer_backend.py --request <request.json>
The request contains prompt, duration, seed, output_dir and diffusion_steps.
stdout is JSON Lines; third-party diagnostics are redirected to stderr.
No network services, account credentials, or automatic downloads are used here.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parent
MODEL_NAME = "Kimodo-SOMA-RP-v1"
ENCODER_REVISION = "207f0c64a7325f6045d15c68c5ea9e961c18fafd"
PROTOCOL_OUT = sys.stdout


def emit(status, message, **fields):
    print(json.dumps({"status": status, "message": message, **fields}, ensure_ascii=False),
          file=PROTOCOL_OUT, flush=True)


def offline_environment():
    env = os.environ.copy()
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                "TEXT_ENCODER_MODE": "local", "TOKENIZERS_PARALLELISM": "false",
                "CHECKPOINT_DIR": str(ROOT / "models"), "PYTHONUNBUFFERED": "1",
                "HF_HOME": str(ROOT / "runtime" / "hf-cache")})
    return env


def read_request(path):
    if path.stat().st_size > 65536:
        raise ValueError("The generation request is too large")
    req = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(req, dict) or not isinstance(req.get("prompt"), str):
        raise ValueError("The request must contain a text prompt")
    if len(req["prompt"]) > 2000:
        raise ValueError("Keep the motion description below 2000 characters")
    if not req["prompt"].strip() and not req.get("allow_unconditioned", False):
        raise ValueError("Enter a motion description")
    duration = float(req.get("duration", 3.0))
    if not math.isfinite(duration) or not 1.0 <= duration <= 10.0:
        raise ValueError("Duration must be between 1 and 10 seconds")
    steps = int(req.get("diffusion_steps", 100))
    if not 1 <= steps <= 200:
        raise ValueError("Diffusion steps must be between 1 and 200")
    seed = int(req.get("seed", 42))
    if not 0 <= seed <= 2147483647:
        raise ValueError("Seed must be between 0 and 2147483647")
    output_value = req.get("output_dir")
    if not isinstance(output_value, str) or not Path(output_value).is_absolute():
        raise ValueError("output_dir must be an absolute local directory")
    req.update(duration=duration, diffusion_steps=steps, seed=seed,
               output_dir=str(Path(output_value).resolve()))
    return req


def ensure_capacity(*, encoding):
    """Fail before loading weights when the desktop is already short of memory."""
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total_physical", ctypes.c_ulonglong), ("available_physical", ctypes.c_ulonglong),
                        ("total_page", ctypes.c_ulonglong), ("available_page", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                        ("available_extended", ctypes.c_ulonglong)]
        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            required_ram = 3.0 if encoding else 2.0
            free_ram = memory.available_physical / (1024 ** 3)
            if free_ram < required_ram:
                raise RuntimeError(f"Available RAM is {free_ram:.1f} GB; this stage needs at least "
                                   f"{required_ram:.0f} GB free. Save and close unused 3D applications, then retry")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                            capture_output=True, text=True, timeout=15, **options)
    if result.returncode != 0:
        raise RuntimeError("Could not check available NVIDIA GPU memory")
    free_vram = int(result.stdout.splitlines()[0].strip())
    required_vram = 5120 if encoding else 2048
    if free_vram < required_vram:
        raise RuntimeError(f"Available GPU memory is {free_vram / 1024:.1f} GB; this stage needs "
                           f"at least {required_vram / 1024:.0f} GB free. Close unused GPU applications, then retry")


def run_child(script, request_path, *, diffusion=False):
    cmd = [sys.executable, str(script), "--request", str(request_path)]
    if diffusion:
        cmd.append("--diffusion-worker")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    child = subprocess.Popen(cmd, cwd=ROOT, env=offline_environment(),
                             stdout=subprocess.PIPE, stderr=sys.stderr,
                             text=True, encoding="utf-8", errors="replace", **options)
    result = None
    failure = None
    try:
        for line in child.stdout:
            try:
                item = json.loads(line)
            except (ValueError, TypeError):
                print(line.rstrip(), file=sys.stderr, flush=True)
                continue
            if not isinstance(item, dict):
                continue
            status = item.get("status")
            if status == "done":
                result = item
            elif status == "error":
                failure = item.get("message", "The local worker failed")
            else:
                emit(status or "progress", item.get("message", "Working locally"))
        code = child.wait()
    except BaseException:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        raise
    finally:
        child.stdout.close()
    if code != 0 or result is None:
        raise RuntimeError(failure or f"Local worker exited with code {code}")
    return result


def run_job(req):
    job_started = time.monotonic()
    encoder_result = None
    model_dir = ROOT / "models" / MODEL_NAME
    if not (model_dir / "config.yaml").is_file():
        raise FileNotFoundError("The local Kimodo motion model is not installed")
    out = Path(req["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    req = dict(req, job_id=job_id)
    if req["prompt"].strip():
        cache_key = hashlib.sha256(json.dumps(
            {"prompt": req["prompt"], "encoder": ENCODER_REVISION, "format": 1},
            sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        cache = ROOT / "runtime" / "prompt-cache" / (cache_key + ".npy")
        req["cache_path"] = str(cache)
        if not cache.is_file():
            ensure_capacity(encoding=True)
            emit("loading", "Encoding the description locally; the motion model stays unloaded")
            encoder_req = out / (job_id + "_encoder_request.json")
            encoder_req.write_text(json.dumps({
                "prompt": req["prompt"], "cache_path": str(cache),
                "model_dir": str(ROOT / "runtime" / "llm2vec-model"), "device": "cuda:0",
            }, ensure_ascii=False), encoding="utf-8")
            encoder_result = run_child(ROOT / "prompt_encoder.py", encoder_req)
            cache.with_suffix(".json").write_text(json.dumps(encoder_result, indent=2), encoding="utf-8")
        else:
            emit("progress", "Reusing the locally cached description")
    worker_req = out / (job_id + "_motion_request.json")
    worker_req.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    emit("progress", "Text encoder has exited; starting the motion process")
    ensure_capacity(encoding=False)
    result = run_child(Path(__file__), worker_req, diffusion=True)
    result["total_seconds"] = round(time.monotonic() - job_started, 2)
    if encoder_result is not None:
        result["encoder_metrics"] = {k: v for k, v in encoder_result.items()
                                     if k not in {"status", "message", "path"}}
    emit("done", "Local motion generated", **{k: v for k, v in result.items()
                                               if k not in {"status", "message"}})


def diffusion_worker(req):
    os.environ.update(offline_environment())
    emit("loading", "Loading the local Kimodo motion model")
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np
        import torch
        from kimodo import load_model
        from kimodo.sanitize import sanitize_texts
        from kimodo.tools import seed_everything
        from kimodo.exports.bvh import save_motion_bvh
        from kimodo.skeleton import SOMASkeleton30, global_rots_to_local_rots

        torch.set_num_threads(2)
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable in the configured local runtime")
        prompt = sanitize_texts([req["prompt"]])[0]
        if prompt:
            cached = np.load(req["cache_path"], allow_pickle=False)
            if cached.shape != (1, 4096) or not np.isfinite(cached).all():
                raise ValueError("The cached local prompt embedding is invalid")
        else:
            cached = np.zeros((1, 4096), dtype=np.float32)

        class CachedEncoder(torch.nn.Module):
            def forward(self, texts):
                normal = sanitize_texts(list(texts))
                values = []
                for text in normal:
                    if not text:
                        values.append(np.zeros((1, 4096), dtype=np.float32))
                    elif text == prompt:
                        values.append(cached)
                    else:
                        raise ValueError("Unexpected prompt requested by the motion model")
                return torch.from_numpy(np.stack(values)).float(), [1] * len(values)

        model = load_model(MODEL_NAME, device="cuda:0", text_encoder=CachedEncoder())
        fps = float(model.fps)
        frames = max(1, round(req["duration"] * fps))
        seed_everything(req["seed"])

    emit("progress", f"Generating {frames} frames locally and correcting foot contacts")
    started = time.monotonic()
    def progress(iterable, *args, **kwargs):
        total = len(iterable)
        last = 0.0
        for index, value in enumerate(iterable):
            now = time.monotonic()
            if index == 0 or now - last > 5:
                emit("progress", f"Motion diffusion {index + 1}/{total}")
                last = now
            yield value

    with contextlib.redirect_stdout(sys.stderr), torch.inference_mode():
        output = model([req["prompt"]], [frames],
                       num_denoising_steps=req["diffusion_steps"], num_samples=1,
                       multi_prompt=False, post_processing=True,
                       return_numpy=True, progress_bar=progress)
        skeleton = model.skeleton
        if isinstance(skeleton, SOMASkeleton30):
            skeleton = skeleton.somaskel77.to("cuda:0")
        positions = np.asarray(output["posed_joints"])[0]
        rotations = np.asarray(output["global_rot_mats"])[0]
        if positions.shape[0] != frames or not np.isfinite(positions).all() or not np.isfinite(rotations).all():
            raise ValueError("The motion model returned invalid frame data")
        local_rots = global_rots_to_local_rots(torch.from_numpy(rotations).to("cuda:0"), skeleton)
        root_pos = torch.from_numpy(positions[:, skeleton.root_idx]).to("cuda:0")
        out = Path(req["output_dir"])
        basename = "Kimodo_" + req["job_id"]
        bvh = out / (basename + ".bvh")
        npz = out / (basename + ".npz")
        save_motion_bvh(str(bvh), local_rots, root_pos, skeleton=skeleton,
                        fps=fps, standard_tpose=True)
        # Use plain arrays only so Blender can read without pickle support.
        np.savez_compressed(npz, posed_joints=positions, global_rot_mats=rotations,
                            neutral_joints=skeleton.neutral_joints.detach().cpu().numpy(),
                            joint_names=np.asarray(skeleton.bone_order_names, dtype=str),
                            fps=np.asarray(fps), seed=np.asarray(req["seed"]),
                            prompt=np.asarray(req["prompt"]))
    emit("done", "Local motion generated", path=str(bvh), npz_path=str(npz),
         fps=fps, frames=frames, seed=req["seed"], duration=req["duration"],
         motion_seconds=round(time.monotonic() - started, 2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--diffusion-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        req = read_request(args.request)
        if args.diffusion_worker:
            diffusion_worker(req)
        else:
            run_job(req)
        return 0
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        emit("error", f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
