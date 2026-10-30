"""Execute source/control validation branches without starting Blender.

Geometry comparison itself remains covered by the persistent Rest-view tests
and native fixtures. These cases prove which Bone each real validator reads.
"""

import ast
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from test_validation_native_scope import load_scope


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class Bone(dict):
    def __init__(self, name, owner='', deform=False, connected=False, parent=None):
        super().__init__(owner=owner)
        self.name, self.use_deform, self.use_connect, self.parent = name, deform, connected, parent


class Bones(dict):
    def __iter__(self):
        return iter(self.values())


@lru_cache(maxsize=None)
def function_code(module, name, strip_import=False):
    tree = ast.parse((SOURCE / (module + '.py')).read_text(encoding='utf-8'))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    if strip_import:
        node.body = [statement for statement in node.body if not isinstance(statement, ast.ImportFrom)]
    return compile(ast.Module(body=[node], type_ignores=[]), module + '.py', 'exec')


def function(module, name, namespace, strip_import=False):
    exec(function_code(module, name, strip_import), namespace)
    return namespace[name]


def fixture(module):
    names = ['NativeHead', 'NativeTorso', 'NativeHips']
    control = Bone('Controller', owner='generated')
    sources = {name: Bone(name, deform=True, connected=True) for name in names}
    bones = Bones({control.name: control, **sources})
    protected = {name: Bone(name, deform=True, connected=True) for name in names}
    sources[names[0]].use_connect = False  # Sole authenticated motion-data difference.
    poses = Bones({name: NS(name=name, constraints=[], lock_scale=(True,) * 3) for name in bones.keys()})
    armature = NS(data=NS(bones=bones), pose=NS(bones=poses))
    state = {'connect': False, 'deform': False}
    source_states = {name: {'connect': True, 'deform': True} for name in names}
    record = {'bones': {'MASTER': control.name}, 'bone_states': {'MASTER': state},
              'source_states': source_states, 'sources': names, 'constraints': [],
              'widgets': {}, 'widget_collection': 'Widgets'}
    calls = []

    class View:
        def source_bone(self, live):
            if live.name not in sources or live is not sources[live.name]:
                raise ValueError('Projection attempted on a generated or foreign bone.')
            calls.append(live.name)
            return protected[live.name]

    namespace = {'validation_native': load_scope(), 'OWNER_KEY': 'owner', 'OWNER_VALUE': 'generated', 'bpy': NS(data=NS(
        collections=NS(get=lambda _name: object()), objects=NS(get=lambda _name: None))),
        '_error': ValueError, 'get_record': lambda _rig: record,
        '_owned': lambda item, _record, _role: item is not None,
        '_same_rest': lambda bone, saved: bone.use_connect == saved['connect']
            and (bone.parent.name if bone.parent else None) == saved.get('parent'),
        '_same_value': lambda left, right: left == right}
    adoption = NS(active=False, view=None, error=None)

    def control_view(_rig):
        if adoption.error:
            raise ValueError(adoption.error)
        return adoption.view

    target = NS(is_adopted=lambda _rig: adoption.active, control_rest_view=control_view)
    limb = NS(_validation_mute=lambda _pb, constraint, _view: constraint.mute)
    limb._validation_source_bone = function('limb_ik', '_validation_source_bone', {})
    limb._validation_source_rest = function('limb_ik', '_validation_source_rest', {
        'animation_worklist_target': target, 'LimbIKError': ValueError,
        'validation_native': namespace['validation_native']}, strip_import=True)
    namespace['_limb'] = lambda: limb
    if module == 'torso_controls':
        # Its only local import is the adjacent module's read-only constraint inventory.
        namespace['spine_ik_fk'] = NS(extra_constraints=lambda _rig: set())
        validate = function(module, 'validate', namespace, strip_import=True)
    elif module == 'spine_ik_fk':
        record.update(torso_id='torso identity', fk_controls={}, bend='Bend',
                      torso_bones=['TorsoHelper'], chest=control.name, drivers=[])
        record['bones'] = {'IK_0': control.name}
        record['bone_states'] = {'IK_0': state}
        namespace.update(torso=NS(get_record=lambda _rig: {
            'id': record['torso_id'], 'sources': names, 'controls': {}, 'bend': 'Bend',
            'bones': {'Helper': 'TorsoHelper'}}, _same_rest=namespace['_same_rest']),
            _SOLVER_FIELDS={}, _constraint_plan=lambda _record: [],
            mode_for_rig=lambda _rig: None, _find_driver=lambda *_args: None,
            property_path=lambda _pb: 'mode')
        validate = function(module, 'validate', namespace)
    elif module == 'root_control':
        record.update(master=control.name, control_states={'Input': dict(state)}, controls=[])
        widget = NS(type='MESH', data=NS(name='WidgetMesh'))
        record['widgets']['MASTER'] = {'object': 'Widget', 'mesh': 'WidgetMesh'}
        poses[control.name].custom_shape = widget
        namespace['bpy'].data.objects.get = lambda _name: widget
        original = record['control_states']['Input']
        original['parent'] = None  # validate adds the owned Master parent itself.
        bones['Input'] = Bone('Input', owner='limb controller', parent=control)
        poses['Input'] = NS(name='Input', constraints=[])
        namespace.update(_validate_scale_drivers=lambda *_args: None,
                         extra_constraints=lambda _rig: set())
        validate = function(module, 'validate', namespace)
    else:
        validate = function(module, 'validate', namespace)
    return NS(validate=validate, rig=armature, view=View(), calls=calls,
              control=control, protected=protected, sources=sources, record=record, adoption=adoption)


class SourceOnlyRestTests(unittest.TestCase):
    modules = ('eye_controls', 'torso_controls', 'spine_ik_fk', 'root_control')

    def test_strict_default_rejects_disconnected_source(self):
        for module in self.modules:
            case = fixture(module)
            with self.subTest(module=module), self.assertRaisesRegex(ValueError, 'source'):
                case.validate(case.rig)
            self.assertFalse(case.calls)

    def test_proven_view_projects_native_sources_and_preserves_controller_checks(self):
        for module in self.modules:
            case = fixture(module)
            with self.subTest(module=module):
                self.assertIs(case.validate(case.rig, source_rest=case.view), case.record)
                self.assertEqual(set(case.calls), set(case.sources))

    def test_generated_controller_edits_are_never_projected(self):
        for module in self.modules:
            case = fixture(module)
            case.control.use_connect = True
            with self.subTest(module=module), self.assertRaises(ValueError):
                case.validate(case.rig, source_rest=case.view)
            self.assertFalse(case.calls)
        case = fixture('root_control')
        case.rig.data.bones['Input'].use_connect = True
        with self.assertRaisesRegex(ValueError, 'Root input'):
            case.validate(case.rig, source_rest=case.view)

    def test_saved_source_rest_edit_still_rejects(self):
        for module in self.modules:
            case = fixture(module)
            case.protected['NativeHead'].use_connect = False
            with self.subTest(module=module), self.assertRaisesRegex(ValueError, 'source'):
                case.validate(case.rig, source_rest=case.view)

    def test_live_foreign_constraints_still_reject(self):
        for module in self.modules:
            case = fixture(module)
            case.rig.pose.bones['Controller'].constraints.append(NS(name='Artist foreign relation'))
            with self.subTest(module=module), self.assertRaises(ValueError):
                case.validate(case.rig, source_rest=case.view)

    def test_normal_adopted_controller_calls_resolve_the_authenticated_view(self):
        for module in self.modules:
            case = fixture(module)
            case.adoption.active, case.adoption.view = True, case.view
            with self.subTest(module=module):
                self.assertIs(case.validate(case.rig), case.record)
                self.assertEqual(set(case.calls), set(case.sources))

    def test_unadopted_rig_cannot_borrow_a_stored_view(self):
        for module in self.modules:
            case = fixture(module)
            case.adoption.view = case.view
            with self.subTest(module=module), self.assertRaisesRegex(ValueError, 'source'):
                case.validate(case.rig)
            self.assertFalse(case.calls)

    def test_foreign_or_changed_adoption_record_cannot_fall_back(self):
        for module in self.modules:
            case = fixture(module)
            case.adoption.active, case.adoption.error = True, 'Foreign session rig or data'
            with self.subTest(module=module), self.assertRaisesRegex(ValueError, 'Foreign session'):
                case.validate(case.rig)
            self.assertFalse(case.calls)

    def test_explicit_original_mute_preflight_keeps_its_strict_source_semantics(self):
        for module in self.modules:
            case = fixture(module)
            case.adoption.active, case.adoption.view = True, case.view
            with self.subTest(module=module), self.assertRaisesRegex(ValueError, 'source'):
                case.validate(case.rig, original_mutes=object())
            self.assertFalse(case.calls)


class InventoryAutoRestTests(unittest.TestCase):
    def setUp(self):
        self.connector = NS(is_adopted=Mock(return_value=False), control_rest_view=Mock(return_value=None))
        self.error = type('LimbIKError', (ValueError,), {})
        scope = load_scope()
        helper = function('limb_ik', '_validation_source_rest', {
            'animation_worklist_target': self.connector, 'LimbIKError': self.error,
            'validation_native': scope}, strip_import=True)
        self.records = Mock(return_value=[])
        namespace = {'validation_native': scope, '_validation_source_rest': helper, 'LimbIKError': self.error,
                     '_owned_constraint_records': self.records, 'OWNER_KEY': 'owner', 'OWNER_VALUE': 'generated'}
        for key in ('ARMATURE_ID_KEY', 'SCHEMA_KEY', 'DIRECT_REST_KEY', 'SOURCE_WIDGETS_KEY',
                    'TARGET_ROTATION_VERSION_KEY'):
            namespace[key] = key
        for name in ('foot_controls', 'torso_controls', 'eye_controls', 'spine_ik_fk', 'root_control'):
            namespace[name] = NS(RECORD_KEY=name)
        self.validate = function('limb_ik', '_validate_inventory', namespace)

        class Data(dict):
            bones = ()

        self.rig = NS(data=Data())

    def test_ordinary_inventory_keeps_strict_default_without_record_loading(self):
        self.validate(self.rig)
        self.connector.control_rest_view.assert_not_called()

    def test_adopted_inventory_authenticates_its_motion_data_before_graph_validation(self):
        self.connector.is_adopted.return_value = True
        self.connector.control_rest_view.return_value = object()
        self.validate(self.rig)
        self.connector.control_rest_view.assert_called_once_with(self.rig)
        self.records.assert_called_once_with(self.rig, strict=True)

    def test_explicit_matches_only_original_view_is_not_reinterpreted_as_adoption(self):
        self.connector.is_adopted.return_value = True
        self.connector.control_rest_view.side_effect = ValueError('Unrelated adoption record')
        old_view = NS(matches=Mock(), details=Mock())
        self.validate(self.rig, original_rest=old_view)
        self.connector.is_adopted.assert_not_called()
        self.connector.control_rest_view.assert_not_called()

    def test_foreign_rig_or_copy_proof_failure_stops_before_graph_validation(self):
        self.connector.is_adopted.return_value = True
        self.connector.control_rest_view.side_effect = ValueError('Foreign session rig/data')
        with self.assertRaisesRegex(self.error, 'Foreign session'):
            self.validate(self.rig)
        self.records.assert_not_called()


if __name__ == '__main__':
    unittest.main()
