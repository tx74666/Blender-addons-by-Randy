"""Exercise real child-process completion and result validation without GPU models."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "addons/character_designer/animation_runtime.py"
spec = importlib.util.spec_from_file_location("animation_runtime", SOURCE)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeTests(unittest.TestCase):
    def test_done_written_during_exit_check(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            result = folder / "motion.bvh"
            result.write_text("HIERARCHY", encoding="utf-8")
            job = runtime.LocalMotionJob.__new__(runtime.LocalMotionJob)
            job.directory = folder
            job.log_path = folder / "backend.log"
            job.log_path.write_bytes(b"")
            job._offset, job._pending, job._done = 0, b"", False
            job.error, job.result_path = "", ""
            class ExitingProcess:
                def poll(self):
                    job.log_path.write_text(json.dumps({"status": "done", "path": str(result)}) + "\n",
                                            encoding="utf-8")
                    return 0
            job.process = ExitingProcess()
            self.assertTrue(job.poll())
            self.assertFalse(job.error)
            self.assertEqual(job.result_path, str(result))

    def test_local_protocol(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backend = root / "backend.py"
            backend.write_text('''import json, pathlib, sys
request = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
folder = pathlib.Path(request["output_dir"])
prompt = request["prompt"]
print("library startup log", flush=True)
print(json.dumps({"status":"progress", "message":"Working"}), flush=True)
if prompt == "failure":
    print(json.dumps({"status":"error", "message":"Expected failure"}), flush=True)
    sys.exit(1)
result = folder / "motion.bvh" if prompt == "success" else folder.parent / "outside.bvh"
result.write_text("HIERARCHY", encoding="utf-8")
print(json.dumps({"status":"done", "path":str(result), "fps":30}), flush=True)
''', encoding="utf-8")
            (root / "character_designer_runtime.json").write_text(json.dumps({
                "python": sys.executable, "backend": str(backend),
                "working_directory": str(root), "outputs": str(root / "outputs"),
            }), encoding="utf-8")
            for prompt in ("success", "failure", "outside"):
                job = runtime.LocalMotionJob(str(root), prompt=prompt, duration=2, seed=42)
                deadline = time.monotonic() + 10
                while not job.poll():
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.02)
                if prompt == "success":
                    self.assertFalse(job.error)
                    self.assertTrue(Path(job.result_path).is_file())
                else:
                    self.assertTrue(job.error)
                    self.assertEqual(job.result_path, "")
                request = json.loads((job.directory / "request.json").read_text(encoding="utf-8"))
                self.assertEqual(request["seed"], 42)


if __name__ == "__main__":
    unittest.main()
