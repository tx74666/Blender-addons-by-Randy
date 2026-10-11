"""Read saved Blender 5.x snapshot ID links as bytes; never load Blender or an add-on.

This diagnostic accepts explicit immutable input hashes and an existing libzstd.
It does not repair, evaluate, write, or delete any input. The only optional write
is an exclusive JSON report. Supported file format: little-endian LargeBHead8.
"""
from __future__ import annotations

import argparse
import collections
import ctypes
import hashlib
import json
import re
import struct
from pathlib import Path


def pin(path, expected):
    path = Path(path).resolve(strict=True)
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError(f"Input hash changed: {path}")
    return path, data, {"path": str(path), "sha256": actual, "bytes": len(data)}


def decode_blocks(data, dll_path):
    class Input(ctypes.Structure):
        _fields_ = [("src", ctypes.c_void_p), ("size", ctypes.c_size_t), ("pos", ctypes.c_size_t)]

    class Output(ctypes.Structure):
        _fields_ = [("dst", ctypes.c_void_p), ("size", ctypes.c_size_t), ("pos", ctypes.c_size_t)]

    zstd = ctypes.CDLL(str(dll_path))
    zstd.ZSTD_createDStream.restype = ctypes.c_void_p
    for name in ("ZSTD_initDStream", "ZSTD_freeDStream"):
        getattr(zstd, name).argtypes = [ctypes.c_void_p]
    zstd.ZSTD_decompressStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(Output), ctypes.POINTER(Input)]
    zstd.ZSTD_decompressStream.restype = ctypes.c_size_t
    zstd.ZSTD_isError.argtypes = [ctypes.c_size_t]
    zstd.ZSTD_isError.restype = ctypes.c_uint
    raw = ctypes.create_string_buffer(data)
    source = Input(ctypes.addressof(raw), len(data), 0)
    target = ctypes.create_string_buffer(1024 * 1024)
    stream = zstd.ZSTD_createDStream()
    zstd.ZSTD_initDStream(stream)
    buffer = bytearray()
    blocks = {}
    direct = {}
    owner = None
    header = None
    dna = None
    total = retained = 0
    done = False
    try:
        while source.pos < source.size and not done:
            out = Output(ctypes.addressof(target), len(target), 0)
            status = zstd.ZSTD_decompressStream(stream, ctypes.byref(out), ctypes.byref(source))
            if zstd.ZSTD_isError(status):
                raise ValueError("Zstd decoder rejected the immutable snapshot")
            buffer.extend(target.raw[:out.pos])
            total += out.pos
            if total > 512 * 1024 * 1024:
                raise ValueError("Snapshot exceeds bounded decompression size")
            if header is None and len(buffer) >= 17:
                header = bytes(buffer[:17])
                del buffer[:17]
                if header != b"BLENDER17-01v0502":
                    raise ValueError("Only the diagnosed Blender 5.2 LargeBHead8 format is admitted")
            while header is not None and len(buffer) >= 32:
                code, sdna, address, length, count = struct.unpack_from("<4siQqq", buffer)
                if not 0 <= length <= 512 * 1024 * 1024:
                    raise ValueError("Invalid block size")
                if len(buffer) < 32 + length:
                    break
                content = bytes(buffer[32:32 + length])
                del buffer[:32 + length]
                if code == b"DNA1":
                    dna = content
                elif code == b"ENDB":
                    done = True
                    break
                else:
                    # Large geometry/keyframe buffers cannot contain the small
                    # IDProperty records inspected here. Preserve their prefix
                    # and size without retaining entire unused arrays.
                    kept = content if length <= 65536 or code != b"DATA" else content[:256]
                    retained += len(kept)
                    if retained > 128 * 1024 * 1024:
                        raise ValueError("Snapshot exceeds bounded retained data size")
                    record = (code, sdna, length, count, kept)
                    if code == b"DATA":
                        # Direct-data address maps are local to their owning ID.
                        # Different IDs can serialize different blocks with the
                        # same old address (e.g. shared modifier settings).
                        key = (owner, address)
                        if key in direct and direct[key] != record:
                            raise ValueError("Conflicting direct-data address within one ID")
                        direct[key] = record
                    elif address:
                        owner = address
                        if address in blocks and blocks[address] != record:
                            raise ValueError("Conflicting ID address")
                        blocks[address] = record
    finally:
        zstd.ZSTD_freeDStream(stream)
    if not done or dna is None:
        raise ValueError("Snapshot is incomplete")
    return blocks, direct, dna, {"header": header.decode(), "uncompressed_bytes": total,
                                 "retained_bytes": retained, "ID_block_count": len(blocks),
                                 "direct_data_block_count": len(direct)}


def parse_sdna(dna):
    offset = 0

    def tag(expected):
        nonlocal offset
        if dna[offset:offset + 4] != expected:
            raise ValueError("SDNA section mismatch")
        offset += 4

    def u32():
        nonlocal offset
        value = struct.unpack_from("<I", dna, offset)[0]
        offset += 4
        return value

    def strings(count):
        nonlocal offset
        result = []
        for _ in range(count):
            end = dna.index(0, offset)
            result.append(dna[offset:end].decode("ascii"))
            offset = end + 1
        offset = (offset + 3) & ~3
        return result

    tag(b"SDNA")
    tag(b"NAME")
    names = strings(u32())
    tag(b"TYPE")
    types = strings(u32())
    tag(b"TLEN")
    sizes = struct.unpack_from("<" + "H" * len(types), dna, offset)
    offset += len(types) * 2
    offset = (offset + 3) & ~3
    tag(b"STRC")
    schemas = []
    for _ in range(u32()):
        type_index, count = struct.unpack_from("<HH", dna, offset)
        offset += 4
        fields, field_offset = [], 0
        for _ in range(count):
            field_type, field_name = struct.unpack_from("<HH", dna, offset)
            offset += 4
            name = names[field_name]
            length = 1
            for part in re.findall(r"\[(\d+)\]", name):
                length *= int(part)
            size = (8 if "*" in name else sizes[field_type]) * length
            fields.append((name, types[field_type], field_offset, size, length))
            field_offset += size
        if field_offset != sizes[type_index]:
            raise ValueError(f"Unsupported implicit padding in {types[type_index]}")
        schemas.append((types[type_index], fields, sizes[type_index]))
    return schemas


class Inventory:
    def __init__(self, blocks, direct, schemas):
        self.blocks, self.direct, self.schemas = blocks, direct, schemas
        self.types = {item[0]: item for item in schemas}
        self.ids = {}
        name_offset, name_size = self.field("ID", "name")[:2]
        for address, (code, sdna, _, _, data) in blocks.items():
            if code in (b"DATA", b"GLOB"):
                continue
            schema = schemas[sdna]
            if schema[0] != "ID" and not any(field[0] == "id" and field[1] == "ID" for field in schema[1]):
                continue
            name = data[name_offset:name_offset + name_size].split(b"\0", 1)[0].decode("utf8", "strict")
            self.ids[address] = name

    def field(self, schema_name, path):
        offset = 0
        for part in path.split("."):
            schema = self.types[schema_name]
            found = [f for f in schema[1] if f[0].lstrip("*").split("[", 1)[0] == part]
            if len(found) != 1:
                raise ValueError(f"Unknown or ambiguous SDNA field: {schema_name}.{part}")
            name, schema_name, local, size, count = found[0]
            offset += local
        return offset, size, schema_name, count

    def value(self, schema, data, path, kind="Q", base=0):
        offset = self.field(schema, path)[0] + base
        return struct.unpack_from("<" + kind, data, offset)[0]

    def id_name(self, address):
        return self.ids.get(address)

    def property_tree(self, owner, address):
        result, counts, top, records = [], collections.Counter(), [], []
        seen = set()
        prop_size = self.types["IDProperty"][2]

        def visit(block_address, index, path, depth):
            if depth > 64 or (block_address, index) in seen:
                raise ValueError("IDProperty cycle or excessive depth")
            seen.add((block_address, index))
            block = self.direct[(owner, block_address)]
            if self.schemas[block[1]][0] != "IDProperty":
                raise ValueError("IDProperty pointer has unexpected schema")
            data, base = block[4], prop_size * index
            if base + prop_size > len(data):
                raise ValueError("IDProperty array was truncated")
            name_offset, name_size = self.field("IDProperty", "name")[:2]
            name = data[base + name_offset:base + name_offset + name_size].split(b"\0", 1)[0].decode("utf8")
            kind = self.value("IDProperty", data, "type", "b", base)
            subtype = self.value("IDProperty", data, "subtype", "b", base)
            length = self.value("IDProperty", data, "len", "i", base)
            pointer = self.value("IDProperty", data, "data.pointer", base=base)
            current = path + (("." if path else "") + name if name else "")
            records.append({"path": current, "address": hex(block_address), "array_index": index, "type": kind})
            counts[str(kind)] += 1
            if depth == 1:
                top.append({"name": name, "type": kind, "subtype": subtype, "length": length})
            if kind == 7 and pointer:
                if pointer not in self.ids:
                    raise ValueError("IDProperty ID target is not a saved ID")
                result.append({"path": current, "property_address": hex(block_address), "array_index": index,
                               "target_address": hex(pointer), "target_id": self.ids[pointer]})
            elif kind == 6:
                child = self.value("IDProperty", data, "data.group.first", base=base)
                siblings = set()
                while child:
                    if child in siblings:
                        raise ValueError("IDProperty sibling cycle")
                    siblings.add(child)
                    visit(child, 0, current, depth + 1)
                    child = self.value("IDProperty", self.direct[(owner, child)][4], "next")
            elif kind == 9 and length:
                for i in range(length):
                    visit(pointer, i, current + f"[{i}]", depth + 1)
            elif kind == 5 and subtype == 6 and length:
                array = self.direct[(owner, pointer)][4]
                if len(array) < length * 8:
                    raise ValueError("Group pointer array was truncated")
                for i in range(length):
                    visit(struct.unpack_from("<Q", array, i * 8)[0], 0, current + f"[{i}]", depth + 1)

        if address:
            visit(address, 0, "", 0)
        return {"storage_address": hex(address), "node_count": sum(counts.values()), "types": dict(counts),
                "top_level": top, "native_ID_links": result, "property_records": records}


def inspect(snapshot_data, dll_path, job):
    blocks, direct, dna, summary = decode_blocks(snapshot_data, dll_path)
    inventory = Inventory(blocks, direct, parse_sdna(dna))
    object_names = {"OB" + job["rig"], *("OB" + name for name in job["composition_job"]["dependency_objects"])}
    action_names = {"AC" + job["action"]}
    roots = [address for address, name in inventory.ids.items() if name in object_names | action_names]
    if len(roots) != len(object_names | action_names):
        raise ValueError("Expected snapshot roots are missing or ambiguous")
    owned = set(roots)
    for address in roots:
        if inventory.ids[address].startswith("OB"):
            data_address = inventory.value("Object", blocks[address][4], "data")
            if data_address not in inventory.ids:
                raise ValueError("Expected root data is missing")
            owned.add(data_address)
    root_properties = []
    for address in sorted(owned, key=lambda a: inventory.ids[a]):
        _, sdna, _, _, data = blocks[address]
        schema = inventory.schemas[sdna][0]
        row = {"id": inventory.ids[address], "address": hex(address), "schema": schema}
        for storage in ("properties", "system_properties"):
            pointer = inventory.value("ID", data, storage)
            row[storage] = inventory.property_tree(address, pointer)
        root_properties.append(row)
    objects = [name for name in inventory.ids.values() if name.startswith("OB")]
    actions = [name for name in inventory.ids.values() if name.startswith("AC")]
    original_rigs = [address for address, name in inventory.ids.items() if name == "OBCoshaRig"]
    clone_rigs = [row for row in root_properties if row["id"] == "OB" + job["rig"]]
    if len(original_rigs) != 1 or len(clone_rigs) != 1:
        raise ValueError("Diagnosed original/clone rig identity is ambiguous")
    original_address = original_rigs[0]
    original_system = inventory.property_tree(original_address, inventory.value(
        "ID", blocks[original_address][4], "system_properties"))
    original_custom = inventory.property_tree(original_address, inventory.value(
        "ID", blocks[original_address][4], "properties"))
    clone_system = clone_rigs[0]["system_properties"]
    prefix = "system_properties.character_designer_unity_export"
    original_records = [row for row in original_system["property_records"]
                        if row["path"] == prefix or row["path"].startswith(prefix + ".")]
    clone_records = [row for row in clone_system["property_records"]
                     if row["path"] == prefix or row["path"].startswith(prefix + ".")]
    original_links = [row for row in original_system["native_ID_links"] if row["path"].startswith(prefix + ".")]
    clone_links = [row for row in clone_system["native_ID_links"] if row["path"].startswith(prefix + ".")]
    original_keys = {(row["address"], row["array_index"]) for row in original_records}
    clone_keys = {(row["address"], row["array_index"]) for row in clone_records}
    copy_proof = {"original_object": inventory.ids[original_address], "original_address": hex(original_address),
                  "copied_object": clone_rigs[0]["id"], "copied_address": clone_rigs[0]["address"],
                  "original_system_storage_address": original_system["storage_address"],
                  "copied_system_storage_address": clone_system["storage_address"],
                  "original_export_property_records": original_records, "copied_export_property_records": clone_records,
                  "all_export_records_disjoint": original_keys.isdisjoint(clone_keys),
                  "same_export_native_targets": [(r["path"], r["target_id"], r["target_address"]) for r in original_links]
                                                == [(r["path"], r["target_id"], r["target_address"]) for r in clone_links],
                  "original_export_native_ID_links": original_links, "copied_export_native_ID_links": clone_links}
    target_parents = []
    for link in clone_links:
        address = int(link["target_address"], 16)
        if link["target_id"].startswith("OB"):
            parent = inventory.value("Object", blocks[address][4], "parent")
            target_parents.append({"object": link["target_id"], "address": link["target_address"],
                                   "parent_address": hex(parent), "parent_id": inventory.id_name(parent)})
    return {**summary, "ID_count": len(inventory.ids), "object_count": len(objects), "Action_count": len(actions),
            "expected_object_names": sorted(object_names), "expected_Action_names": sorted(action_names),
            "owned_root_and_data_count": len(owned), "unowned_object_names": sorted(set(objects) - object_names),
            "unowned_Action_names": sorted(set(actions) - action_names), "owned_root_property_links": root_properties,
            "export_property_copy_proof": copy_proof, "export_target_object_parents": target_parents,
            "original_rig_custom_property_node_count": original_custom["node_count"],
            "original_rig_custom_native_ID_links": original_custom["native_ID_links"],
            "limitation": "Saved SDNA IDProperty storage only; no Blender evaluation or loaded-library expansion performed."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("snapshot", "job", "log", "result", "zstd-dll"):
        parser.add_argument("--" + key, required=True)
        parser.add_argument("--" + key + "-sha256", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    pins, content, paths = {}, {}, {}
    for key in ("snapshot", "job", "log", "result", "zstd_dll"):
        path, data, receipt = pin(getattr(args, key), getattr(args, key + "_sha256"))
        pins[key], content[key], paths[key] = receipt, data, path
    report = {"schema": "x.validation.character-composition-snapshot-id-links/1", "source_only": True,
              "native_execution_performed": False, "input_pins": pins,
              "snapshot_inventory": inspect(content["snapshot"], paths["zstd_dll"], json.loads(content["job"]))}
    target = Path(args.report).resolve()
    with target.open("x", encoding="utf8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    data = target.read_bytes()
    inventory = report["snapshot_inventory"]
    print(json.dumps({"report": str(target), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                      "objects": inventory["object_count"], "Actions": inventory["Action_count"],
                      "root_native_links": {row["id"]: sum(len(row[key]["native_ID_links"]) for key in
                                                            ("properties", "system_properties"))
                                            for row in inventory["owned_root_property_links"]}}, ensure_ascii=True))


if __name__ == "__main__":
    main()
