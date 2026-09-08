"""One local Kimodo job, without importing ML packages into Blender."""

import json
import os
from pathlib import Path
import subprocess
import time
import uuid


DEFAULT_RUNTIME_ROOT = "D:/Applications/Kimodo"


def read_runtime(root):
    root = Path(root).expanduser().resolve()
    config_path = root / "character_designer_runtime.json"
    if not config_path.is_file():
        raise RuntimeError("Local Kimodo is not configured. Choose its runtime folder.")
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    for key in ("python", "backend"):
        if not Path(config.get(key, "")).is_file():
            raise RuntimeError(f"Kimodo {key} is missing; check the runtime folder.")
    config.setdefault("working_directory", str(root))
    config.setdefault("outputs", str(root / "outputs"))
    if not Path(config["working_directory"]).is_dir():
        raise RuntimeError("Kimodo working directory is missing.")
    return config


class LocalMotionJob:
    def __init__(self, root, *, prompt, duration, seed):
        config = read_runtime(root)
        self.directory = Path(config["outputs"]).resolve() / (
            time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        )
        self.directory.mkdir(parents=True)
        self.log_path = self.directory / "backend.log"
        self.status = "Starting local Kimodo…"
        self.result_path = ""
        self.fps = 30.0
        self.error = ""
        self._offset = 0
        self._pending = b""
        self._done = False
        request = self.directory / "request.json"
        request.write_text(json.dumps({
            "prompt": prompt.strip(), "duration": float(duration), "seed": int(seed),
            "diffusion_steps": 100, "output_dir": str(self.directory),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        env = os.environ.copy()
        env.update({"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
                    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        with self.log_path.open("wb") as log:
            self.process = subprocess.Popen(
                [config["python"], config["backend"], "--request", str(request)],
                cwd=config["working_directory"], env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

    def _consume(self, line):
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError):
            return
        if not isinstance(message, dict):
            return
        status = message.get("status")
        if status == "error":
            self.error = str(message.get("message") or message.get("error") or "Generation failed")
        elif status == "done":
            path = Path(message.get("path", "")).resolve()
            if (not path.is_relative_to(self.directory) or path.suffix.lower() != ".bvh"
                    or not path.is_file() or path.stat().st_size == 0):
                self.error = "Kimodo returned a missing or invalid BVH file. See the job log."
                return
            self.result_path = str(path)
            self.fps = float(message.get("fps", 30))
            self._done = True
        elif status in {"loading", "progress", "ready"}:
            self.status = str(message.get("message") or status)

    def _drain(self):
        with self.log_path.open("rb") as log:
            log.seek(self._offset)
            chunk = log.read()
            self._offset = log.tell()
        lines = (self._pending + chunk).split(b"\n")
        self._pending = lines.pop()
        for line in lines:
            self._consume(line)

    def poll(self):
        """Return True only after the worker exits and its output is validated."""
        self._drain()
        code = self.process.poll()
        if code is None:
            return False
        # It may have written its final result between the first read and exit.
        self._drain()
        if self._pending:
            self._consume(self._pending)
            self._pending = b""
        if not self.error and (code != 0 or not self._done):
            self.error = f"Kimodo stopped (exit {code}). See the job log."
        if self.error:
            self.result_path = ""
        self.status = self.error or "Ready to import preview"
        return True

    def cancel(self):
        if self.process.poll() is None:
            if os.name == "nt":
                # The adapter may own an encoder/diffusion child process.
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                               capture_output=True, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                self.process.terminate()
        self.status = "Cancelled"
        self.result_path = ""
