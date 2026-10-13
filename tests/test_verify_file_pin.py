"""Regression checks for external receipt paths and strict file identity."""

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from verify_file_pin import main, verify_file_pin


class FilePinTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.file = self.folder / "source.bin"
        self.payload = b"original evidence"
        self.file.write_bytes(self.payload)
        self.pin = {"path": str(self.file),
                    "sha256": hashlib.sha256(self.payload).hexdigest(),
                    "bytes": len(self.payload)}

    def test_path_spelling_normalizes_without_changing_file(self):
        alias = str(self.folder) + os.sep + "." + os.sep + self.file.name
        if os.name == "nt":
            alias = alias.replace("\\", "/")
        actual = verify_file_pin({**self.pin, "path": alias})
        self.assertEqual(actual, {**self.pin, "path": str(self.file.resolve())})
        self.assertEqual(self.file.read_bytes(), self.payload)

    def test_sha_letter_case_is_canonical(self):
        self.assertEqual(verify_file_pin({**self.pin, "sha256": self.pin["sha256"].upper()}), self.pin)

    def test_same_size_file_change_is_rejected(self):
        self.file.write_bytes(b"x" * len(self.payload))
        with self.assertRaisesRegex(ValueError, "SHA-256 changed"):
            verify_file_pin(self.pin)

    def test_wrong_byte_count_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "byte count changed"):
            verify_file_pin({**self.pin, "bytes": len(self.payload) + 1})

    def test_boolean_byte_count_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "nonnegative integer"):
            verify_file_pin({**self.pin, "bytes": True})

    def test_unknown_pin_field_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly"):
            verify_file_pin({**self.pin, "allow_changed": True})

    def test_relative_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            verify_file_pin({**self.pin, "path": self.file.name})

    def test_cli_reads_nested_bom_receipt(self):
        receipt = self.folder / "receipt.json"
        receipt.write_text(json.dumps({"inputs": {"writer_report": self.pin}}), encoding="utf-8-sig")
        output = io.StringIO()
        with redirect_stdout(output):
            result = main([str(receipt), "--field", "inputs.writer_report"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), {"verified": True, "pin": self.pin})


if __name__ == "__main__":
    unittest.main()
