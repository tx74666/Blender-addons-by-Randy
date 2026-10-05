"""Native weak ownership registry edge cases; disposable Blender process only.

Run --background --factory-startup --python this_file. Linked objects are read
from a tiny generated library. No authored scene or Unity output is accessed.
"""

from pathlib import Path
import sys
import tempfile
import unittest

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
import random_realm_builder_exporter as rr
from random_realm_builder_exporter import rr_export_identity_tracking as tracking
from test_export_identity_tracking_blender import (
    clear_scene, mesh_object, owned_value, registry_snapshot, select_only,
)


CONSTRAINT_FIELDS = (
    "name", "type", "target", "subtarget", "mute", "influence",
    "owner_space", "target_space", "use_x", "use_y", "use_z",
    "invert_x", "invert_y", "invert_z", "use_offset", "head_tail",
    "use_bbone_shape", "mix_mode",
)


def constraint_values(carrier):
    return tuple(tuple((name, owned_value(getattr(item, name)))
                       for name in CONSTRAINT_FIELDS if hasattr(item, name))
                 for item in carrier.constraints)


def object_native(obj):
    data = getattr(obj, "data", None)
    geometry = None
    if obj.type == "MESH":
        geometry = (
            tuple(tuple(vertex.co) for vertex in data.vertices),
            tuple(tuple(edge.vertices) for edge in data.edges),
            tuple((tuple(face.vertices), face.material_index) for face in data.polygons),
        )
    return (
        obj.name, obj.as_pointer(), obj.type,
        data.name if data else None, data.as_pointer() if data else None,
        owned_value(dict(data.items())) if data else None, geometry,
        owned_value(obj.parent), tuple(tuple(row) for row in obj.matrix_world),
        tuple(tuple(row) for row in obj.matrix_basis),
        tuple(tuple(row) for row in obj.matrix_parent_inverse),
        obj.hide_render, obj.hide_viewport, obj.hide_select,
        obj.library.filepath if obj.library else None,
        tuple((slot.link, owned_value(slot.material)) for slot in obj.material_slots),
    )


def scene_native():
    result = []
    for scene in bpy.data.scenes:
        settings = scene.rr_builder_export_settings
        layers = []
        for layer in scene.view_layers:
            # Native linking is lazy: compare evaluated layer membership both
            # before and after the operation, including copied Scenes.
            layer.update()
            objects = list(layer.objects)
            layers.append((layer.name, owned_value(layer.objects.active),
                           tuple((owned_value(obj), obj.select_get(view_layer=layer),
                                  obj.hide_get(view_layer=layer)) for obj in objects)))
        result.append((
            scene.name, scene.as_pointer(), scene.render.engine,
            scene.frame_current, scene.frame_start, scene.frame_end,
            scene.unit_settings.scale_length, owned_value(scene.camera),
            owned_value(scene.world),
            tuple(owned_value(obj) for obj in scene.objects), tuple(layers),
            owned_value({key: value for key, value in scene.items()
                         if key != tracking.REGISTRY_PROP}),
            owned_value(scene.rr_builder_reference_layout.reference_object),
            settings.export_mode, settings.use_reference_layout,
            tuple(item.object_name for item in settings.export_queue),
        ))
    return tuple(result)


def full_snapshot():
    bpy.context.view_layer.update()
    return (
        tuple((object_native(obj), owned_value(dict(obj.items())), constraint_values(obj))
              for obj in bpy.data.objects),
        scene_native(), registry_snapshot(),
    )


class InjectedRegistryFailure(RuntimeError):
    pass


class ExportIdentityRegistryTests(unittest.TestCase):
    def setUp(self):
        clear_scene()
        self.scene = bpy.context.scene
        for scene in list(bpy.data.scenes):
            if scene is not self.scene:
                bpy.data.scenes.remove(scene)
        self.scene["artist_scene_note"] = "preserve native scene metadata"
        self.temporary = tempfile.TemporaryDirectory(prefix="rr_registry_native_")
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        clear_scene()
        for scene in list(bpy.data.scenes):
            if scene is not bpy.context.scene:
                bpy.data.scenes.remove(scene)
        self.temporary.cleanup()

    def owner(self, name="Wall"):
        obj = mesh_object(name)
        obj["artist_object_note"] = {"label": "preserve", "weights": [0.25, 0.75]}
        obj.data["artist_mesh_note"] = "preserve native geometry"
        select_only(obj)
        stable = rr.ensure_export_identity(obj)[0]
        return obj, stable

    def carrier(self, scene):
        return scene[tracking.REGISTRY_PROP]["carrier"]

    def install_row(self, scene, carrier, stable, constraint):
        scene[tracking.REGISTRY_PROP] = {
            "carrier": carrier,
            tracking._key(stable): {"stableId": stable, "constraint": constraint.name},
        }

    def fixture_carrier(self, name, owner):
        carrier = bpy.data.objects.new(name, None)
        carrier[tracking.CARRIER_MARKER] = True
        carrier.hide_render = carrier.hide_viewport = carrier.hide_select = True
        constraint = carrier.constraints.new("COPY_LOCATION")
        constraint.name = "Fixture owner reference"
        constraint.target = owner
        constraint.mute = True
        constraint.influence = 0.0
        return carrier, constraint

    def linked_carrier(self, stable):
        target = mesh_object("Linked owner fixture")
        target[rr.EXPORT_STABLE_ID_PROP] = stable
        carrier, constraint = self.fixture_carrier("Linked registry fixture", target)
        # Nondefault values make accidental recreation/mutation of read-only
        # constraints observable even if name/target/mute happen to survive.
        constraint.owner_space = "LOCAL"
        constraint.target_space = "LOCAL"
        constraint.use_y = False
        constraint.invert_x = True
        constraint.use_offset = True
        constraint.influence = 0.375
        path = self.directory / "readonly_registry.blend"
        names = [carrier.name, target.name]
        bpy.data.libraries.write(str(path), {carrier, target})
        bpy.data.objects.remove(carrier, do_unlink=True)
        bpy.data.objects.remove(target, do_unlink=True)
        with bpy.data.libraries.load(str(path), link=True) as (_available, requested):
            requested.objects = names
        linked, linked_target = requested.objects
        self.assertFalse(linked.is_editable)
        self.assertIsNotNone(linked.library)
        self.assertTrue(linked[tracking.CARRIER_MARKER])
        self.assertIs(linked.constraints[0].target, linked_target)
        return linked, linked_target

    def test_transaction_keeps_linked_carrier_and_primary_exception_while_restoring_local_state(self):
        owner, stable = self.owner()
        copied = owner.copy()
        copied.data = owner.data.copy()
        copied.name = "Glass"
        bpy.context.scene.collection.objects.link(copied)
        copied[rr.EXPORT_ASSET_ID_OVERRIDE_PROP] = "Wall"
        linked, linked_target = self.linked_carrier("rr_asset_readonly_auxiliary")
        imported = bpy.data.scenes.new("Imported registry scene")
        imported.collection.objects.link(linked_target)
        self.install_row(imported, linked, linked_target[rr.EXPORT_STABLE_ID_PROP], linked.constraints[0])
        local = self.carrier(self.scene)
        before = full_snapshot()
        linked_pointer = linked.as_pointer()
        linked_constraint_pointer = linked.constraints[0].as_pointer()
        primary = InjectedRegistryFailure("primary fixture failure")
        with self.assertRaises(InjectedRegistryFailure) as caught:
            with tracking.transaction([copied]):
                copied[rr.EXPORT_STABLE_ID_PROP] = "rr_asset_temporary_copy"
                copied[rr.EXPORT_LAST_ID_PROP] = "TemporaryRoute"
                copied[rr.EXPORT_PREVIOUS_IDS_PROP] = '["TemporaryOldRoute"]'
                copied[rr.EXPORT_ASSET_ID_OVERRIDE_PROP] = "TemporaryRoute"
                local.constraints[0].target = copied
                local.constraints[0].mute = False
                local.constraints[0].influence = 0.5
                tracking.remember(copied)
                # Failed work may allocate a carrier before attaching its row.
                self.fixture_carrier("Transient failed registry", copied)
                raise primary
        self.assertIs(caught.exception, primary)
        self.assertEqual(full_snapshot(), before)
        self.assertIs(bpy.data.objects.get(linked.name), linked)
        self.assertEqual(linked.as_pointer(), linked_pointer)
        self.assertEqual(linked.constraints[0].as_pointer(), linked_constraint_pointer)
        self.assertEqual(tracking.ownership(stable), (True, owner))

    def test_scene_copy_shared_carrier_replacement_and_keep_original_do_not_double_remove(self):
        owner, stable = self.owner()
        carrier = self.carrier(self.scene)
        copied_scene = self.scene.copy()
        copied_scene.name = "Copied scene with shared registry"
        self.assertIs(self.carrier(copied_scene), carrier)
        key = tracking._key(stable)
        self.assertEqual(self.scene[tracking.REGISTRY_PROP][key]["constraint"],
                         copied_scene[tracking.REGISTRY_PROP][key]["constraint"])
        copied = owner.copy()
        copied.data = owner.data.copy()
        copied.name = "Glass"
        self.scene.collection.objects.link(copied)
        before_objects = [object_native(obj) for obj in (owner, copied)]
        before_scenes = scene_native()
        self.assertTrue(tracking.remember(owner, replace=True))
        self.assertIs(self.carrier(self.scene), carrier)
        self.assertIs(self.carrier(copied_scene), carrier)
        self.assertNotIn(key, copied_scene[tracking.REGISTRY_PROP])
        self.assertEqual(len(carrier.constraints), 1)
        self.assertIs(carrier.constraints[0].target, owner)
        self.assertEqual(tracking.ownership(stable), (True, owner))
        rr.keep_export_identity_owner(owner)
        self.assertEqual(owner[rr.EXPORT_STABLE_ID_PROP], stable)
        self.assertNotEqual(copied[rr.EXPORT_STABLE_ID_PROP], stable)
        self.assertEqual(rr.export_asset_id(owner), "Wall")
        self.assertEqual(rr.export_asset_id(copied), "Glass")
        self.assertEqual([object_native(obj) for obj in (owner, copied)], before_objects)
        self.assertEqual(scene_native(), before_scenes)
        self.assertEqual(tracking.ownership(stable), (True, owner))
        self.assertEqual(tracking.ownership(copied[rr.EXPORT_STABLE_ID_PROP]), (True, copied))
        self.assertTrue(rr.validate_export_identity(owner))
        self.assertTrue(rr.validate_export_identity(copied))

    def test_distinct_live_owners_conflict_but_explicit_replace_can_choose_the_original(self):
        owner, stable = self.owner()
        peer = mesh_object("Glass")
        peer[rr.EXPORT_STABLE_ID_PROP] = stable
        peer["artist_object_note"] = "preserve competing native object"
        other = bpy.data.scenes.new("Competing local registry scene")
        self.scene.collection.objects.unlink(peer)
        other.collection.objects.link(peer)
        carrier, constraint = self.fixture_carrier("Competing registry fixture", peer)
        self.install_row(other, carrier, stable, constraint)
        before = full_snapshot()
        with self.assertRaisesRegex(RuntimeError, "ownership conflicts"):
            tracking.ownership(stable)
        self.assertEqual(full_snapshot(), before)
        before_objects = [(object_native(obj), owned_value(dict(obj.items()))) for obj in (owner, peer)]
        before_scenes = scene_native()
        self.assertTrue(tracking.remember(owner, replace=True))
        self.assertEqual(tracking.ownership(stable), (True, owner))
        self.assertNotIn(tracking._key(stable), other[tracking.REGISTRY_PROP])
        self.assertEqual(len(carrier.constraints), 0)
        self.assertEqual([(object_native(obj), owned_value(dict(obj.items()))) for obj in (owner, peer)], before_objects)
        self.assertEqual(scene_native(), before_scenes)

    def assert_linked_registry_replacement_is_atomic(self, current_scene):
        owner, stable = self.owner()
        linked, target = self.linked_carrier(stable)
        destination = self.scene if current_scene else bpy.data.scenes.new("Imported competing registry")
        destination.collection.objects.link(target)
        self.install_row(destination, linked, stable, linked.constraints[0])
        before = full_snapshot()
        constraint_pointer = linked.constraints[0].as_pointer()
        with self.assertRaisesRegex(RuntimeError, "linked registry"):
            tracking.remember(owner, replace=True)
        self.assertEqual(full_snapshot(), before)
        self.assertEqual(linked.constraints[0].as_pointer(), constraint_pointer)
        self.assertIs(self.carrier(destination), linked)

    def test_current_editable_scene_cannot_replace_its_linked_readonly_carrier(self):
        self.assert_linked_registry_replacement_is_atomic(current_scene=True)

    def test_other_editable_imported_scene_blocks_replace_before_local_row_deletion(self):
        self.assert_linked_registry_replacement_is_atomic(current_scene=False)


def main():
    if hasattr(bpy.types.Scene, "rr_builder_export_settings"):
        raise RuntimeError("Use a disposable --factory-startup Blender process.")
    if Path(rr.__file__).resolve() != (ROOT / "addons" / "random_realm_builder_exporter" / "__init__.py").resolve():
        raise RuntimeError("Import the canonical repository package for these regressions.")
    rr.register()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ExportIdentityRegistryTests)
        print("EXPORT_IDENTITY_REGISTRY_TEST_COUNT=" + str(suite.countTestCases()))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            raise RuntimeError("Export identity registry regressions failed.")
        print("RR_EXPORT_IDENTITY_REGISTRY_BLENDER_PASS tests=" + str(result.testsRun))
    finally:
        rr.unregister()


if __name__ == "__main__":
    main()
