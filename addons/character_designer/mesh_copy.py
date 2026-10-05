"""Dispose of Keys belonging to a proved independent native Mesh copy."""
import bpy


def clear_copied_shape_keys(source, duplicate):
    """Clear only the duplicate's Key, including Blender's ownerless remainder.

    Blender 5.1 Mesh.copy() can retain an extra Key user count after
    shape_key_clear(). Native ID references, rather than that count, prove
    whether the exact copied Key can be removed. Never search for orphans.
    """
    data = duplicate.data
    key = data.shape_keys
    if key is None:
        return
    original = source.data.shape_keys
    if (source == duplicate or source.data == data or data.users != 1
            or original is None or original == key
            or data.library or data.override_library or key.library or key.override_library):
        raise ValueError('Shape Key cleanup requires an independent local Mesh copy.')
    name, pointer = key.name, key.as_pointer()
    if bpy.data.shape_keys.get(name) != key or bpy.data.user_map(subset={key}).get(key, set()) != {data}:
        raise ValueError('The copied Shape Key has unexpected native users; preserve it.')
    duplicate.shape_key_clear()
    if data.shape_keys is not None or source.data.shape_keys != original:
        raise ValueError('Native copied Shape Key clearing changed its source or binding.')
    remaining = bpy.data.shape_keys.get(name)
    if remaining is None:
        return
    if remaining.as_pointer() != pointer or bpy.data.user_map(subset={remaining}).get(remaining, set()):
        raise ValueError('The exact cleared copied Shape Key acquired an outside native user.')
    bpy.data.batch_remove(ids=(remaining,))
    if bpy.data.shape_keys.get(name) is not None:
        raise ValueError('Blender did not remove the exact cleared copied Shape Key.')
