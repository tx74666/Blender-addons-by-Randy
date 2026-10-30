"""Native reference proof and no-refresh lifecycle derivative of pinned tiny10."""
import json
from pathlib import Path
import runpy
from unittest.mock import patch

import bpy


state = runpy.run_path(str(Path(__file__).with_name('test_forearm_twist_angle_preview_tiny_blender.py')))
preview, runtime, session = (state[name] for name in ('preview', 'runtime', 'session'))
mesh, arm = session['mesh'], session['armature']
runtime._SESSION = session
mesh[runtime.PREVIEW_KEY] = json.dumps({'target': 'hand'})
assert mesh.data.users == 1
preview.set_angle(bpy.context, runtime, session, .45)
helper = mesh[preview.SOURCE_REF]
assert mesh.data.users == 2
assert helper[preview.MESH_DATA] == mesh.data
assert set(bpy.data.user_map(subset={mesh.data})[mesh.data]) == {mesh, helper}
assert preview.owned_mesh_data_reference(runtime, mesh)
mesh.data.use_fake_user = True
assert not preview.owned_mesh_data_reference(runtime, mesh)
mesh.data.use_fake_user = False
shared = bpy.data.objects.new('A real second Mesh Object', mesh.data)
bpy.context.scene.collection.objects.link(shared)
assert mesh.data.users == 3
assert not preview.owned_mesh_data_reference(runtime, mesh)
bpy.data.objects.remove(shared, do_unlink=True)
assert preview.owned_mesh_data_reference(runtime, mesh)
saved = session['angle_preview']
runtime._SESSION = None
assert not preview.owned_mesh_data_reference(runtime, mesh)
runtime._SESSION = session
original_clear = preview._clear_receipt


def clear_without_graph(*args):
    class NoGraph:
        def update(self):
            raise AssertionError('release must not request a dependency-graph update')
    from types import SimpleNamespace
    return original_clear(SimpleNamespace(view_layer=NoGraph()), *args[1:])


with patch.object(preview, '_clear_receipt', side_effect=clear_without_graph):
    assert preview.release(bpy.context, runtime, session)
assert mesh.data.users == 1
assert not preview.owned_mesh_data_reference(runtime, mesh)
data = bpy.data.meshes.new('Deleted ordinary source data')
deleted = bpy.data.objects.new('Deleted ordinary source', data)
bpy.context.scene.collection.objects.link(deleted)
ordinary = {'mesh': deleted, 'armature': arm}
bpy.data.objects.remove(deleted, do_unlink=True)
assert preview.release(bpy.context, runtime, ordinary) is False
print('FOREARM_ANGLE_REFERENCE_PASS', flush=True)
