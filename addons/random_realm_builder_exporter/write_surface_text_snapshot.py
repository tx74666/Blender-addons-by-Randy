"""Convert a compact Blender library into a standalone Surface Text source file."""

import os
import sys

import bpy


def _argument(name):
    arguments = list(sys.argv)
    if "--" not in arguments:
        raise RuntimeError("Surface Text snapshot arguments must follow '--'.")
    separator = arguments.index("--")
    try:
        index = arguments.index(name, separator + 1)
    except ValueError as exception:
        raise RuntimeError(f"Surface Text snapshot argument is missing: {name}") from exception
    value_index = index + 1
    if value_index >= len(arguments) or not arguments[value_index]:
        raise RuntimeError(f"Surface Text snapshot argument is empty: {name}")
    return os.path.abspath(arguments[value_index])


def main():
    library_path = _argument("--library")
    output_path = _argument("--output")
    if not os.path.isfile(library_path):
        raise RuntimeError(f"Surface Text snapshot library was not found: {library_path}")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    with bpy.data.libraries.load(library_path, link=False) as (data_from, data_to):
        data_to.objects = list(data_from.objects)

    for obj in data_to.objects:
        if obj is not None and obj.name not in scene.collection.objects:
            scene.collection.objects.link(obj)

    if not bpy.data.objects:
        raise RuntimeError("Surface Text snapshot library contained no objects.")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=output_path, check_existing=False)
    if not os.path.isfile(output_path) or os.path.getsize(output_path) <= 0:
        raise RuntimeError("Surface Text snapshot output was not written.")


if __name__ == "__main__":
    main()
