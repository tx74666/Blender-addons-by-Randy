"""Pure Dress skeletal-export ownership and mutation-order boundaries.

Native geometry, cache evaluation and library/FBX roundtrip are not emulated.
The prepared Blender fixture covers those separately in a private snapshot.
"""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class ID:
    def __init__(self, name, kind='MESH', **properties):
        self.name, self.type, self.properties = name, kind, properties
        self.animation_data = None

    def get(self, key, default=None):
        return self.properties.get(key, default)

    def __getitem__(self, key):
        return self.properties[key]


class Named(list):
    def get(self, name):
        return next((item for item in self if item.name == name), None)

    def __getitem__(self, key):
        return self.get(key) if isinstance(key, str) else super().__getitem__(key)


def curve(path):
    return SimpleNamespace(data_path=path, mute=True)


def action(path=None, layered=False):
    result = ID('Artist Action', 'ACTION')
    result.is_action_layered = layered
    result.fcurves = [curve(path)] if path else []
    result.layers = [SimpleNamespace(strips=[SimpleNamespace(channelbags=[
        SimpleNamespace(fcurves=result.fcurves)])])]
    return result


class DressAnimationExport(unittest.TestCase):
    def setUp(self):
        self.bpy = ModuleType('bpy')
        self.bpy.app = SimpleNamespace(background=True)
        self.bpy.data = SimpleNamespace(objects=Named(), scenes=Named(),
            filepath=str(Path('cdesigner-action-test/animation.blend').resolve()))
        self.package = ModuleType('_dress_animation_test')
        self.package.__path__ = [str(ROOT)]
        self.service = ModuleType('_dress_animation_test.skirt_surface')
        self.service.BACKEND = 'ACTUAL_SURFACE_DELTA_V1'
        self.skirt = ModuleType('_dress_animation_test.skirt_rig')
        self.calls = []
        self.rig = ID('Main Rig', 'ARMATURE')
        self.rig.pose = SimpleNamespace(bones=Named())
        self.rig.animation_data = SimpleNamespace(action=None, nla_tracks=[], drivers=[])
        self.bpy.data.objects.append(self.rig)
        self.service.export_capture = self.capture
        self.service.validate_snapshot = self.validate
        self.service.strip_export_snapshot = self.strip
        self.skirt.physics_control = lambda source: (source.holder, self.rig, source.path)
        self.modules = patch.dict(sys.modules, {'bpy': self.bpy,
            '_dress_animation_test': self.package,
            '_dress_animation_test.skirt_surface': self.service,
            '_dress_animation_test.skirt_rig': self.skirt})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location('_dress_animation_test.dress_export_snapshot',
                                                    ROOT / 'dress_export_snapshot.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.selected = action('pose.bones["Body"].rotation_euler')
        self.source = self.fixture('Dress')

    def fixture(self, name):
        source = ID(name, character_designer_skirt_armature=self.rig)
        source.valid, source.stripped = True, False
        source.path = 'pose.bones["' + name + ' Waist"]["physics_influence"]'
        source.holder = {'physics_influence': 1.}
        scene = ID(name + ' Home', 'SCENE')
        cloth = ID(name + ' Cloth')
        cloth.modifiers = Named([SimpleNamespace(name='Cloth', type='CLOTH',
            show_viewport=True, show_render=True)])
        neutral = ID(name + ' Neutral', 'ARMATURE')
        neutral.pose = SimpleNamespace(bones=Named())
        chain = {'def': [name + ' DEF'], 'phys': [name + ' PHYS']}
        for rig in (self.rig, neutral):
            deform = SimpleNamespace(name=chain['def'][0], constraints=Named([
                SimpleNamespace(name='Skirt manual pose', mute=False),
                SimpleNamespace(name='Skirt physics delta', mute=False)]))
            phys = SimpleNamespace(name=chain['phys'][0], constraints=Named([
                SimpleNamespace(name='Track', mute=False)]))
            for bone in (deform, phys):
                prefix = 'pose.bones[' + json.dumps(bone.name) + ']'
                bone.path_from_id = lambda value=prefix: value
                for constraint in bone.constraints:
                    path = prefix + '.constraints[' + json.dumps(constraint.name) + ']'
                    constraint.path_from_id = lambda value=path: value
            rig.pose.bones.extend((deform, phys))
            if rig.animation_data is None:
                rig.animation_data = SimpleNamespace(action=None, nla_tracks=[], drivers=[])
            driver_curve = curve(deform.constraints[1].path_from_id() + '.influence')
            driver_curve.mute = False
            driver_curve.driver = SimpleNamespace(type='AVERAGE', variables=[SimpleNamespace(
                type='SINGLE_PROP', targets=[SimpleNamespace(id=self.rig, data_path=source.path)])])
            rig.animation_data.drivers.append(driver_curve)
        source.record = {'rig': self.rig.name, 'owner': name + ' owner',
            'chains': [chain], 'physics': {'backend': self.service.BACKEND, 'surface': {
            'home_scene': scene.name, 'cloth_modifier': 'Cloth', 'roles': {
                'CLOTH_PROXY': [cloth.name], 'NEUTRAL_RIG': [neutral.name]}}}}
        source.properties[self.module.RECORD_KEY] = json.dumps(source.record)
        self.bpy.data.objects.extend((source, cloth, neutral))
        self.bpy.data.scenes.append(scene)
        source.cloth, source.neutral, source.home = cloth, neutral, scene
        return source

    def capture(self, source):
        return {'version': 1, 'backend': self.service.BACKEND, 'source': source.name,
                'rig': self.rig.name, 'owner': source.record['owner']}

    def validate(self, source, proof):
        self.calls.append(('prove', source.name))
        if not source.valid or proof != self.capture(source):
            raise ValueError('native proof rejected')

    def strip(self, source, proof):
        self.validate(source, proof)
        self.calls.append(('strip', source.name))
        # The real strip performs native validation again; all physics flags
        # must still be unmodified until every overlay has been stripped.
        self.assertTrue(source.cloth.modifiers[0].show_viewport)
        self.assertFalse(self.rig.pose.bones[source.record['chains'][0]['phys'][0]].constraints[0].mute)
        source.stripped = True

    def prepare(self, proofs=None):
        return self.module.prepare_animation_snapshot(self.rig,
            [self.capture(self.source)] if proofs is None else proofs, self.selected,
            private_snapshot=self.bpy.data.filepath)

    def native_flags(self, source):
        return (source.stripped, source.cloth.modifiers[0].show_viewport,
                tuple(c.mute for rig in (self.rig, source.neutral)
                      for name in (source.record['chains'][0]['def'][0], source.record['chains'][0]['phys'][0])
                      for c in rig.pose.bones[name].constraints), copy.deepcopy(source.holder))

    def test_host_scene_roots_are_proved_without_mutation(self):
        before = self.native_flags(self.source)
        self.assertEqual(self.module.snapshot_scene_roots([self.capture(self.source)]), {self.source.home})
        context = SimpleNamespace(scene=SimpleNamespace(objects=Named([self.rig])))
        self.assertEqual(self.module.capture_animation_surfaces(context, self.rig, self.selected),
                         [self.capture(self.source)])
        self.assertEqual(self.native_flags(self.source), before)
        self.assertEqual(self.calls, [('prove', 'Dress')])

    def test_only_owned_physical_contribution_is_omitted(self):
        before_action = copy.deepcopy(self.selected.fcurves[0].__dict__)
        result = self.prepare()
        self.assertEqual(result[0]['physics_omitted'], True)
        self.assertEqual(result[0]['simulation_baked'], False)
        self.assertTrue(self.source.stripped)
        self.assertFalse(self.source.cloth.modifiers[0].show_viewport)
        self.assertFalse(self.source.cloth.modifiers[0].show_render)
        for rig in (self.rig, self.source.neutral):
            self.assertFalse(rig.pose.bones['Dress DEF'].constraints[0].mute)
            self.assertTrue(rig.pose.bones['Dress DEF'].constraints[1].mute)
            self.assertTrue(rig.pose.bones['Dress PHYS'].constraints[0].mute)
        self.assertEqual(self.source.holder, {'physics_influence': 1.})
        self.assertEqual(self.selected.fcurves[0].__dict__, before_action)

    def test_every_native_proof_and_author_guard_precedes_any_mutation(self):
        second = self.fixture('Second')
        proofs = [self.capture(self.source), self.capture(second)]
        for invalid in ('native', 'holder_action'):
            with self.subTest(invalid=invalid):
                second.valid = invalid != 'native'
                self.selected = action(second.path) if invalid == 'holder_action' else action('location')
                first_before, second_before = self.native_flags(self.source), self.native_flags(second)
                with self.assertRaises(ValueError): self.prepare(proofs)
                self.assertEqual(self.native_flags(self.source), first_before)
                self.assertEqual(self.native_flags(second), second_before)
                self.assertFalse(any(call[0] == 'strip' for call in self.calls))

    def test_all_overlays_strip_before_any_physical_endpoint_changes(self):
        second = self.fixture('Second')
        self.prepare([self.capture(self.source), self.capture(second)])
        self.assertEqual([name for call, name in self.calls if call == 'strip'], ['Dress', 'Second'])
        self.assertTrue(second.stripped)

    def test_holder_actions_nla_meta_drivers_and_quote_aliases_are_preserved(self):
        for kind in ('selected', 'active', 'layered', 'nla_meta', 'driver', 'alias'):
            with self.subTest(kind=kind):
                self.rig.animation_data = SimpleNamespace(action=None, nla_tracks=[], drivers=[])
                self.selected = action('location')
                path = self.source.path
                if kind == 'selected': self.selected = action(path)
                elif kind == 'active': self.rig.animation_data.action = action(path)
                elif kind == 'layered': self.selected = action(path, True)
                elif kind == 'nla_meta':
                    child = SimpleNamespace(action=action(path), strips=[])
                    self.rig.animation_data.nla_tracks = [SimpleNamespace(strips=[SimpleNamespace(action=None, strips=[child])])]
                elif kind == 'driver': self.rig.animation_data.drivers = [curve(path)]
                elif kind == 'alias': self.selected = action(path.replace('"', "'"))
                before = self.native_flags(self.source)
                with self.assertRaisesRegex(ValueError, 'physics_influence'): self.prepare()
                self.assertEqual(self.native_flags(self.source), before)

    def test_private_background_snapshot_is_required(self):
        for reason in ('foreground', 'other_path', 'other_directory', 'missing_path'):
            with self.subTest(reason=reason):
                before = self.native_flags(self.source)
                old_path = self.bpy.data.filepath
                supplied = old_path
                if reason == 'foreground': self.bpy.app.background = False
                elif reason == 'other_path': supplied = str(Path('cdesigner-action-other/animation.blend').resolve())
                elif reason == 'other_directory':
                    self.bpy.data.filepath = str(Path('artist/animation.blend').resolve())
                    supplied = self.bpy.data.filepath
                else: supplied = None
                with self.assertRaisesRegex(ValueError, 'isolated background'):
                    self.module.prepare_animation_snapshot(self.rig, [self.capture(self.source)],
                        self.selected, private_snapshot=supplied)
                self.assertEqual(self.native_flags(self.source), before)
                self.bpy.app.background, self.bpy.data.filepath = True, old_path

    def test_nonactive_selected_constraint_false_keys_are_refused_in_host_and_worker(self):
        self.rig.animation_data.action = action('location')
        paths = (
            'pose.bones["Dress DEF"].constraints["Skirt physics delta"].mute',
            'pose.bones["Dress DEF"].constraints["Skirt physics delta"].influence',
            "pose.bones['Dress DEF'].constraints['Skirt physics delta'].mute",
            'pose.bones["Dress DEF"].constraints[1].mute',
            'pose.bones["Dress PHYS"].constraints["Track"].mute',
            'pose.bones["Dress PHYS"].constraints[0].target',
        )
        for path in paths:
            with self.subTest(path=path):
                self.selected = action(path)
                self.selected.fcurves[0].mute = False
                self.selected.fcurves[0].keyframe_value = False
                before = self.native_flags(self.source)
                context = SimpleNamespace(scene=SimpleNamespace(objects=self.bpy.data.objects))
                with self.assertRaisesRegex(ValueError, 'physical constraint'):
                    self.module.capture_animation_surfaces(context, self.rig, self.selected)
                with self.assertRaisesRegex(ValueError, 'physical constraint'): self.prepare()
                self.assertEqual(self.native_flags(self.source), before)
                self.assertFalse(any(call[0] == 'strip' for call in self.calls))

    def test_physical_constraint_actions_nla_and_drivers_cannot_reopen_routing(self):
        active, nla, main_drivers = self.rig.animation_data.action, self.rig.animation_data.nla_tracks, list(self.rig.animation_data.drivers)
        for case in ('active_mute', 'nla_aim', 'driver_def_mute', 'driver_def_influence',
                     'driver_aim_mute', 'driver_aim_influence', 'driver_aim_target', 'driver_quote_alias',
                     'neutral_mute', 'neutral_influence', 'duplicate_generated'):
            with self.subTest(case=case):
                self.rig.animation_data.action, self.rig.animation_data.nla_tracks = active, nla
                self.rig.animation_data.drivers = list(main_drivers)
                neutral = self.source.neutral.animation_data
                neutral_drivers = list(neutral.drivers)
                deform = 'pose.bones["Dress DEF"].constraints["Skirt physics delta"]'
                aim = 'pose.bones["Dress PHYS"].constraints[0]'
                if case == 'active_mute': self.rig.animation_data.action = action(deform + '.mute')
                elif case == 'nla_aim':
                    self.rig.animation_data.nla_tracks = [SimpleNamespace(strips=[SimpleNamespace(
                        action=action(aim + '.influence'), strips=[])])]
                elif case == 'duplicate_generated':
                    self.rig.animation_data.drivers.append(self.rig.animation_data.drivers[0])
                else:
                    target = neutral if case.startswith('neutral') else self.rig.animation_data
                    path = ((deform if 'def' in case or case.startswith('neutral') or case == 'driver_quote_alias' else aim)
                            + ('.mute' if 'mute' in case or case == 'driver_quote_alias' else '.target' if 'target' in case else '.influence'))
                    if case == 'driver_quote_alias': path = path.replace('"', "'")
                    candidate = curve(path)
                    candidate.mute = False
                    candidate.driver = SimpleNamespace(type='SCRIPTED', variables=[])
                    target.drivers.append(candidate)
                before = self.native_flags(self.source)
                with self.assertRaisesRegex(ValueError, 'physical'): self.prepare()
                self.assertEqual(self.native_flags(self.source), before)
                self.assertFalse(any(call[0] == 'strip' for call in self.calls))
                neutral.drivers = neutral_drivers

    def test_inventory_missing_duplicate_or_wrong_rig_is_rejected(self):
        for invalid in ('missing', 'duplicate', 'wrong_rig', 'changed_source'):
            with self.subTest(invalid=invalid):
                proof = self.capture(self.source)
                proofs = [] if invalid == 'missing' else [proof]
                if invalid == 'duplicate': proofs.append(proof)
                elif invalid == 'wrong_rig': proof['rig'] = 'Other Rig'
                elif invalid == 'changed_source': proof['source'] = 'Missing'
                before = self.native_flags(self.source)
                with self.assertRaises(ValueError): self.prepare(proofs)
                self.assertEqual(self.native_flags(self.source), before)

    def test_plain_fk_keeps_existing_worker_boundary(self):
        self.source.properties[self.module.RIG_KEY] = ID('Other Rig', 'ARMATURE')
        self.assertEqual(self.module.prepare_animation_snapshot(self.rig, [], self.selected), [])

    def test_worker_prepares_before_first_frame_link_or_evaluation(self):
        tree = ast.parse((ROOT / 'animation_export_worker.py').read_text(encoding='utf-8'))
        export = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'export_job')
        calls = list(ast.walk(export))
        prepared = [node.lineno for node in calls if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == 'prepare_animation_snapshot']
        evaluated = [node.lineno for node in calls if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute) and node.func.attr in {'frame_set', 'update', 'link', 'evaluated_get'}]
        self.assertEqual(len(prepared), 1)
        self.assertLess(prepared[0], min(evaluated))


if __name__ == '__main__':
    unittest.main()
