"""Standard FK wrist planning and atomic legacy migration without Blender."""
import ast
from copy import deepcopy
import importlib.util
import json
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import test_limb_fk_visuals as fixture_model
from test_limb_fk_visuals import ID, PoseBone, SOURCE


class Mesh(ID):
    def __init__(self, name, vertices, edges):
        super().__init__(name=name, users=1, library=None, use_fake_user=False,
                         animation_data=None, shape_keys=None)
        self.from_pydata(vertices, edges, [])

    def clear_geometry(self):
        self.vertices, self.edges, self.polygons = [], [], []

    def from_pydata(self, vertices, edges, polygons):
        self.vertices = [NS(co=tuple(point)) for point in vertices]
        self.edges = [NS(vertices=tuple(edge)) for edge in edges]
        self.polygons = list(polygons)

    def update(self):
        pass


class StandardWristTests(unittest.TestCase):
    def setUp(self):
        # Reuse the small RNA ID model and real display serialization, without
        # inheriting/rerunning its unrelated historical build expectations.
        model = fixture_model.LimbFKVisualTests(methodName='runTest')
        model.setUp()
        self.addCleanup(model.doCleanups)
        self.model, self.V, self.L = model, model.visuals, model.limb
        self.rig, self.inventory = model.rig, model.inventory
        self.rig.data.pose_position = 'POSE'
        package = sys.modules['_fk_visuals_test']
        package.limb_fk_visuals = self.V
        spec = importlib.util.spec_from_file_location(
            package.__name__ + '.limb_fk_wrist_visuals', SOURCE / 'limb_fk_wrist_visuals.py')
        self.W = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.W)
        package.limb_fk_wrist_visuals = self.W
        sys.modules[spec.name] = self.W
        self.addCleanup(lambda: sys.modules.pop(spec.name, None))
        helper_names = {'_widget_geometry', '_control_visual_state', '_parse_control_visual_state'}
        tree = ast.parse((SOURCE / 'limb_ik.py').read_text(encoding='utf-8-sig'))
        definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in helper_names]
        self.assertEqual({node.name for node in definitions}, helper_names)
        self.L.__dict__.update(json=json, CURRENT_SCHEMA=3, LEGACY_SCHEMA=1,
            CONTROL_VISUAL_DEFAULT_VERSION=1, OWNER_KEY='ik_owner', OWNER_VALUE='limb_ik',
            ROLE_KEY='ik_role', KIND_KEY='ik_kind', CONTROL_VISUAL_DEFAULT_KEY='default')
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE / 'limb_ik.py'), 'exec'), self.L.__dict__)
        self.L._owned = lambda item, armature_id, **keys: item is not None and item.get('owned') is True
        self.L._control_shape_style = lambda bone: bone.get('style', 'DEFAULT')
        self.sources = {}
        vertices, edges = self.W.geometry()
        for key, item in self.inventory['rigs'].items():
            if key[0] != 'ARM':
                continue
            end = self.rig.pose.bones[item['chain'][2]]
            target = PoseBone('IK_' + key[1], .2)
            target.bone.update(owned=True, style='DEFAULT', default=json.dumps({
                'version':1, 'scale':[.12] * 3, 'translation':[0., 0., 0.], 'rotation':[.1, .2, .3]}))
            target.use_custom_shape_bone_size = False
            target.custom_shape_scale_xyz = (.12,) * 3
            target.custom_shape_translation = (0., 0., 0.)
            target.custom_shape_rotation_euler = (.1, .2, .3)
            target.custom_shape_wire_width = 2.
            target.custom_shape_transform = end
            mesh = Mesh('IK_mesh_' + key[1], vertices, edges)
            dict.update(mesh, owned=True, ik_kind='HAND')
            shape = ID(name='IK_shape_' + key[1], type='MESH', data=mesh,
                       library=None, animation_data=None, modifiers=[], constraints=[])
            shape.update(owned=True, ik_kind='HAND')
            target.custom_shape = shape
            self.rig.pose.bones[target.name] = target
            item['target'], item['rig_id'] = target.bone, 'owned-' + key[1]
            target.bone.name = target.name
            self.sources[key] = target
        original_create = self.V._create_widget
        def create(*args):
            original_create(*args)
            _context, _rig, record, name, entry = args
            obj = model.objects[entry['object']]
            old_mesh = obj.data
            geom = self.W.geometry() if entry.get('shape_kind') == 'HAND' else self.V._geometry()
            mesh = Mesh(old_mesh.name, *geom)
            dict.update(mesh, old_mesh)
            obj.data = mesh
            model.meshes[mesh.name] = mesh
            obj.library, obj.animation_data, obj.modifiers, obj.constraints = None, None, [], []
            obj.users, obj.use_fake_user = 2, False
            obj.users_collection = (model.collections[record['collection']],)
        self.V._create_widget = create

    def circles(self):
        # Old153 registry is exactly the old circle generator/default vectors.
        old_plan = lambda rig, item: {
            'custom_shape':'', 'custom_shape_transform':'', 'use_bone_size':False,
            'scale':[.045] * 3, 'translation':[0.] * 3, 'rotation':[0.] * 3, 'wire_width':2.}
        with patch.object(self.W, 'plan', side_effect=old_plan):
            record = self.V.build(None, self.rig)
        for key in self.sources:
            name = self.inventory['rigs'][key]['chain'][2]
            entry = record['bindings'][name]
            entry.pop('shape_kind')
            self.W._replace_geometry(self.model.objects[entry['object']].data, *self.V._geometry())
        self.rig.data[self.V.RECORD_KEY] = json.dumps(record)
        return record

    def checkpoint(self):
        return (dict(self.rig.data), self.model.displays(),
                {name:self.W._mesh_state(mesh) for name, mesh in self.model.meshes.items()},
                {name:id(obj) for name,obj in self.model.objects.items()},
                {name:id(mesh) for name,mesh in self.model.meshes.items()})

    def test_new_wrists_have_same_3d_hand_geometry_and_source_frame_vectors(self):
        record = self.V.build(None, self.rig)
        for key, source in self.sources.items():
            name = self.inventory['rigs'][key]['chain'][2]
            entry = record['bindings'][name]
            self.assertEqual(entry['shape_kind'], 'HAND')
            self.assertTrue(self.W._same_geometry(self.model.objects[entry['object']].data, self.W.geometry()))
            pb = self.rig.pose.bones[name]
            self.assertIsNone(pb.custom_shape_transform)
            self.assertEqual(tuple(pb.custom_shape_scale_xyz), source.custom_shape_scale_xyz)
            self.assertEqual(tuple(pb.custom_shape_rotation_euler), source.custom_shape_rotation_euler)
        vertices, _edges = self.W.geometry()
        self.assertGreater(max(point[1] for point in vertices) - min(point[1] for point in vertices), .4)

    def test_two_circle_migration_preserves_ids_original_and_source_and_is_idempotent(self):
        old = self.circles()
        before = self.checkpoint()
        source_before = {key:deepcopy(self.L._control_visual_state(pb)) for key,pb in self.sources.items()}
        result = self.W.migrate(None, self.rig, inventory=self.inventory)
        self.assertEqual(result, {'migrated':2, 'retained':0, 'skipped':{}})
        final = self.V.get_record(self.rig)
        for name, entry in old['bindings'].items():
            self.assertEqual(final['bindings'][name]['original'], entry['original'])
            if name != entry['chain'][2]:
                self.assertEqual(final['bindings'][name], entry)
        after = self.checkpoint()
        self.assertEqual(after[3:], before[3:])
        self.assertEqual({key:self.L._control_visual_state(pb) for key,pb in self.sources.items()}, source_before)
        self.assertEqual(self.W.migrate(None, self.rig, inventory=self.inventory),
                         {'migrated':0, 'retained':2, 'skipped':{}})
        self.assertEqual(self.checkpoint(), after)

    def test_migration_preserves_vertex_edge_identity_and_custom_mesh_data(self):
        record = self.circles()
        name = self.inventory['rigs']['ARM', 'R']['chain'][2]
        mesh = self.model.objects[record['bindings'][name]['object']].data
        mesh.attributes = {'artist_attribute': [.25] * 32}
        mesh.materials = ['Artist material']
        vertices, edges = list(mesh.vertices), list(mesh.edges)
        for index, vertex in enumerate(vertices):
            vertex.select = index % 2 == 0
        for index, edge in enumerate(edges):
            edge.select = index % 3 == 0
            edge.vertices = tuple(reversed(edge.vertices))
        selected_vertices = [v.select for v in vertices]
        selected_edges = [edge.select for edge in edges]
        edge_order = [edge.vertices for edge in edges]
        self.W.migrate(None, self.rig, inventory=self.inventory)
        self.assertTrue(all(current is previous for current,previous in zip(mesh.vertices, vertices)))
        self.assertTrue(all(current is previous for current,previous in zip(mesh.edges, edges)))
        self.assertEqual([v.select for v in mesh.vertices], selected_vertices)
        self.assertEqual([edge.select for edge in mesh.edges], selected_edges)
        self.assertEqual([edge.vertices for edge in mesh.edges], edge_order)
        self.assertEqual(mesh.attributes, {'artist_attribute':[.25] * 32})
        self.assertEqual(mesh.materials, ['Artist material'])

    def test_artist_mesh_changes_refuse_before_any_write(self):
        record = self.circles()
        right = self.inventory['rigs']['ARM', 'R']['chain'][2]
        mesh = self.model.objects[record['bindings'][right]['object']].data
        mesh.vertices[0].co = (.91, 0., 0.)
        before = self.checkpoint()
        with self.assertRaisesRegex(ValueError, 'edited'):
            self.W.migrate(None, self.rig, inventory=self.inventory)
        self.assertEqual(self.checkpoint(), before)

    def test_artist_display_and_shared_mesh_refuse_before_any_write(self):
        record = self.circles()
        right = self.inventory['rigs']['ARM', 'R']['chain'][2]
        pb = self.rig.pose.bones[right]
        pb.custom_shape_translation = (.01, 0., 0.)
        before = self.checkpoint()
        with self.assertRaisesRegex(ValueError, 'edited'):
            self.W.migrate(None, self.rig, inventory=self.inventory)
        self.assertEqual(self.checkpoint(), before)
        pb.custom_shape_translation = (0., 0., 0.)
        mesh = self.model.objects[record['bindings'][right]['object']].data
        mesh.users = 2
        before = self.checkpoint()
        with self.assertRaisesRegex(ValueError, 'shared'):
            self.W.migrate(None, self.rig, inventory=self.inventory)
        self.assertEqual(self.checkpoint(), before)

    def test_artist_source_geometry_is_skipped_without_overwriting_it(self):
        self.circles()
        source = self.sources['ARM', 'R']
        source.custom_shape.data.vertices[0].co = (.91, 0., 0.)
        original = self.W._mesh_state(source.custom_shape.data)
        result = self.W.migrate(None, self.rig, inventory=self.inventory)
        self.assertEqual(result['migrated'], 1)
        self.assertIn(self.inventory['rigs']['ARM', 'R']['chain'][2], result['skipped'])
        self.assertEqual(self.W._mesh_state(source.custom_shape.data), original)

    def test_nonstandard_source_skips_only_its_wrist_without_source_writes(self):
        record = self.circles()
        source = self.sources['ARM', 'R']
        source.custom_shape_scale_xyz = (.14,) * 3
        source_state = self.L._pose_shape_json_state(source)
        right = self.inventory['rigs']['ARM', 'R']['chain'][2]
        original_right = deepcopy(record['bindings'][right])
        result = self.W.migrate(None, self.rig, inventory=self.inventory)
        self.assertEqual(result['migrated'], 1)
        self.assertIn(right, result['skipped'])
        self.assertEqual(self.V.get_record(self.rig)['bindings'][right], original_right)
        self.assertEqual(self.L._pose_shape_json_state(source), source_state)

    def test_mutation_failure_after_first_mesh_restores_entire_transaction(self):
        self.circles()
        before = self.checkpoint()
        replace = self.W._replace_geometry
        def fail(*args):
            replace(*args)
            raise RuntimeError('Injected native mesh failure')
        with patch.object(self.W, '_replace_geometry', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Injected'):
                self.W.migrate(None, self.rig, inventory=self.inventory)
        self.assertEqual(self.checkpoint(), before)


if __name__ == '__main__':
    unittest.main()
