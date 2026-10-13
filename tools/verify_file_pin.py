"""Verify an absolute path/SHA-256/bytes pin without Blender or scene writes.

Return a canonical pin so Windows slash differences do not cause a false
mismatch. This checks file identity; receipt provenance stays with the caller.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re


def verify_file_pin(row):
    if type(row) is not dict or set(row) != {"path", "sha256", "bytes"}:
        raise ValueError("A file pin must contain exactly path, sha256 and bytes.")
    if not isinstance(row["path"], str) or not row["path"]:
        raise ValueError("The file path must be a nonempty string.")
    if not isinstance(row["sha256"], str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", row["sha256"]
    ):
        raise ValueError("The expected SHA-256 must contain 64 hexadecimal digits.")
    if type(row["bytes"]) is not int or row["bytes"] < 0:
        raise ValueError("The expected byte count must be a nonnegative integer.")
    path = Path(row["path"])
    if not path.is_absolute():
        raise ValueError("File pins require absolute paths.")
    path = path.resolve(strict=True)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    if size != row["bytes"]:
        raise ValueError(f"File byte count changed: {path}")
    actual_sha = digest.hexdigest()
    if actual_sha != row["sha256"].lower():
        raise ValueError(f"File SHA-256 changed: {path}")
    return {"path": str(path), "sha256": actual_sha, "bytes": size}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--field", help="Optional dotted dictionary field containing the pin.")
    args = parser.parse_args(argv)
    try:
        row = json.loads(args.receipt.read_text(encoding="utf-8-sig"))
        if args.field:
            for key in args.field.split("."):
                row = row[key]
        canonical = verify_file_pin(row)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"verified": False, "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({"verified": True, "pin": canonical}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
