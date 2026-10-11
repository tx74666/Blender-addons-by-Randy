"""Pure weapon admission/dispatch checks; no Blender, solver or mesh import."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer' / 'animation_composition_weapon.py'
PACKAGE = '_rigging_weapon_admission'


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class Matrix:
    def __iter__(self):
        return iter(((1., 0., 0., 0.), (0., 1., 0., 0.),
                     (0., 0., 1., 0.), (0., 0., 0., 1.)))

    def __matmul__(self, _other):
        return self

    def inverted(self):
        return self


class Native:
    def __init__(self, pointer):
        self.pointer = pointer

    def as_pointer(self):
        return self.pointer


class RiggingWeaponTests(unittest.TestCase):
    def setUp(self):
        module = ast.parse(SOURCE.read_text(encoding='utf-8'))
        names = {'_rigging_context_admission', 'add_rigging_context',
                 'remove_rigging_context', 'add_context', 'validate_context'}
        body = [node for node in module.body if isinstance(node, ast.FunctionDef)
                and node.name in names]
        self.protocol = ModuleType(PACKAGE + '.animation_rigging_contract')
        self.protocol.validate_capture = Mock(side_effect=lambda value, **_kw: deepcopy(value))
        self.controls = ModuleType(PACKAGE + '.animation_composition_controls')
        self.controls.validate_scope = Mock()
        package = ModuleType(PACKAGE)
        package.__path__ = []
        self.modules = patch.dict(sys.modules, {PACKAGE: package,
            self.protocol.__name__: self.protocol, self.controls.__name__: self.controls})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.scene = object()
        self.rig = SimpleNamespace(data=SimpleNamespace(name='Original Rig',
            bones={'RightHand': SimpleNamespace(matrix_local=Matrix())}), matrix_world=Matrix())
        self.context = SimpleNamespace(scene=self.scene)
        self.preview = {'weaponRigBonePath': 'Body/RightHand',
                        'weaponSocketPath': 'Body/RightHand/RangedPosition'}
        self.capture = {'weaponPreview': self.preview,
                        'bindings': [{'tipPath': 'Body/RightHand'}]}
        self.packet = {'_sha256': 'a' * 64,
                       'bones': [{'name': 'RightHand', 'path': 'Body/RightHand', 'rest': []}]}
        self.mapping = SimpleNamespace(indices={'RightHand': 0}, conversion=Matrix(),
                                       rest_world={'RightHand': Matrix()})
        self.session = {'owner': 'session-owner', 'rig': self.rig, 'scene': self.scene,
            'base_action': Native(11), 'base_slot': 7, 'input_action': Native(22),
            'input_slot': 8, 'source_packet_sha256': 'a' * 64,
            'work_item_id': 'work', 'new_action_id': 'draft', 'source_read_only': False,
            'data_name': 'Original Rig', 'rest_sha256': digest({'rest': 'original'}),
            'conversion': [v for row in Matrix() for v in row], 'capture': self.capture}
        self.current = dict(self.session)
        self.native = SimpleNamespace(current=Mock(side_effect=lambda _rig: self.current))
        self.ua = SimpleNamespace(_rest_state=lambda _data: {'rest': 'original'},
                                  _matrix=lambda *_args: Matrix())
        self.core = Mock(return_value={'display_owner': 'display-owner'})
        self.document = {'meshes': 'authenticated'}
        self.namespace = {'__package__': PACKAGE, '_require': require,
            '_host': lambda name: self.native if name == 'unity_animation_rigging' else self.ua,
            'contract': SimpleNamespace(canonical_bytes=canonical, digest=digest,
                                        validate=Mock(side_effect=lambda value: value)),
            '_validate_context_identity': Mock(return_value=(self.document, ['surface'])),
            '_add_display': self.core, '_weapon_identity': lambda _spec: 'legacy-identity',
            '_attachment_basis': Mock(), 'RECORD': 'owned-weapon',
            'validate_owned': Mock(), 'remove_context': Mock()}
        exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), 'exec'), self.namespace)

    def admit(self):
        return self.namespace['_rigging_context_admission'](
            self.context, self.rig, self.preview, self.packet, self.mapping, self.session)

    def test_exact_native_session_packet_and_mapping_are_admitted(self):
        self.assertEqual(len(self.admit()), 64)
        self.protocol.validate_capture.assert_called_once_with(self.capture, packet=self.packet)

    def test_foreign_owner_action_slot_packet_or_rest_rejects(self):
        for key, value in (('owner', 'foreign'), ('base_slot', 99), ('input_slot', 99),
                           ('source_packet_sha256', 'b' * 64), ('rest_sha256', 'b' * 64)):
            with self.subTest(key=key):
                old = self.current[key]
                self.current[key] = value
                with self.assertRaises(ValueError):
                    self.admit()
                self.current[key] = old
        self.current['base_action'] = Native(99)
        with self.assertRaisesRegex(ValueError, 'native base or input'):
            self.admit()

    def test_missing_current_or_foreign_scene_rejects_before_display(self):
        self.current = None
        with self.assertRaises(ValueError):
            self.admit()
        self.core.assert_not_called()
        self.current = dict(self.session, scene=object())
        with self.assertRaises(ValueError):
            self.admit()

    def test_descendant_or_non_right_mount_is_not_a_socket_alias(self):
        for path in ('Body/RightHand', 'Body/RightHand/Extra/Socket', 'Body/LeftHand/Socket'):
            with self.subTest(path=path):
                self.preview['weaponSocketPath'] = path
                with self.assertRaisesRegex(ValueError, 'direct child'):
                    self.admit()

    def test_mapping_conversion_indices_rest_and_duplicate_bones_reject(self):
        self.current['conversion'] = [2.] * 16
        with self.assertRaisesRegex(ValueError, 'exact original'):
            self.admit()
        self.current['conversion'] = self.session['conversion']
        self.mapping.indices = {'RightHand': 9}
        with self.assertRaises(ValueError):
            self.admit()
        self.mapping.indices = {'RightHand': 0}
        self.mapping.rest_world = {}
        with self.assertRaises(ValueError):
            self.admit()
        self.mapping.rest_world = {'RightHand': Matrix()}
        self.packet['bones'].append(dict(self.packet['bones'][0]))
        with self.assertRaises(ValueError):
            self.admit()

    def test_new_route_reuses_geometry_without_legacy_fk_scope_or_grips(self):
        result = self.namespace['add_rigging_context'](
            self.context, self.rig, self.preview, self.packet, self.mapping, session=self.session)
        self.assertEqual(result, {'display_owner': 'display-owner'})
        args, kwargs = self.core.call_args
        self.assertIs(args[4], self.mapping)
        self.assertIs(kwargs['document'], self.document)
        self.assertEqual(kwargs['grips'], ())
        self.assertEqual(kwargs['rigging_owner'], self.session['owner'])
        self.controls.validate_scope.assert_not_called()
        self.namespace['contract'].validate.assert_not_called()
        self.current['source_packet_sha256'] = 'b' * 64
        with self.assertRaises(ValueError):
            kwargs['revalidate']()

    def test_absent_preview_does_not_load_native_but_cannot_omit_declared_context(self):
        self.session['capture'] = {'weaponPreview': None}
        self.assertIsNone(self.namespace['add_rigging_context'](
            self.context, self.rig, None, self.packet, self.mapping, session=self.session))
        self.native.current.assert_not_called()
        self.session['capture'] = self.capture
        with self.assertRaises(ValueError):
            self.namespace['add_rigging_context'](
                self.context, self.rig, None, self.packet, self.mapping, session=self.session)

    def test_legacy_route_still_validates_composition_and_fk_scope(self):
        spec = {'gripTargets': [{'original': True}]}
        scope = SimpleNamespace(mapping=self.mapping)
        self.namespace['add_context'](self.context, self.rig, spec, self.packet, scope)
        self.assertEqual(self.namespace['contract'].validate.call_count, 2)
        self.namespace['contract'].validate.assert_called_with(spec)
        self.controls.validate_scope.assert_called_once_with(
            self.context, self.rig, spec, self.packet, scope)
        self.assertEqual(self.core.call_args.kwargs['grips'], spec['gripTargets'])
        self.assertNotIn('rigging_owner', self.core.call_args.kwargs)

    def test_remove_rejects_a_closed_or_foreign_session_without_deletion(self):
        self.session['weapon_preview'] = {'record_key': 'owned-weapon'}
        self.current = None
        with self.assertRaisesRegex(ValueError, 'actual owning'):
            self.namespace['remove_rigging_context'](self.context, self.rig, session=self.session)
        self.namespace['remove_context'].assert_not_called()

    def test_remove_requires_exact_plain_proof_and_never_claims_legacy_display(self):
        proof = {'record_key': 'owned-weapon', 'rigging_session_owner': 'session-owner',
                 'display_owner': 'display-owner', 'identity': 'sealed-identity'}
        self.session['weapon_preview'] = proof
        record = {'rigging_session_owner': 'session-owner',
                  'owner': 'display-owner', 'identity': 'sealed-identity'}
        self.namespace['validate_owned'].return_value = (record, ())
        self.namespace['remove_rigging_context'](self.context, self.rig, session=self.session)
        self.namespace['remove_context'].assert_called_once_with(self.context, self.rig)
        self.namespace['remove_context'].reset_mock()
        for key in ('rigging_session_owner', 'owner', 'identity'):
            old = record[key]
            record[key] = 'foreign'
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.namespace['remove_rigging_context'](
                    self.context, self.rig, session=self.session)
            self.namespace['remove_context'].assert_not_called()
            record[key] = old


if __name__ == '__main__':
    unittest.main()
