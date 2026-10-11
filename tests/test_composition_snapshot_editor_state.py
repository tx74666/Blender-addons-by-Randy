"""Snapshot-boundary regressions; no Blender process or artist mutation."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'))
import animation_composition_export as export

FIELD = 'character_designer_unity_export'


class ID:
    def as_pointer(self):
        return id(self)


class Group:
    def __init__(self, **values):
        self.values = values
        self.bl_rna = NS(properties=[NS(identifier='rna_type', type='POINTER')] + [
            NS(identifier=name, type='POINTER' if isinstance(value, ID) or value is None else
               'COLLECTION' if isinstance(value, list) else
               'BOOLEAN' if isinstance(value, bool) else 'STRING')
            for name, value in values.items()])

    def is_property_set(self, name):
        return name in self.values and self.values[name] is not None

    def __getattr__(self, name):
        return self.values[name]


class Rig(ID):
    def __init__(self, settings=None):
        self.data = ID()
        self.data.animation_data = None
        self.library = None
        self.animation_data = NS(drivers=[])
        self.values = {} if settings is None else {FIELD: settings}
        self.bl_rna = NS(properties={FIELD: object()})
        self.other_editor_state = object()

    def is_property_set(self, name):
        return name in self.values

    def property_unset(self, name):
        self.values.pop(name, None)

    def __getattr__(self, name):
        return self.values[name]


def settings(objects, materials):
    return Group(directory='D:/Unity/Assets', filename='Cosha', show_objects=True,
        extras=[Group(object=obj, enabled=True) for obj in objects],
        simple_materials=[Group(material=materials[0])],
        unity_material_matches=[Group(material=materials[1], material_guid='kept')])


class SnapshotEditorStateTests(unittest.TestCase):
    def setUp(self):
        self.bpy = patch.dict(sys.modules, bpy=NS(types=NS(ID=ID)))
        self.bpy.start()
        self.addCleanup(self.bpy.stop)

    def test_five_native_picker_refs_removed_only_from_copy(self):
        meshes, materials = [ID() for _ in range(3)], [ID(), ID()]
        original = Rig(settings(meshes, materials))
        copied = Rig(settings(meshes, materials))
        original_state = export._snapshot_editor_state(original)
        other = copied.other_editor_state
        export._prepare_snapshot_editor(copied, original)
        self.assertFalse(copied.is_property_set(FIELD))
        self.assertEqual(export._snapshot_editor_state(original), original_state)
        self.assertEqual([entry.object for entry in original.values[FIELD].extras], meshes)
        self.assertEqual(original.values[FIELD].simple_materials[0].material, materials[0])
        self.assertEqual(original.values[FIELD].unity_material_matches[0].material, materials[1])
        self.assertIs(copied.other_editor_state, other)

    def test_unset_original_group_is_not_materialized(self):
        original, copied = Rig(), Rig()
        export._prepare_snapshot_editor(copied, original)
        self.assertEqual(original.values, {})
        self.assertEqual(copied.values, {})

    def test_artist_or_shared_armature_rejected_before_unset(self):
        original = Rig(settings([ID()], [ID(), ID()]))
        copied = Rig(settings([ID()], [ID(), ID()]))
        for target in (original, copied):
            with self.subTest(same_object=target is original):
                target.data = original.data
                before = dict(target.values)
                with self.assertRaisesRegex(ValueError, 'disposable'):
                    export._prepare_snapshot_editor(target, original)
                self.assertEqual(target.values, before)

    def test_even_muted_driver_using_picker_reference_rejected(self):
        original, copied = Rig(), Rig(settings([ID()], [ID(), ID()]))
        copied.animation_data.drivers = [NS(data_path='pose.bones["Control"].location', mute=True,
            driver=NS(use_self=False, expression='var', variables=[NS(targets=[
                NS(id=copied, data_path=FIELD + '.extras[0].object')])]))]
        with self.assertRaisesRegex(ValueError, 'evaluation input'):
            export._prepare_snapshot_editor(copied, original)
        self.assertTrue(copied.is_property_set(FIELD))

    def test_self_expression_using_editor_group_rejected(self):
        original, copied = Rig(), Rig(settings([ID()], [ID(), ID()]))
        copied.animation_data.drivers = [NS(data_path='pose.bones["Control"].location',
            driver=NS(use_self=True, expression='self.' + FIELD + '.show_objects', variables=[]))]
        with self.assertRaisesRegex(ValueError, 'evaluation input'):
            export._prepare_snapshot_editor(copied, original)
        self.assertTrue(copied.is_property_set(FIELD))

    def test_missing_registration_fails_before_mutation(self):
        original, copied = Rig(), Rig(settings([ID()], [ID(), ID()]))
        copied.bl_rna.properties = {}
        with self.assertRaisesRegex(ValueError, 'Register'):
            export._prepare_snapshot_editor(copied, original)
        self.assertTrue(copied.is_property_set(FIELD))

    def test_failed_unset_is_not_accepted(self):
        original, copied = Rig(), Rig(settings([ID()], [ID(), ID()]))
        copied.property_unset = lambda name: None
        with self.assertRaisesRegex(ValueError, 'preserve'):
            export._prepare_snapshot_editor(copied, original)

    def test_another_python_wrapper_cannot_bypass_native_ownership(self):
        original = Rig(settings([ID()], [ID(), ID()]))
        wrapper = Rig(settings([ID()], [ID(), ID()]))
        wrapper.as_pointer = original.as_pointer
        with self.assertRaisesRegex(ValueError, 'disposable'):
            export._prepare_snapshot_editor(wrapper, original)
        self.assertTrue(wrapper.is_property_set(FIELD))


if __name__ == '__main__':
    unittest.main()
