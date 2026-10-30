"""Exact inert callback admission, changing predicates and hostile lookalikes."""
import ast
import hashlib
from pathlib import Path
import sys
import tempfile
from types import FunctionType, ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/hair_wiggle2_adapter.py'
MODULE = 'bl_ext.user_default.auto_rig_pro.src.rig_functions'
PAYLOAD = b'def rig_layers_anim_update(_unused):\n    if bpy.context.scene.arp_layers_animated:\n        writer()\n'


class Curve:
    def __init__(self, path):
        self.data_path = path
    @property
    def keyframe_points(self):
        raise AssertionError('Inert admission inspected keyframe contents')


def action(paths=(), layered=()):
    result = NS(fcurves=[Curve(path) for path in paths], layers=[NS(strips=[NS(
        channelbags=[NS(fcurves=[Curve(path) for path in layered])])])])
    result.as_pointer = lambda: id(result)
    return result


def strip(value=None, children=()):
    result = NS(action=value, strips=list(children))
    result.as_pointer = lambda: id(result)
    return result


class InertARPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'rig_functions.py'
        self.path.write_bytes(PAYLOAD)
        self.scene = NS(arp_layers_animated=False, animation_data=None)
        self.properties = {'arp_layers_animated': NS(type='BOOLEAN', is_array=False)}
        self.bpy = NS(context=NS(scene=self.scene), types=NS(Scene=NS(bl_rna=NS(properties=self.properties))),
                      app=NS(handlers=NS(frame_change_pre=[], frame_change_post=[])))
        self.module = ModuleType(MODULE)
        self.module.__file__ = str(self.path)
        self.module.bpy, self.module.writer = self.bpy, Mock(side_effect=AssertionError('Executed ARP writer'))
        code = next(code for code in compile(PAYLOAD, str(self.path), 'exec').co_consts
                    if hasattr(code, 'co_code') and code.co_name == 'rig_layers_anim_update')
        self.callback = FunctionType(code, vars(self.module), 'rig_layers_anim_update')
        self.module.rig_layers_anim_update = self.callback
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {MODULE: self.module}).start()
        tree = ast.parse(SOURCE.read_bytes())
        names = {'_callback_label', '_arp_callback_certificate', '_arp_action_has_flag_path',
                 '_arp_scene_flag_animated', '_arp_callback_is_inert', '_handler_gate', '_fail'}
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names
                 or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                    and target.id.startswith('_ARP_INERT_') for target in node.targets)]
        self.ns = dict(bpy=self.bpy, sys=sys, hashlib=hashlib, HairWiggleError=ValueError,
            _old=lambda: NS(_FRAME_OWNERSHIP={}, _frame_guard=lambda: None),
            runtime=NS(validate=Mock()), _frame_guard=lambda: None)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.ns)
        self.ns['_ARP_INERT_SOURCE_SHA256'] = hashlib.sha256(PAYLOAD).hexdigest()
        self.backend = NS(wiggle_pre=lambda: None, wiggle_post=lambda: None)
        self.bpy.app.handlers.frame_change_post.append(self.callback)
    def gate(self):
        self.ns['_handler_gate'](self.backend, object(), {'Hair Bone'})
    def data(self, direct=None, tracks=(), drivers=()):
        self.scene.animation_data = NS(action=direct, nla_tracks=[NS(strips=list(track)) for track in tracks],
                                       drivers=[Curve(path) for path in drivers])
    def test_reviewed_live_code_false_unanimated_is_allowed_without_executing_it(self):
        self.gate()
        self.callback(None)
        self.module.writer.assert_not_called()
        self.assertIs(self.ns['_ARP_INERT_CERTIFICATE']['function'], self.callback)
    def test_hot_guard_has_no_file_reads_resolve_or_compile(self):
        self.gate()
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Hot disk read')):
            with patch.object(Path, 'resolve', side_effect=AssertionError('Hot path stat')):
                with patch('builtins.compile', side_effect=AssertionError('Hot compile')):
                    self.gate()
                    self.gate()
    def test_false_predicate_is_rechecked_after_certificate(self):
        self.gate()
        self.scene.arp_layers_animated = True
        with self.assertRaisesRegex(ValueError, 'Turn off Auto Rig Pro.*' + MODULE.replace('.', r'\.')):
            self.gate()
        self.scene.arp_layers_animated = False
        self.gate()
    def test_rna_boolean_and_exact_false_are_required(self):
        for value in (0, 1, None, 'False'):
            self.scene.arp_layers_animated = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Turn off Auto Rig Pro'):
                self.gate()
        self.scene.arp_layers_animated = False
        self.properties['arp_layers_animated'].type = 'INT'
        with self.assertRaisesRegex(ValueError, 'unverified Scene setting'):
            self.gate()
        self.properties.clear()
        with self.assertRaisesRegex(ValueError, 'unverified Scene setting'):
            self.gate()
    def test_current_action_and_all_layered_slots_are_checked_without_keys(self):
        for path in self.ns['_ARP_INERT_PATHS']:
            for value in (action((path,)), action(layered=(path,))):
                self.data(direct=value)
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'must be unanimated'):
                    self.gate()
    def test_nla_including_meta_strips_and_muted_tracks_are_checked(self):
        self.data(tracks=((strip(children=(strip(action(('arp_layers_animated',))),)),),))
        self.scene.animation_data.nla_tracks[0].mute = True
        with self.assertRaisesRegex(ValueError, 'must be unanimated'):
            self.gate()
    def test_driver_and_new_path_after_admission_are_checked(self):
        self.gate()
        self.data(drivers=('["arp_layers_animated"]',))
        with self.assertRaisesRegex(ValueError, 'must be unanimated'):
            self.gate()
    def test_unrelated_scene_animation_is_allowed_without_keys(self):
        self.data(direct=action(('gravity',)), tracks=((strip(action(('frame_start',))),),), drivers=('render.fps',))
        self.gate()
    def test_exact_current_export_is_required_even_after_certificate(self):
        self.gate()
        self.module.rig_layers_anim_update = lambda: None
        with self.assertRaisesRegex(ValueError, 'unverified live bindings'):
            self.gate()
    def test_exact_module_global_bpy_is_required_even_after_certificate(self):
        self.gate()
        self.module.bpy = object()
        with self.assertRaisesRegex(ValueError, 'unverified live bindings'):
            self.gate()
    def test_runtime_code_mutation_does_not_inherit_the_certificate(self):
        self.gate()
        altered = PAYLOAD.replace(b'writer()', b'different_writer()')
        self.callback.__code__ = next(code for code in compile(altered, str(self.path), 'exec').co_consts
                                     if hasattr(code, 'co_code'))
        with self.assertRaisesRegex(ValueError, 'code differs'):
            self.gate()
    def test_unknown_source_hash_is_not_admitted_by_name(self):
        self.path.write_bytes(PAYLOAD + b'# another release\n')
        with self.assertRaisesRegex(ValueError, 'source version is not reviewed'):
            self.gate()
    def test_defaults_mutation_is_not_admitted(self):
        self.gate()
        self.callback.__defaults__ = (False,)
        with self.assertRaisesRegex(ValueError, 'unverified live bindings'):
            self.gate()
    def test_same_code_in_foreign_globals_is_not_admitted(self):
        self.module.rig_layers_anim_update = FunctionType(self.callback.__code__,
            {'__name__': MODULE, 'bpy': self.bpy, 'writer': self.module.writer})
        self.callback = self.module.rig_layers_anim_update
        self.bpy.app.handlers.frame_change_post[:] = [self.callback]
        with self.assertRaisesRegex(ValueError, 'unverified live bindings'):
            self.gate()
    def test_unknown_callbacks_still_refuse_and_identify_the_callable(self):
        def unknown():
            pass
        self.bpy.app.handlers.frame_change_post[:] = [unknown]
        with self.assertRaisesRegex(ValueError, 'Callback:.*unknown'):
            self.gate()
    def test_source_path_replacement_is_not_a_cached_admission(self):
        self.gate()
        self.module.__file__ = str(self.path.with_name('other.py'))
        with self.assertRaisesRegex(ValueError, 'source path changed'):
            self.gate()


if __name__ == '__main__':
    unittest.main()
