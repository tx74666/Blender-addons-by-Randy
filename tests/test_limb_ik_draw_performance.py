"""Instrument the production UI/draw functions without Blender or a GPU."""

import ast
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/limb_ik.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
FUNCTIONS = {
    '_active_control_visual', '_has_active_limb_target', '_active_limb_target',
    '_theme_rgba', '_pole_guide_line_vertices', '_pole_guide_line_batches',
    '_clear_pole_guide_cache', '_pole_guide_gpu_batches', '_draw_direct_pole_guides',
    'register_limb_ik_viewport_handler', 'unregister_limb_ik_viewport_handler',
}
CLASSES = {
    'CHARACTERDESIGNER_OT_limb_ik_reset_target_rotation': {'poll', 'execute'},
    'CHARACTERDESIGNER_PT_limb_ik_target_rotation': {'poll', 'draw'},
}


class Vector(tuple):
    def __sub__(self, other):
        return Vector(a - b for a, b in zip(self, other))

    @property
    def length(self):
        return math.sqrt(sum(value * value for value in self))


class Item(dict):
    def __init__(self, attributes=None, **properties):
        super().__init__(properties)
        self.__dict__.update(attributes or {})


def production_functions():
    # Execute the actual functions, not a second implementation. Exclude addon
    # registration and Blender-only annotations so this check stays lightweight.
    nodes = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS]
    for node in TREE.body:
        if isinstance(node, ast.ClassDef) and node.name in CLASSES:
            nodes.append(ast.ClassDef(name=node.name, bases=[], keywords=[], decorator_list=[],
                                      body=[method for method in node.body
                                            if isinstance(method, ast.FunctionDef)
                                            and method.name in CLASSES[node.name]]))
    scope = {
        'math': math, 'Vector': Vector, 'EPSILON': 1e-8, 'LimbIKError': ValueError,
        'ARMATURE_ID_KEY': 'rig_id', 'ROLE_KEY': 'role',
        'CONTROL_VISUAL_ROLES': {'MASTER', 'HAND_IK', 'FOOT_IK', 'POLE', 'HEEL_ROLL'},
        '_POLE_GUIDE_SHADER': None, '_POLE_GUIDE_BATCH_CACHE': None,
        '_POLE_GUIDE_DRAW_HANDLE': None, 'POLE_GUIDE_HANDLER_KEY': 'guide_handler',
        '_settings': lambda _context: None, '_set_status': Mock(),
        'rig_page_active': lambda context, _page: context.body_page,
        '_owned': lambda item, rig_id, role: item.get('rig_id') == rig_id and item.get('role') == role,
        '_validate_inventory': Mock(side_effect=ValueError('Damaged inventory')),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(SOURCE), 'exec'), scope)
    return scope


class PollTests(unittest.TestCase):
    def setUp(self):
        self.scope = production_functions()
        self.target = SimpleNamespace(select=True, name='Hand',
            bone=Item(rig_id='rig', role='HAND_IK'),
            custom_shape=Item(rig_id='rig', role='WIDGET'))
        self.context = SimpleNamespace(mode='POSE', body_page=True,
            object=SimpleNamespace(type='ARMATURE', data={'rig_id': 'rig'}),
            active_pose_bone=self.target)
        self.panel = self.scope['CHARACTERDESIGNER_PT_limb_ik_target_rotation']
        self.operator = self.scope['CHARACTERDESIGNER_OT_limb_ik_reset_target_rotation']

    def test_repeated_polls_do_not_validate_but_draw_and_execute_still_do(self):
        for _ in range(20):
            self.assertTrue(self.panel.poll(self.context))
            self.assertTrue(self.operator.poll(self.context))
        audit = self.scope['_validate_inventory']
        audit.assert_not_called()
        # A corrupt rig can expose the control but cannot draw its editable
        # fields or pass Reset's strict preflight.
        self.panel().draw(self.context)
        self.assertEqual(audit.call_count, 1)
        operator = self.operator()
        operator.report = Mock()
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.assertEqual(audit.call_count, 2)
        self.assertFalse(hasattr(self.target, 'rotation_euler'))

    def test_polls_follow_current_selection_role_and_ownership(self):
        cases = [
            ('HAND_IK', True, 'rig', True), ('FOOT_IK', True, 'rig', True),
            ('POLE', True, 'rig', False), ('HAND_IK', False, 'rig', False),
            ('HAND_IK', True, 'foreign', False),
        ]
        for role, selected, owner, expected in cases:
            self.target.bone.update(role=role, rig_id=owner)
            self.target.select = selected
            self.assertEqual(self.panel.poll(self.context), expected)
            self.assertEqual(self.operator.poll(self.context), expected)
        self.context.active_pose_bone = None
        self.assertFalse(self.panel.poll(self.context))
        self.context.active_pose_bone = self.target
        self.target.bone.update(role='HAND_IK', rig_id='rig')
        self.context.body_page = False
        self.assertFalse(self.panel.poll(self.context))
        self.context.mode = 'OBJECT'
        self.assertFalse(self.operator.poll(self.context))
        self.scope['_validate_inventory'].assert_not_called()


class GuideDrawTests(unittest.TestCase):
    def setUp(self):
        self.scope = production_functions()
        self.content = [([0., 0., 0.], [0., 1., 0.], [1., 0., 0., 1.])]
        self.query = Mock(side_effect=lambda _context: self.content)
        self.scope['_direct_pole_guide_segments'] = self.query
        self.shader = SimpleNamespace(bind=Mock(), uniform_float=Mock())
        self.factory = Mock(side_effect=lambda *_args: SimpleNamespace(draw=Mock()))
        self.state = SimpleNamespace()
        for name, value in [('depth_test', 'LESS'), ('depth_mask', True), ('blend', 'NONE'), ('line_width', 1.0)]:
            setattr(self.state, name + '_get', Mock(return_value=value))
            setattr(self.state, name + '_set', Mock())
        self.gpu = SimpleNamespace(state=self.state,
            shader=SimpleNamespace(from_builtin=Mock(return_value=self.shader)))
        self.context = SimpleNamespace(area=SimpleNamespace(type='VIEW_3D'),
            space_data=SimpleNamespace(overlay=SimpleNamespace(show_overlays=True, show_bones=True)))
        self.bpy = SimpleNamespace(context=self.context,
            app=SimpleNamespace(background=False, driver_namespace={}),
            types=SimpleNamespace(SpaceView3D=SimpleNamespace(
                draw_handler_add=Mock(return_value=object()), draw_handler_remove=Mock())))
        self.scope['bpy'] = self.bpy
        modules = patch.dict(sys.modules, {'gpu': self.gpu,
            'gpu_extras': SimpleNamespace(), 'gpu_extras.batch': SimpleNamespace(batch_for_shader=self.factory)})
        modules.start()
        self.addCleanup(modules.stop)

    def draw(self):
        self.scope['_draw_direct_pole_guides']()

    def test_equal_content_reuses_batches_but_queries_current_segments_each_time(self):
        self.draw()
        first = self.scope['_POLE_GUIDE_BATCH_CACHE']
        self.draw()
        self.assertIs(first, self.scope['_POLE_GUIDE_BATCH_CACHE'])
        self.assertEqual(self.query.call_count, 2)
        self.assertEqual(self.factory.call_count, 1)
        self.assertEqual(first[2][0][1].draw.call_count, 2)

    def test_pose_color_visibility_and_numeric_snapshot_stay_fresh(self):
        self.draw()
        old = self.scope['_POLE_GUIDE_BATCH_CACHE']
        self.content[0][1][0] = 2.0
        self.assertEqual(old[1][0][1][1], (0., 1., 0.))
        self.draw()
        self.assertEqual(self.factory.call_count, 2)
        self.content[0][2][:3] = [.2, .7, .9]
        self.draw()
        self.assertEqual(self.factory.call_count, 3)
        self.shader.uniform_float.assert_called_with('color', (.2, .7, .9, 1.))
        self.content = []
        self.draw()
        self.assertIsNone(self.scope['_POLE_GUIDE_BATCH_CACHE'])
        self.assertIsNone(self.scope['_POLE_GUIDE_SHADER'])

    def test_stopping_overlays_or_bones_drops_the_last_batches(self):
        for setting in ('show_overlays', 'show_bones'):
            self.draw()
            self.assertIsNotNone(self.scope['_POLE_GUIDE_BATCH_CACHE'])
            setattr(self.context.space_data.overlay, setting, False)
            self.draw()
            self.assertIsNone(self.scope['_POLE_GUIDE_BATCH_CACHE'])
            setattr(self.context.space_data.overlay, setting, True)

    def test_shader_change_and_new_content_replace_the_single_entry(self):
        batches = (((1., 0., 0., 1.), ((0., 0., 0.), (0., 1., 0.))),)
        build = self.scope['_pole_guide_gpu_batches']
        first = build(self.shader, batches, self.factory)
        new_shader = object()
        second = build(new_shader, batches, self.factory)
        self.assertIsNot(first, second)
        self.assertIs(self.scope['_POLE_GUIDE_BATCH_CACHE'][0], new_shader)
        self.assertEqual(self.factory.call_count, 2)
        build(new_shader, (), self.factory)
        self.assertIsNone(self.scope['_POLE_GUIDE_BATCH_CACHE'])

    def test_failed_construction_or_draw_clears_cache_and_restores_gpu_state(self):
        self.draw()
        self.content[0][1][0] = 2.
        self.factory.side_effect = RuntimeError('GPU creation failed')
        self.draw()
        self.assertIsNone(self.scope['_POLE_GUIDE_BATCH_CACHE'])
        self.assertIsNone(self.scope['_POLE_GUIDE_SHADER'])
        self.factory.side_effect = lambda *_args: SimpleNamespace(draw=Mock(side_effect=RuntimeError('GPU draw failed')))
        self.draw()
        self.assertIsNone(self.scope['_POLE_GUIDE_BATCH_CACHE'])
        self.state.depth_test_set.assert_called_with('LESS')
        self.state.depth_mask_set.assert_called_with(True)
        self.state.blend_set.assert_called_with('NONE')
        self.state.line_width_set.assert_called_with(1.0)

    def test_handler_lifecycle_clears_cached_resources(self):
        self.draw()
        self.scope['register_limb_ik_viewport_handler']()
        self.assertIsNone(self.scope['_POLE_GUIDE_BATCH_CACHE'])
        self.draw()
        self.scope['unregister_limb_ik_viewport_handler']()
        self.assertIsNone(self.scope['_POLE_GUIDE_BATCH_CACHE'])
        self.assertIsNone(self.scope['_POLE_GUIDE_SHADER'])
        self.assertEqual(self.bpy.app.driver_namespace, {})


if __name__ == '__main__':
    unittest.main()
