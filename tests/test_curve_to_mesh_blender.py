"""Curve Tools conversion keeps the authored Curve and restores failed selection."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import curve_tools


def matrix_values(matrix):
    return tuple(value for row in matrix for value in row)


def curve_state(obj):
    keys = obj.data.shape_keys
    return (
        obj.as_pointer(), obj.data.as_pointer(), matrix_values(obj.matrix_world),
        tuple(tuple(point.co) for spline in obj.data.splines for point in spline.points),
        tuple((key.name, key.value, tuple(tuple(point.co) for point in key.data))
              for key in keys.key_blocks) if keys else None,
        tuple((modifier.name, modifier.type) for modifier in obj.modifiers),
        tuple(sorted((name, str(value)) for name, value in obj.items())),
        tuple(sorted((name, str(value)) for name, value in obj.data.items())),
        tuple((slot.link, slot.material.as_pointer() if slot.material else None)
              for slot in obj.material_slots),
    )


class CurveMeshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for klass in curve_tools.CLASSES:
            if not klass.is_registered:
                bpy.utils.register_class(klass)

    def setUp(self):
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in tuple(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        data = bpy.data.curves.new('AuthoredCurve', 'CURVE')
        data.dimensions = '3D'
        data.resolution_u = 1
        data.bevel_depth = .12
        data.bevel_resolution = 2
        spline = data.splines.new('POLY')
        spline.points.add(2)
        for point, co in zip(spline.points, ((0, 0, 0, 1), (0, .5, 1, 1), (.2, 1, 2, 1))):
            point.co = co
        self.source = bpy.data.objects.new('CurveSource', data)
        self.collection = bpy.data.collections.new('CurveSourceCollection')
        bpy.context.scene.collection.children.link(self.collection)
        self.collection.objects.link(self.source)
        self.source.matrix_world = (
            Matrix.Translation((2, -4, 1)) @ Matrix.Rotation(.4, 4, 'Z')
            @ Matrix.Diagonal((1.2, .8, 1.1, 1.0))
        )
        self.source['character_designer_generator'] = 'character_designer_centerline_v1'
        self.source['character_designer_profile'] = 'ROUND_HALF_WIDTH_AND_FRONT'
        data['character_designer_profile'] = 'test_source_metadata'
        self.source.select_set(True)
        bpy.context.view_layer.objects.active = self.source
        # Flush Blender's matrix decomposition before taking exact source
        # snapshots; evaluating this newly assigned transform normalizes it.
        bpy.context.view_layer.update()

    def test_mode_defaults_keep_old_hair_objects_and_route(self):
        self.assertEqual(curve_tools.object_mode(self.source), 'HAIR')
        self.source[curve_tools.MODE_KEY] = 'GENERAL'
        self.assertEqual(curve_tools.object_mode(self.source), 'GENERAL')
        self.source[curve_tools.MODE_KEY] = [1, 2]
        self.assertEqual(curve_tools.object_mode(self.source), 'HAIR')
        self.assertEqual(curve_tools.settings_mode(None), 'GENERAL')
        self.assertEqual(curve_tools.settings_mode(SimpleNamespace(ui_page='HAIR', curve_tools_mode='GENERAL')), 'HAIR')
        self.assertEqual(curve_tools.settings_mode(SimpleNamespace(ui_page='MODELING', curve_tools_mode='HAIR')), 'HAIR')

    def test_copy_bakes_evaluated_shape_preserves_source_keys_and_material_override(self):
        original_material = bpy.data.materials.new('CurveDataMaterial')
        override_material = bpy.data.materials.new('CurveObjectMaterial')
        self.source.data.materials.append(original_material)
        self.source.material_slots[0].link = 'OBJECT'
        self.source.material_slots[0].material = override_material
        self.source.shape_key_add(name='Basis')
        key = self.source.shape_key_add(name='Bent')
        key.data[1].co.x += .4
        key.value = .65
        self.source.modifiers.new('MirrorResult', 'MIRROR')
        bpy.context.view_layer.update()
        before = curve_state(self.source)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = self.source.evaluated_get(depsgraph)
        expected = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        try:
            vertices = tuple(tuple(vertex.co) for vertex in expected.vertices)
            polygons = tuple(tuple(polygon.vertices) for polygon in expected.polygons)
        finally:
            evaluated.to_mesh_clear()
        result = bpy.ops.character_designer.curve_to_mesh_copy()
        self.assertEqual(result, {'FINISHED'})
        output = bpy.context.active_object
        self.assertEqual(output.type, 'MESH')
        self.assertEqual(curve_state(self.source), before)
        self.assertEqual(matrix_values(output.matrix_world), before[2])
        self.assertEqual(tuple(tuple(vertex.co) for vertex in output.data.vertices), vertices)
        self.assertEqual(tuple(tuple(polygon.vertices) for polygon in output.data.polygons), polygons)
        self.assertEqual(tuple(output.users_collection), (self.collection,))
        self.assertIs(output.data.materials[0], override_material)
        self.assertEqual(tuple(bpy.context.selected_objects), (output,))
        self.assertFalse(output.modifiers)
        self.assertIsNone(output.data.shape_keys)
        self.assertFalse(any(key.startswith('character_designer_') for key in output.keys()))
        self.assertFalse(any(key.startswith('character_designer_') for key in output.data.keys()))

    def test_wire_curve_converts_to_edges_without_forced_profile(self):
        self.source.data.bevel_depth = 0
        self.source.data.extrude = 0
        output = curve_tools.mesh_copy(bpy.context)
        self.assertEqual(len(output.data.vertices), 3)
        self.assertEqual(len(output.data.edges), 2)
        self.assertEqual(len(output.data.polygons), 0)

    def test_empty_material_slot_survives(self):
        material = bpy.data.materials.new('EmptyAfterClear')
        self.source.data.materials.append(material)
        self.source.data.materials[0] = None
        output = curve_tools.mesh_copy(bpy.context)
        self.assertEqual(len(output.material_slots), 1)
        self.assertIsNone(output.material_slots[0].material)

    def test_failure_after_selection_rolls_back_new_ids_and_exact_selection(self):
        companion = bpy.data.objects.new('AlsoSelected', None)
        self.collection.objects.link(companion)
        companion.select_set(True)
        before = curve_state(self.source)
        objects = frozenset(obj.as_pointer() for obj in bpy.data.objects)
        meshes = frozenset(mesh.as_pointer() for mesh in bpy.data.meshes)
        selection = frozenset(bpy.context.selected_objects)
        real_select = curve_tools._select_output

        def fail_after_selection(context, output):
            real_select(context, output)
            raise RuntimeError('Injected failure after output selection')

        with patch.object(curve_tools, '_select_output', fail_after_selection):
            with self.assertRaisesRegex(RuntimeError, 'Injected failure'):
                curve_tools.mesh_copy(bpy.context)
        self.assertEqual(frozenset(obj.as_pointer() for obj in bpy.data.objects), objects)
        self.assertEqual(frozenset(mesh.as_pointer() for mesh in bpy.data.meshes), meshes)
        self.assertEqual(frozenset(bpy.context.selected_objects), selection)
        self.assertIs(bpy.context.active_object, self.source)
        self.assertEqual(curve_state(self.source), before)

    def test_operator_rejects_curve_edit_mode(self):
        bpy.ops.object.mode_set(mode='EDIT')
        self.assertFalse(curve_tools.CHARACTERDESIGNER_OT_curve_to_mesh_copy.poll(bpy.context))
        bpy.ops.object.mode_set(mode='OBJECT')


if __name__ == '__main__':
    result = unittest.main(argv=[__file__], exit=False).result
    if not result.wasSuccessful():
        raise RuntimeError('Curve Tools conversion tests failed')
