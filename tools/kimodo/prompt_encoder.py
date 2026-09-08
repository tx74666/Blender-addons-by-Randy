"""Encode one local Kimodo prompt, persist its vector, and exit to free memory.

This process owns only the text encoder. The caller must wait for its successful
exit before loading the motion model. It never downloads models at generation time.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def report(status: str, message: str, **extra) -> None:
    print(json.dumps({"status": status, "message": message, **extra}, ensure_ascii=False), flush=True)


def memory_diagnostics(device=None) -> dict:
    """Report process peaks; these are measurements, not minimum requirements."""
    result = {}
    torch = sys.modules.get("torch")
    try:
        if torch is not None and device is not None and torch.cuda.is_initialized():
            result["torch_peak_allocated_mib"] = round(torch.cuda.max_memory_allocated(device) / 2**20, 2)
            result["torch_peak_reserved_mib"] = round(torch.cuda.max_memory_reserved(device) / 2**20, 2)
    except Exception:
        pass  # Diagnostics must not replace the original CUDA failure.
    try:
        import psutil
        info = psutil.Process().memory_info()
        result["process_rss_mib"] = round(info.rss / 2**20, 2)
        if hasattr(info, "peak_wset"):
            result["process_peak_wset_mib"] = round(info.peak_wset / 2**20, 2)
        if hasattr(info, "peak_pagefile"):
            result["process_peak_pagefile_mib"] = round(info.peak_pagefile / 2**20, 2)
    except Exception:
        pass
    return result


def read_request(request_path: Path) -> dict:
    request = json.loads(request_path.read_text(encoding="utf-8-sig"))
    if not isinstance(request, dict):
        raise ValueError("Request must be a JSON object")
    if not isinstance(request.get("prompt"), str):
        raise ValueError("prompt must be text")
    for field in ("cache_path", "model_dir"):
        if not isinstance(request.get(field), str) or not request[field].strip():
            raise ValueError(f"{field} must be a local path")
    device = request.get("device", "cuda:0")
    if not isinstance(device, str) or not (device == "cuda" or device.startswith("cuda:")):
        raise ValueError("This local NF4 encoder requires a CUDA device")
    request["device"] = device
    return request


def run(request: dict) -> tuple[Path, dict]:
    started = time.monotonic()
    load_seconds = 0.0
    encode_seconds = 0.0
    model_dir = Path(request["model_dir"]).resolve()
    cache_path = Path(request["cache_path"]).resolve()
    for filename in ("config.json", "llm2vec_config.json", "tokenizer.json", "model.safetensors"):
        if not (model_dir / filename).is_file():
            raise FileNotFoundError(f"The local text encoder is incomplete: {model_dir / filename}")
    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    quant = config.get("quantization_config", {})
    if not quant.get("load_in_4bit") or quant.get("bnb_4bit_quant_type") != "nf4":
        raise ValueError("The configured text encoder is not the expected NF4 model")

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    report("progress", "Preparing local text encoder")
    # Keep third-party logs off the JSON protocol stream.
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np
        import torch
        from kimodo.sanitize import sanitize_texts
        from kimodo.model.llm2vec.llm2vec import LLM2Vec

        prompt = sanitize_texts([request["prompt"]])[0]
        device = torch.device(request["device"])
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable in the configured Kimodo runtime")
        torch.cuda.reset_peak_memory_stats(device)

    if not prompt:
        # Kimodo itself zeroes text features for empty prompts.
        vector = np.zeros((1, 4096), dtype=np.float32)
    else:
        report("progress", "Loading local NF4 text encoder; motion model remains unloaded")
        load_started = time.monotonic()
        with contextlib.redirect_stdout(sys.stderr):
            encoder = LLM2Vec.from_pretrained(
                base_model_name_or_path=str(model_dir),
                peft_model_name_or_path=None,
                torch_dtype=torch.bfloat16,
                device_map=str(device),
                local_files_only=True,
            )
            encoder.eval()
        load_seconds = time.monotonic() - load_started
        report("progress", "Encoding motion description")
        encode_started = time.monotonic()
        with contextlib.redirect_stdout(sys.stderr), torch.inference_mode():
            encoded = encoder.encode(
                [prompt], batch_size=1, show_progress_bar=False,
                convert_to_tensor=True, device=str(device),
            )
            if isinstance(encoded, torch.Tensor):
                vector = encoded.detach().to(device="cpu", dtype=torch.float32).numpy()
            else:
                vector = np.asarray(encoded, dtype=np.float32)
        encode_seconds = time.monotonic() - encode_started
        # Do not offload the model into RAM. Exiting this process releases both
        # the CUDA allocation and process memory before diffusion starts.

    vector = np.asarray(vector, dtype=np.float32)
    if vector.shape != (1, 4096) or not np.isfinite(vector).all():
        raise RuntimeError(f"Unexpected text embedding output: {vector.shape}")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=cache_path.parent, suffix=".npy.tmp", delete=False) as handle:
            temporary_path = Path(handle.name)
            np.save(handle, vector, allow_pickle=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, cache_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    diagnostics = memory_diagnostics(device)
    diagnostics.update(total_seconds=round(time.monotonic() - started, 2),
                       load_seconds=round(load_seconds, 2),
                       encode_seconds=round(encode_seconds, 2))
    return cache_path, diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    request = None
    try:
        request = read_request(args.request)
        result, diagnostics = run(request)
    except Exception as exc:
        diagnostic_device = request.get("device") if request else None
        diagnostics = memory_diagnostics(diagnostic_device)
        diagnostics["total_seconds"] = round(time.monotonic() - started, 2)
        report("error", f"{type(exc).__name__}: {exc}", **diagnostics)
        return 1
    report("done", "Prompt cache saved; encoder process will exit", path=str(result), **diagnostics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
