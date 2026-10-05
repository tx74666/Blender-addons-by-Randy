"""Registered Dress motion UI transport on disposable native Cloth setups.

Run with factory-startup background Blender. No artist file is opened, no
simulation is baked, and the dialog transport is replaced with a recorder.
"""
import json
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "addons"), str(ROOT / "tests")]
from character_designer import skirt, skirt_rig, skirt_physics
from character_designer import skirt_motion_profiles as profiles
from character_designer import skirt_motion_tuning as tuning
from character_designer import skirt_motion_ui as ui
from test_skirt_physics_blender import activate, character
from test_skirt_topology_blender import frustum


class Layout:
    def __init__(self, events=None, parent=None):
        self.events = [] if events is None else events
        self.parent, self._enabled = parent, True

    @property
    def enabled(self):
        return self._enabled and (self.parent is None or self.parent.enabled)

    @enabled.setter
    def enabled(self, value):
        self._enabled = value

    def row(self, **_kwargs):
        return Layout(self.events, self)

    box = column = row

    def label(self, **kwargs):
        self.events.append(("label", kwargs.get("text", ""), self.enabled))

    def prop(self, _data, name, **_kwargs):
        self.events.append(("prop", name, self.enabled))

    def operator(self, name, **kwargs):
        namespace, operation = name.split(".")
        getattr(getattr(bpy.ops, namespace), operation).get_rna_type()
        values = SimpleNamespace()
        self.events.append(("operator", name, kwargs.get("text", ""), self.enabled, values))
        return values


def state(source):
    rig = source[skirt_rig.RIG_KEY]
    return (tuple(tuple(vertex.co) for vertex in source.data.vertices),
            tuple((group.name, group.lock_weight) for group in source.vertex_groups),
            tuple(tuple((item.group, item.weight) for item in vertex.groups) for vertex in source.data.vertices),
            tuple((bone.name, tuple(bone.head_local), tuple(bone.tail_local)) for bone in rig.data.bones),
            tuple((bone.name, tuple(tuple(row) for row in bone.matrix_basis)) for bone in rig.pose.bones),
            rig.animation_data.action if rig.animation_data else None)


def artist_source_state(source, group_names):
    keys = source.data.shape_keys
    return {
        "basis": tuple(tuple(vertex.co) for vertex in source.data.vertices),
        "edges": tuple(tuple(edge.vertices) for edge in source.data.edges),
        "faces": tuple(tuple(face.vertices) for face in source.data.polygons),
        "keys": tuple((key.name, key.value, key.mute, key.slider_min, key.slider_max,
                       key.relative_key.name, tuple(tuple(point.co) for point in key.data))
                      for key in keys.key_blocks) if keys else (),
        "weights": tuple((name, source.vertex_groups[name].index,
                          source.vertex_groups[name].lock_weight,
                          tuple(next((item.weight for item in vertex.groups
                                      if item.group == source.vertex_groups[name].index), None)
                                for vertex in source.data.vertices)) for name in group_names),
    }


def call(name, *, cancelled=False, **kwargs):
    try:
        result = getattr(bpy.ops.character_designer, name)(**kwargs)
    except RuntimeError:
        if not cancelled:
            raise
        result = {"CANCELLED"}
    assert result == ({"CANCELLED"} if cancelled else {"FINISHED"}), (name, result)


class DressMotionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registered = []
        for item in (*skirt.SKIRT_CLASSES, *ui.DRESS_MOTION_CLASSES):
            if not item.is_registered:
                bpy.utils.register_class(item)
                cls.registered.append(item)
        cls.created_property = not hasattr(bpy.types.WindowManager, "character_designer_skirt")
        if cls.created_property:
            bpy.types.WindowManager.character_designer_skirt = bpy.props.PointerProperty(
                type=skirt.CharacterDesignerSkirtState)
        cls.rig = character()
        cls.sources = []
        for name, physics in (("Motion Dress A", True), ("Motion Dress B", True),
                              ("Manual Dress", False)):
            source = frustum(name)
            activate(source)
            skirt_rig.build_skirt(bpy.context, source, chain_count=4, segment_count=3,
                                 armature=cls.rig, parent_bone="Hips")
            if physics:
                skirt_physics.add_physics(bpy.context, source)
            tuning.initialize(source)
            cls.sources.append(source)

    @classmethod
    def tearDownClass(cls):
        skirt.stop_skirt_runtime()
        if cls.created_property:
            del bpy.types.WindowManager.character_designer_skirt
        for item in reversed(cls.registered):
            bpy.utils.unregister_class(item)

    def setUp(self):
        self.source = self.sources[0]
        activate(self.source)
        self.record = skirt_rig.read_record(self.source)

    def cloth(self, source=None):
        return skirt_physics.validate_physics(source or self.source)[3]

    @contextmanager
    def fresh_generation_settings(self):
        settings = bpy.context.window_manager.character_designer_skirt
        names = ("source", "armature", "parent_bone", "chain_count", "segment_count", "physics", "capability")
        previous = {name: (settings.is_property_set(name), getattr(settings, name)) for name in names}
        try:
            for name in names:
                settings.property_unset(name)
            self.assertEqual((settings.chain_count, settings.segment_count, settings.capability, settings.physics),
                             (8, 4, "BOTH", True))
            self.assertFalse(settings.is_property_set("physics"))
            self.assertFalse(settings.is_property_set("capability"))
            settings.armature, settings.parent_bone = self.rig, "Hips"
            yield settings
        finally:
            for name, (was_set, value) in previous.items():
                if was_set:
                    setattr(settings, name, value)
                else:
                    settings.property_unset(name)

    def generation_source(self, name):
        source = frustum(name)
        activate(source)
        group = source.vertex_groups.new(name="Artist pin")
        group.add([0, 3, 7], 0.42, "REPLACE")
        group.lock_weight = True
        source.shape_key_add(name="Basis")
        key = source.shape_key_add(name="Artist fold")
        key.data[12].co.z += 0.012
        key.value = 0.27
        return source

    def assert_internal_chains_retained(self, source, record):
        rig = source[skirt_rig.RIG_KEY]
        self.assertEqual(len(record["chains"]), record["chain_count"])
        for chain in record["chains"]:
            for role in ("manual", "phys", "def"):
                self.assertEqual(len(chain[role]), record["segment_count"])
                self.assertTrue(all(name in rig.data.bones for name in chain[role]))

    def draw(self, source=None, record=None):
        source = self.source if source is None else source
        record = skirt_rig.read_record(source) if record is None else record
        layout = Layout()
        with patch.object(tuning, "initialize", side_effect=AssertionError("Draw initialized")), \
                patch.object(tuning, "effective", side_effect=AssertionError("Draw read native")), \
                patch.object(tuning, "apply", side_effect=AssertionError("Draw applied")), \
                patch.object(skirt_physics, "validate_physics", side_effect=AssertionError("Draw proved physics")), \
                patch.object(skirt_rig, "read_record", side_effect=AssertionError("Draw proved rig")), \
                patch.object(skirt, "_source", side_effect=AssertionError("Draw resolved source again")):
            ui.draw_motion(layout, bpy.context, source, record)
        return layout.events

    def test_registered_property_ranges_match_profile_contract(self):
        props = bpy.ops.character_designer.dress_motion_tuning.get_rna_type().properties
        for key, value in profiles.DEFAULTS.items():
            self.assertIn(key, props)
            self.assertAlmostEqual(props[key].default, value, places=5)
            if key in profiles.RANGES:
                low, high = profiles.RANGES[key]
                self.assertAlmostEqual(props[key].hard_min, low, places=5)
                self.assertAlmostEqual(props[key].hard_max, high, places=5)

    def test_draw_and_poll_use_only_saved_metadata(self):
        before = self.source[profiles.PROFILE_KEY]
        events = self.draw(record=self.record)
        self.assertEqual(self.source[profiles.PROFILE_KEY], before)
        self.assertIn("Automatic", [event[2] for event in events if event[0] == "operator"])
        self.assertIn("character_designer.skirt_bake_physics", [event[1] for event in events if event[0] == "operator"])
        with patch.object(skirt_rig, "read_record", side_effect=AssertionError("Poll proved rig")), \
                patch.object(skirt_physics, "validate_physics", side_effect=AssertionError("Poll proved physics")):
            self.assertTrue(ui.CHARACTERDESIGNER_OT_dress_motion_tuning.poll(bpy.context))

    def test_missing_and_invalid_profiles_do_not_mutate_on_draw(self):
        saved = self.source[profiles.PROFILE_KEY]
        try:
            del self.source[profiles.PROFILE_KEY]
            self.assertTrue(any(event[0] == "operator" for event in self.draw(record=self.record)))
            self.assertNotIn(profiles.PROFILE_KEY, self.source)
            self.source[profiles.PROFILE_KEY] = "{broken"
            events = self.draw(record=self.record)
            self.assertEqual(self.source[profiles.PROFILE_KEY], "{broken")
            self.assertTrue(any(event[0] == "label" and "review" in event[1] for event in events))
        finally:
            self.source[profiles.PROFILE_KEY] = saved

    def test_current_influence_takes_priority_over_saved_mode(self):
        holder = skirt_rig.physics_control(self.source)[0]
        previous = holder.get("physics_influence")
        try:
            holder["physics_influence"] = 0.0
            profile = profiles.read(self.source, self.record)
            self.assertEqual(ui._metadata_mode(self.source, self.record, profile), "MANUAL")
            holder["physics_influence"] = 1.0
            self.assertEqual(ui._metadata_mode(self.source, self.record, profile), "AUTOMATIC")
        finally:
            holder["physics_influence"] = previous

    def test_manual_only_setup_has_hint_and_no_physics_actions(self):
        source = self.sources[2]
        events = self.draw(source)
        operations = {event[1] for event in events if event[0] == "operator"}
        self.assertNotIn("character_designer.dress_motion_tuning", operations)
        self.assertNotIn("character_designer.dress_motion_reset", operations)
        automatic = next(event for event in events if event[0] == "operator" and event[2] == "Automatic")
        self.assertFalse(automatic[3])
        self.assertTrue(any(event[0] == "label" and "Manual controls ready" in event[1] for event in events))

    def test_capability_and_baked_metadata_gate_buttons(self):
        saved = self.source[profiles.PROFILE_KEY]
        try:
            profile = profiles.read(self.source, self.record)
            profile = profiles.edited(profile, self.record, capability="PHYSICS", mode="AUTOMATIC")
            profiles.write(self.source, profile, self.record)
            events = self.draw(record=self.record)
            manual = next(event for event in events if event[0] == "operator" and event[2] == "Manual")
            self.assertFalse(manual[3])
            record = json.loads(self.source[skirt_rig.RECORD_KEY])
            record["physics"]["baked_range"] = [1, 3]
            events = self.draw(record=record)
            tune = next(event for event in events if event[0] == "operator" and event[2] == "Tuning")
            self.assertFalse(tune[3])
            reset = next(event for event in events if event[0] == "operator" and event[2] == "Reset")
            self.assertTrue(reset[3])
        finally:
            self.source[profiles.PROFILE_KEY] = saved

    def test_busy_and_original_mode_disable_motion_actions(self):
        key = bpy.context.window_manager.as_pointer()
        skirt._ACTIVE_BAKES[key] = object()
        try:
            self.assertFalse(ui.CHARACTERDESIGNER_OT_dress_motion_mode.poll(bpy.context))
            self.assertTrue(all(not event[3] for event in self.draw(record=self.record) if event[0] == "operator"))
        finally:
            skirt._ACTIVE_BAKES.pop(key, None)
        rig = self.source[skirt_rig.RIG_KEY]
        rig[skirt_rig._ORIGINAL_SESSION_KEY] = "{}"
        try:
            self.assertFalse(ui.CHARACTERDESIGNER_OT_dress_motion_mode.poll(bpy.context))
            self.assertTrue(all(not event[3] for event in self.draw(record=self.record) if event[0] == "operator"))
        finally:
            del rig[skirt_rig._ORIGINAL_SESSION_KEY]

    def test_mode_switch_preserves_author_data_and_bake_record(self):
        record = skirt_rig.read_record(self.source)
        raw = self.source[skirt_rig.RECORD_KEY]
        record["physics"]["baked_range"] = [1, 3]
        skirt_rig.write_record(self.source, record)
        before = state(self.source)
        try:
            call("dress_motion_mode", mode="MANUAL")
            self.assertEqual(skirt_rig.physics_control(self.source)[0]["physics_influence"], 0.0)
            call("dress_motion_mode", mode="AUTOMATIC")
            self.assertEqual(skirt_rig.physics_control(self.source)[0]["physics_influence"], 1.0)
            self.assertEqual(skirt_rig.read_record(self.source)["physics"]["baked_range"], [1, 3])
            self.assertEqual(state(self.source), before)
        finally:
            self.source[skirt_rig.RECORD_KEY] = raw

    def test_explicit_batch_changes_only_requested_settings(self):
        first, second, manual = self.sources
        self.cloth(second).settings.mass = 0.37
        tuning.initialize(second)
        manual_before = manual[profiles.PROFILE_KEY]
        before = state(first)
        call("dress_motion_tuning", all_dresses=True, bend=2.4, quality=9)
        self.assertAlmostEqual(self.cloth(first).settings.bending_stiffness, 2.4, places=5)
        self.assertAlmostEqual(self.cloth(second).settings.bending_stiffness, 2.4, places=5)
        self.assertAlmostEqual(self.cloth(second).settings.mass, 0.37, places=5)
        self.assertEqual(self.cloth(first).settings.quality, 9)
        self.assertEqual(manual[profiles.PROFILE_KEY], manual_before)
        self.assertEqual(state(first), before)

    def test_generation_fresh_defaults_create_both_automatic_cloth(self):
        with self.fresh_generation_settings() as settings:
            source = self.generation_source("Fresh Both Dress")
            call("create_skirt_setup")
            record = skirt_rig.read_record(source)
            profile = profiles.read(source, record)
            self.assertEqual((profile["capability"], profile["mode"]), ("BOTH", "AUTOMATIC"))
            self.assertEqual((record["chain_count"], record["segment_count"]), (8, 4))
            self.assertTrue(record.get("physics"))
            self.assertEqual(self.cloth(source).type, "CLOTH")
            self.assertEqual(skirt_rig.physics_control(source)[0]["physics_influence"], 1.0)
            self.assertFalse(settings.is_property_set("capability"))
            self.assertFalse(settings.is_property_set("physics"))
            self.assert_internal_chains_retained(source, record)

    def test_generation_manual_and_legacy_false_preserve_artist_shape_and_weights(self):
        for legacy in (False, True):
            with self.subTest(legacy_physics_false=legacy), self.fresh_generation_settings() as settings:
                if legacy:
                    settings.physics = False
                    self.assertTrue(settings.is_property_set("physics"))
                    self.assertFalse(settings.is_property_set("capability"))
                    self.assertEqual(settings.capability, "BOTH")
                else:
                    settings.capability = "MANUAL"
                    self.assertTrue(settings.is_property_set("capability"))
                    self.assertFalse(settings.is_property_set("physics"))
                    self.assertTrue(settings.physics)
                source = self.generation_source("Legacy Manual Dress" if legacy else "Explicit Manual Dress")
                before = artist_source_state(source, ("Artist pin",))
                call("create_skirt_setup")
                record = skirt_rig.read_record(source)
                profile = profiles.read(source, record)
                self.assertEqual((profile["capability"], profile["mode"]), ("MANUAL", "MANUAL"))
                self.assertFalse(record.get("physics"))
                self.assertEqual(skirt_rig.physics_control(source)[0].get("physics_influence", 0.0), 0.0)
                self.assertFalse(any(modifier.type == "CLOTH"
                                     for name in record["owned_objects"]
                                     for modifier in bpy.data.objects[name].modifiers))
                self.assertEqual(artist_source_state(source, ("Artist pin",)), before)
                self.assert_internal_chains_retained(source, record)

    def test_generation_explicit_physics_overrides_legacy_false_and_rejects_manual_mode(self):
        with self.fresh_generation_settings() as settings:
            settings.physics = False
            settings.capability = "PHYSICS"
            self.assertTrue(settings.is_property_set("physics"))
            self.assertTrue(settings.is_property_set("capability"))
            source = self.generation_source("Explicit Physics Dress")
            call("create_skirt_setup")
            record = skirt_rig.read_record(source)
            profile = profiles.read(source, record)
            self.assertEqual((profile["capability"], profile["mode"]), ("PHYSICS", "AUTOMATIC"))
            self.assertEqual(self.cloth(source).type, "CLOTH")
            self.assert_internal_chains_retained(source, record)
            before = (source[profiles.PROFILE_KEY], source[skirt_rig.RECORD_KEY], state(source))
            call("dress_motion_mode", cancelled=True, mode="MANUAL")
            self.assertEqual((source[profiles.PROFILE_KEY], source[skirt_rig.RECORD_KEY], state(source)), before)
            self.assertEqual(skirt_rig.physics_control(source)[0]["physics_influence"], 1.0)

    def test_dialog_reads_current_native_values_and_cancel_does_not_write(self):
        self.cloth().settings.bending_stiffness = 3.1
        profile_before = self.source[profiles.PROFILE_KEY]
        native_before = self.cloth().settings.bending_stiffness
        calls = []
        wm = SimpleNamespace(as_pointer=bpy.context.window_manager.as_pointer,
                             character_designer_skirt=bpy.context.window_manager.character_designer_skirt,
                             invoke_props_dialog=lambda operator, **kwargs: calls.append((operator, kwargs)) or {"RUNNING_MODAL"})
        context = SimpleNamespace(window_manager=wm, view_layer=bpy.context.view_layer,
                                  selected_objects=bpy.context.selected_objects)
        operator = SimpleNamespace(report=lambda *_args: None)
        result = ui.CHARACTERDESIGNER_OT_dress_motion_tuning.invoke(operator, context, None)
        self.assertEqual(result, {"RUNNING_MODAL"})
        self.assertAlmostEqual(operator.bend, 3.1, places=5)
        self.assertEqual(operator._loaded_values["bend"], operator.bend)
        self.assertEqual(calls[0][1]["width"], 420)
        self.assertEqual(self.source[profiles.PROFILE_KEY], profile_before)
        self.assertEqual(self.cloth().settings.bending_stiffness, native_before)

    def test_dialog_confirmation_passes_only_changed_field_to_batch(self):
        profile = tuning.effective(self.source)
        values = dict(profile["settings"])
        operator = SimpleNamespace(**values, _source_ref=self.source,
                                   _identity=profile["identity"], _loaded_values=values,
                                   all_dresses=True, report=lambda *_args: None)
        operator.quality = values["quality"] + 1
        with patch.object(tuning, "apply", return_value=()) as apply:
            result = ui.CHARACTERDESIGNER_OT_dress_motion_tuning.execute(operator, bpy.context)
        self.assertEqual(result, {"FINISHED"})
        targets, changes = apply.call_args.args[1:]
        self.assertEqual(set(targets), set(self.sources[:2]))
        self.assertEqual(changes, {"quality": values["quality"] + 1})

    def test_reset_routes_to_native_reset_and_baked_tuning_reports_reset(self):
        with patch.object(skirt_physics, "reset_simulation") as reset:
            call("dress_motion_reset")
            reset.assert_called_once()
            self.assertEqual(reset.call_args.args[1], self.source)
        raw = self.source[profiles.PROFILE_KEY]
        before = state(self.source)
        with patch.object(tuning, "apply", side_effect=ValueError("Reset the baked Dress simulation before tuning its material.")):
            call("dress_motion_tuning", cancelled=True, bend=2.9)
        self.assertIn("Reset", bpy.context.window_manager.character_designer_skirt.last_message)
        self.assertEqual(self.source[profiles.PROFILE_KEY], raw)
        self.assertEqual(state(self.source), before)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DressMotionUITests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
