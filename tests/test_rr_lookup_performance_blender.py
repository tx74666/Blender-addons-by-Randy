"""Native lookup-scope regressions; generated fixtures in a disposable process.

Run Blender --background --factory-startup --disable-autoexec --threads 1
--python-exit-code 1 --python tests/test_rr_lookup_performance_blender.py.
Optional unittest names after -- select individual cases. No add-on registration,
timers, authored .blend, export writer, render, or Unity resource is used.
Call counts establish avoided work; they are not a wall-time or FPS benchmark.
"""

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
import random_realm_builder_exporter as rr


def empty_object(name, parent=None):
    obj = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    return obj


def mesh_object(name, parent=None):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 1)], [], [(0, 1, 2)])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    return obj


def assembly_object(name, parent=None):
    obj = empty_object(name, parent)
    obj[rr.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
    obj[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "lookup-assembly-" + name
    obj[rr.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = name
    obj[rr.OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = "ASSEMBLY"
    return obj


def collider_object(name, target=None, parent=None):
    obj = mesh_object(name, parent)
    obj["rr_builder_collision_helper"] = True
    if target is not None:
        obj["rr_collider_target"] = target.name
    return obj


def nested_fixture():
    root = assembly_object("LookupTop")
    direct = mesh_object("LookupDirect", root)
    nested = assembly_object("LookupNested", root)
    leaf = mesh_object("LookupLeaf", nested)
    detached = mesh_object("LookupDetached")
    detached[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = root[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP]
    internal_collider = collider_object("LookupInternalCollider", parent=nested)
    external_collider = collider_object("LookupExternalCollider", target=direct)
    hidden = mesh_object("LookupHidden", root)
    hidden.hide_set(True)
    locked = mesh_object("LookupLocked", root)
    locked.hide_select = True
    return SimpleNamespace(root=root, direct=direct, nested=nested, leaf=leaf,
                           detached=detached, internal_collider=internal_collider,
                           external_collider=external_collider, hidden=hidden,
                           locked=locked)


def native_snapshot():
    """Our fixture ID properties are scalar; include native authoring state too."""
    bpy.context.view_layer.update()
    return tuple((
        obj.as_pointer(), obj.name, obj.type,
        obj.parent.as_pointer() if obj.parent else None,
        tuple(sorted(obj.items())),
        tuple(tuple(row) for row in obj.matrix_world),
        obj.select_get(), obj.hide_get(), obj.hide_select, obj.hide_render,
        obj.data.as_pointer() if obj.data else None,
        tuple(tuple(vertex.co) for vertex in obj.data.vertices) if obj.type == "MESH" else (),
        tuple(tuple(face.vertices) for face in obj.data.polygons) if obj.type == "MESH" else (),
    ) for obj in bpy.data.objects)


class LookupPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.previous_repair = rr.OBJECT_MANAGER_REPAIR_ALLOWED
        self.previous_cache = rr._EXPORT_IDENTITY_LOOKUP_CACHE
        self.addCleanup(setattr, rr, "OBJECT_MANAGER_REPAIR_ALLOWED", self.previous_repair)
        self.addCleanup(setattr, rr, "_EXPORT_IDENTITY_LOOKUP_CACHE", self.previous_cache)
        rr.OBJECT_MANAGER_REPAIR_ALLOWED = True
        rr._EXPORT_IDENTITY_LOOKUP_CACHE = None
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in list(bpy.data.meshes):
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)

    def assert_scope_restored(self, repair=True, cache=None):
        self.assertIs(rr.OBJECT_MANAGER_REPAIR_ALLOWED, repair)
        self.assertIs(rr._EXPORT_IDENTITY_LOOKUP_CACHE, cache)

    def test_nested_scopes_share_cache_without_exposing_cached_lists(self):
        fixture = nested_fixture()
        with rr.export_identity_lookup_scope():
            cache = rr._EXPORT_IDENTITY_LOOKUP_CACHE
            first = rr.get_asset_meshes(fixture.root)
            expected = list(first)
            self.assertTrue(expected)
            first.clear()
            with rr.export_identity_lookup_scope():
                self.assertIs(rr._EXPORT_IDENTITY_LOOKUP_CACHE, cache)
                self.assertFalse(rr.OBJECT_MANAGER_REPAIR_ALLOWED)
                second = rr.get_asset_meshes(fixture.root)
                self.assertEqual(second, expected)
                self.assertIsNot(second, first)
                second.reverse()
            self.assertIs(rr._EXPORT_IDENTITY_LOOKUP_CACHE, cache)
            self.assertEqual(rr.get_asset_meshes(fixture.root), expected)
        self.assert_scope_restored()

    def test_exception_restores_all_prior_repair_and_cache_combinations(self):
        for repair in (True, False):
            for previous in (None, {"existing-sentinel": object()}):
                with self.subTest(repair=repair, prior_cache=previous is not None):
                    rr.OBJECT_MANAGER_REPAIR_ALLOWED = repair
                    rr._EXPORT_IDENTITY_LOOKUP_CACHE = previous
                    with self.assertRaisesRegex(RuntimeError, "scope fixture failure"):
                        with rr.export_identity_lookup_scope():
                            active = rr._EXPORT_IDENTITY_LOOKUP_CACHE
                            self.assertIsInstance(active, dict)
                            self.assertFalse(rr.OBJECT_MANAGER_REPAIR_ALLOWED)
                            if not repair and previous is not None:
                                self.assertIs(active, previous)
                            else:
                                self.assertIsNot(active, previous)
                            with rr.export_identity_lookup_scope():
                                self.assertIs(rr._EXPORT_IDENTITY_LOOKUP_CACHE, active)
                                raise RuntimeError("scope fixture failure")
                    self.assert_scope_restored(repair, previous)

    def test_newly_memoized_native_lookups_preserve_order_and_defensive_copies(self):
        fixture = nested_fixture()
        before = native_snapshot()
        for name in ("object_manager_member_objects", "object_manager_selection_objects",
                     "get_collision_meshes"):
            with self.subTest(lookup=name):
                lookup = getattr(rr, name)
                self.assertTrue(hasattr(lookup, "__wrapped__"))
                with mock.patch.object(rr, "OBJECT_MANAGER_REPAIR_ALLOWED", False):
                    expected = lookup.__wrapped__(fixture.root)
                self.assertTrue(expected)
                with rr.export_identity_lookup_scope():
                    actual = lookup(fixture.root)
                    self.assertEqual(actual, expected)
                    actual.clear()
                    self.assertEqual(lookup(fixture.root), expected)
                self.assert_scope_restored()
        selectable = rr.object_manager_selection_objects(fixture.root)
        self.assertIn(fixture.leaf, selectable)
        self.assertIn(fixture.detached, selectable)
        self.assertNotIn(fixture.hidden, selectable)
        self.assertNotIn(fixture.locked, selectable)
        self.assertEqual(native_snapshot(), before)

    def test_cached_collider_lookup_avoids_repeating_whole_scene_work(self):
        fixture = nested_fixture()
        with mock.patch.object(rr, "collider_name_matches_root",
                               wraps=rr.collider_name_matches_root) as match:
            with rr.export_identity_lookup_scope():
                expected = rr.get_collision_meshes(fixture.root)
                first_count = match.call_count
                self.assertGreater(first_count, 0)
                self.assertEqual(rr.get_collision_meshes(fixture.root), expected)
                self.assertEqual(match.call_count, first_count)
                with rr.export_identity_lookup_scope():
                    self.assertEqual(rr.get_collision_meshes(fixture.root), expected)
                    self.assertEqual(match.call_count, first_count)
            match.reset_mock()
            with mock.patch.object(rr, "OBJECT_MANAGER_REPAIR_ALLOWED", False):
                first = rr.get_collision_meshes.__wrapped__(fixture.root)
                uncached_count = match.call_count
                second = rr.get_collision_meshes.__wrapped__(fixture.root)
            self.assertEqual((first, second), (expected, expected))
            self.assertEqual(uncached_count, first_count)
            self.assertEqual(match.call_count, 2 * first_count)
        self.assert_scope_restored()

    def test_finished_scope_does_not_hide_a_new_object(self):
        fixture = nested_fixture()
        with rr.export_identity_lookup_scope():
            old_meshes = rr.get_asset_meshes(fixture.root)
        added = mesh_object("LookupAdded", fixture.root)
        self.assertNotIn(added, old_meshes)
        self.assertIn(added, rr.get_asset_meshes(fixture.root))
        with rr.export_identity_lookup_scope():
            self.assertIn(added, rr.get_asset_meshes(fixture.root))
        self.assert_scope_restored()

    def test_finished_scope_does_not_reuse_deleted_same_name_objects(self):
        root = empty_object("LookupReplacementRoot")
        child = mesh_object("LookupReplacementChild", root)
        name = child.name
        with rr.export_identity_lookup_scope():
            self.assertEqual(rr.get_asset_meshes(root), [child])
        bpy.data.objects.remove(child, do_unlink=True)
        replacement = mesh_object(name, root)
        replacement.data.vertices[0].co.x = 2.0
        with rr.export_identity_lookup_scope():
            meshes = rr.get_asset_meshes(root)
            self.assertEqual(meshes, [replacement])
            self.assertEqual(meshes[0].data.vertices[0].co.x, 2.0)
        self.assert_scope_restored()

    def test_finished_scope_does_not_hide_native_parent_changes(self):
        first = empty_object("LookupParentFirst")
        second = empty_object("LookupParentSecond")
        child = mesh_object("LookupReparented", first)
        with rr.export_identity_lookup_scope():
            self.assertEqual(rr.get_asset_meshes(first), [child])
            self.assertEqual(rr.get_asset_meshes(second), [])
        child.parent = second
        self.assertEqual(rr.get_asset_meshes(first), [])
        self.assertEqual(rr.get_asset_meshes(second), [child])
        with rr.export_identity_lookup_scope():
            self.assertEqual(rr.get_asset_meshes(first), [])
            self.assertEqual(rr.get_asset_meshes(second), [child])

    def test_finished_scope_does_not_hide_native_rename_collider_matching(self):
        root = mesh_object("LookupDoor")
        collider = mesh_object("LookupDoor_Collider")
        with rr.export_identity_lookup_scope():
            self.assertEqual(rr.get_collision_meshes(root), [collider])
        root.name = "LookupWindow"
        self.assertEqual(rr.get_collision_meshes(root), [])
        with rr.export_identity_lookup_scope():
            self.assertEqual(rr.get_collision_meshes(root), [])
        collider.name = "LookupWindow_Collider"
        self.assertEqual(rr.get_collision_meshes(root), [collider])

    def test_finished_scope_does_not_hide_custom_collider_target_changes(self):
        first = mesh_object("LookupTargetFirst")
        second = mesh_object("LookupTargetSecond")
        collider = collider_object("LookupMovableCollider", target=first)
        with rr.export_identity_lookup_scope():
            self.assertEqual(rr.get_collision_meshes(first), [collider])
            self.assertEqual(rr.get_collision_meshes(second), [])
        collider["rr_collider_target"] = second.name
        self.assertEqual(rr.get_collision_meshes(first), [])
        self.assertEqual(rr.get_collision_meshes(second), [collider])
        with rr.export_identity_lookup_scope():
            self.assertEqual(rr.get_collision_meshes(first), [])
            self.assertEqual(rr.get_collision_meshes(second), [collider])

    def test_finished_scope_does_not_hide_selection_visibility_changes(self):
        fixture = nested_fixture()
        with rr.export_identity_lookup_scope():
            self.assertIn(fixture.direct, rr.object_manager_selection_objects(fixture.root))
        fixture.direct.hide_set(True)
        fixture.hidden.hide_set(False)
        fixture.locked.hide_select = False
        with rr.export_identity_lookup_scope():
            selected = rr.object_manager_selection_objects(fixture.root)
            self.assertNotIn(fixture.direct, selected)
            self.assertIn(fixture.hidden, selected)
            self.assertIn(fixture.locked, selected)

    def test_conflict_validation_reads_new_previous_and_current_aliases(self):
        owner = mesh_object("LookupOwner")
        other = mesh_object("LookupOther")
        owner[rr.EXPORT_STABLE_ID_PROP] = "lookup-owner-stable"
        other[rr.EXPORT_STABLE_ID_PROP] = "lookup-other-stable"
        before = native_snapshot()
        self.assertEqual(rr.collect_export_identity_conflicts(owner), [])
        self.assertEqual(rr.validate_export_batch_identities([owner, other]), [owner, other])
        self.assertEqual(native_snapshot(), before)

        owner[rr.EXPORT_PREVIOUS_IDS_PROP] = json.dumps([other.name])
        conflicts = rr.collect_export_identity_conflicts(owner)
        self.assertTrue(any("previous ID" in item and other.name in item for item in conflicts))
        with self.assertRaisesRegex(RuntimeError, "Export identity conflict"):
            rr.validate_export_batch_identities([owner, other])
        self.assert_scope_restored()

        owner[rr.EXPORT_PREVIOUS_IDS_PROP] = "[]"
        self.assertEqual(rr.collect_export_identity_conflicts(owner), [])
        other[rr.EXPORT_ASSET_ID_OVERRIDE_PROP] = rr.export_asset_id(owner)
        self.assertTrue(any("current export ID" in item
                            for item in rr.collect_export_identity_conflicts(owner)))
        del other[rr.EXPORT_ASSET_ID_OVERRIDE_PROP]
        self.assertEqual(rr.validate_export_batch_identities([owner, other]), [owner, other])

    def test_batch_shares_one_scope_through_nested_live_root_discovery(self):
        first = mesh_object("LookupBatchFirst")
        second = mesh_object("LookupBatchSecond")
        observed = []
        original = rr.get_export_roots

        def inspect_scope(objects):
            self.assertFalse(rr.OBJECT_MANAGER_REPAIR_ALLOWED)
            self.assertIsInstance(rr._EXPORT_IDENTITY_LOOKUP_CACHE, dict)
            observed.append(rr._EXPORT_IDENTITY_LOOKUP_CACHE)
            return original(objects)

        with mock.patch.object(rr, "get_export_roots", side_effect=inspect_scope):
            self.assertEqual(rr.validate_export_batch_identities([second, None, first, second]),
                             [second, first, second])
        self.assertEqual(len(observed), 3)
        self.assertTrue(all(cache is observed[0] for cache in observed))
        self.assertTrue(observed[0])
        self.assert_scope_restored()

    def test_diagnostic_entrypoints_cannot_repair_legacy_group_metadata(self):
        incomplete = empty_object("LookupLegacyGroup")
        incomplete[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "lookup-legacy-id"
        incomplete[rr.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = incomplete.name
        child = mesh_object("LookupLegacyChild", incomplete)
        child[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "lookup-legacy-id"
        child[rr.OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = incomplete.name
        before = native_snapshot()
        operations = (
            lambda: rr.live_export_identity_roots([child]),
            lambda: rr.collect_export_identity_conflicts(child),
            lambda: rr.validate_export_batch_identities([child]),
        )
        with mock.patch.object(rr, "repair_object_manager_assembly_root",
                               side_effect=AssertionError("Read-only lookup attempted repair")) as repair:
            for operation in operations:
                operation()
                self.assert_scope_restored()
                self.assertEqual(native_snapshot(), before)
            with rr.export_identity_lookup_scope():
                self.assertIsNone(rr.object_manager_assembly_root_for_object(child))
            repair.assert_not_called()

    def test_live_root_filtering_and_conflict_failure_restore_scope(self):
        root = mesh_object("LookupFailureRoot")

        def fail_inside_scope(message):
            self.assertFalse(rr.OBJECT_MANAGER_REPAIR_ALLOWED)
            self.assertIsInstance(rr._EXPORT_IDENTITY_LOOKUP_CACHE, dict)
            raise RuntimeError(message)

        with mock.patch.object(rr, "get_export_asset_meshes",
                               side_effect=lambda *_args: fail_inside_scope("final root filter failure")):
            with self.assertRaisesRegex(RuntimeError, "final root filter failure"):
                rr.live_export_identity_roots([root])
        self.assert_scope_restored()
        with mock.patch.object(rr, "export_previous_ids",
                               side_effect=lambda *_args: fail_inside_scope("conflict alias failure")):
            with self.assertRaisesRegex(RuntimeError, "conflict alias failure"):
                rr.collect_export_identity_conflicts(root)
        self.assert_scope_restored()
        with mock.patch.object(rr, "validate_export_identity",
                               side_effect=lambda *_args: fail_inside_scope("batch fixture failure")):
            with self.assertRaisesRegex(RuntimeError, "batch fixture failure"):
                rr.validate_export_batch_identities([root])
        self.assert_scope_restored()

    def test_depsgraph_callbacks_are_uncached_and_inflight_results_are_not_retained(self):
        first = empty_object("LookupCallbackFirst")
        second = empty_object("LookupCallbackSecond")
        child = mesh_object("LookupCallbackChild", first)
        source = mock.Mock(name="empty_generated_source")
        source.data.vertices = []
        phases = []
        computations = []

        def observe(phase):
            phases.append(phase)
            self.assertIsNone(rr._EXPORT_IDENTITY_LOOKUP_CACHE)
            self.assertFalse(rr.OBJECT_MANAGER_REPAIR_ALLOWED)

        def evaluated(_graph):
            observe("evaluated_get")
            self.assertEqual(rr.get_asset_meshes(first), [])
            return SimpleNamespace(data=SimpleNamespace(vertices=[]))

        def instances():
            observe("object_instances")
            yield SimpleNamespace(is_instance=True, parent=SimpleNamespace(original=source),
                                  object=SimpleNamespace(type="MESH",
                                                         data=SimpleNamespace(vertices=[object()])))

        def get_depsgraph():
            observe("evaluated_depsgraph_get")
            child.parent = second
            return SimpleNamespace(object_instances=instances())

        source.evaluated_get.side_effect = evaluated
        mocked_bpy = SimpleNamespace(data=bpy.data, types=bpy.types,
                                     context=SimpleNamespace(evaluated_depsgraph_get=get_depsgraph))

        @rr._memoize_export_identity_lookup
        def in_flight_membership():
            computations.append(True)
            before_callback = rr.get_asset_meshes(first)
            self.assertTrue(rr.mesh_objects_have_export_geometry([source]))
            return before_callback

        with rr.export_identity_lookup_scope():
            cache = rr._EXPORT_IDENTITY_LOOKUP_CACHE
            self.assertEqual(rr.get_asset_meshes(first), [child])
            self.assertTrue(cache)
            with mock.patch.object(rr, "bpy", mocked_bpy):
                # A result captured before callbacks may finish, but must never
                # repopulate the restored dictionary after its epoch was cleared.
                self.assertEqual(in_flight_membership(), [child])
                self.assertIs(rr._EXPORT_IDENTITY_LOOKUP_CACHE, cache)
                self.assertEqual(cache, {})
                self.assertEqual(in_flight_membership(), [])
                self.assertEqual(cache, {})
            self.assertEqual(len(computations), 2)
            self.assertEqual(rr.get_asset_meshes(first), [])
            self.assertEqual(rr.get_asset_meshes(second), [child])
        self.assertEqual(phases, ["evaluated_depsgraph_get", "evaluated_get", "object_instances"] * 2)
        self.assert_scope_restored()

    def test_depsgraph_callback_failure_clears_and_restores_the_outer_cache(self):
        first = empty_object("LookupFailureFirst")
        second = empty_object("LookupFailureSecond")
        child = mesh_object("LookupFailureChild", first)
        source = mock.Mock(name="failing_empty_source")
        source.data.vertices = []
        failure = RuntimeError("depsgraph callback fixture failure")

        def get_depsgraph():
            self.assertIsNone(rr._EXPORT_IDENTITY_LOOKUP_CACHE)
            self.assertFalse(rr.OBJECT_MANAGER_REPAIR_ALLOWED)
            child.parent = second
            raise failure

        mocked_bpy = SimpleNamespace(context=SimpleNamespace(evaluated_depsgraph_get=get_depsgraph))
        with rr.export_identity_lookup_scope():
            cache = rr._EXPORT_IDENTITY_LOOKUP_CACHE
            self.assertEqual(rr.get_asset_meshes(first), [child])
            self.assertTrue(cache)
            with mock.patch.object(rr, "bpy", mocked_bpy):
                with self.assertRaisesRegex(RuntimeError, "Could not verify generated export geometry") as error:
                    rr.mesh_objects_have_export_geometry([source])
            self.assertIs(error.exception.__cause__, failure)
            self.assertIs(rr._EXPORT_IDENTITY_LOOKUP_CACHE, cache)
            self.assertFalse(rr.OBJECT_MANAGER_REPAIR_ALLOWED)
            self.assertEqual(cache, {})
            self.assertEqual(rr.get_asset_meshes(first), [])
            self.assertEqual(rr.get_asset_meshes(second), [child])
        source.evaluated_get.assert_not_called()
        self.assert_scope_restored()

    def test_existing_native_vertices_skip_evaluation_without_invalidating_cache(self):
        root = empty_object("LookupFastRoot")
        child = mesh_object("LookupFastChild", root)
        source = mock.Mock(name="nonempty_native_source")
        source.data.vertices = [object()]
        getter = mock.Mock(side_effect=AssertionError("Native vertices must skip dependency evaluation"))
        mocked_bpy = SimpleNamespace(context=SimpleNamespace(evaluated_depsgraph_get=getter))
        with rr.export_identity_lookup_scope():
            cache = rr._EXPORT_IDENTITY_LOOKUP_CACHE
            self.assertEqual(rr.get_asset_meshes(root), [child])
            entries = dict(cache)
            with mock.patch.object(rr, "bpy", mocked_bpy):
                self.assertTrue(rr.mesh_objects_have_export_geometry([source]))
                self.assertFalse(rr.mesh_objects_have_export_geometry([]))
            self.assertIs(rr._EXPORT_IDENTITY_LOOKUP_CACHE, cache)
            self.assertEqual(cache, entries)
        getter.assert_not_called()
        source.evaluated_get.assert_not_called()
        self.assert_scope_restored()

    def test_nested_suspension_restores_none_then_the_cleared_outer_cache(self):
        root = empty_object("LookupNestedSuspendRoot")
        child = mesh_object("LookupNestedSuspendChild", root)
        with rr.export_identity_lookup_scope():
            outer_cache = rr._EXPORT_IDENTITY_LOOKUP_CACHE
            self.assertEqual(rr.get_asset_meshes(root), [child])
            self.assertTrue(outer_cache)
            with rr.suspend_export_identity_lookup_cache():
                self.assertIsNone(rr._EXPORT_IDENTITY_LOOKUP_CACHE)
                self.assertFalse(rr.OBJECT_MANAGER_REPAIR_ALLOWED)
                self.assertEqual(outer_cache, {})
                with rr.suspend_export_identity_lookup_cache():
                    self.assertIsNone(rr._EXPORT_IDENTITY_LOOKUP_CACHE)
                    self.assertFalse(rr.OBJECT_MANAGER_REPAIR_ALLOWED)
                    # A retained alias must also be cleared when the outer
                    # suspension ends, even though the nested prior cache is None.
                    outer_cache["retained-alias-write"] = [child]
                self.assertIsNone(rr._EXPORT_IDENTITY_LOOKUP_CACHE)
                self.assertFalse(rr.OBJECT_MANAGER_REPAIR_ALLOWED)
                self.assertIn("retained-alias-write", outer_cache)
            self.assertIs(rr._EXPORT_IDENTITY_LOOKUP_CACHE, outer_cache)
            self.assertEqual(outer_cache, {})
            self.assertFalse(rr.OBJECT_MANAGER_REPAIR_ALLOWED)
        self.assert_scope_restored()

    def test_reentrant_conflict_diagnostic_owns_a_separate_suspended_lookup_scope(self):
        root = mesh_object("LookupReentrantRoot")
        mesh_object("LookupReentrantOther")
        observed = []
        original = rr.get_export_roots

        def inspect_reentrant_scope(objects):
            self.assertFalse(rr.OBJECT_MANAGER_REPAIR_ALLOWED)
            current = rr._EXPORT_IDENTITY_LOOKUP_CACHE
            self.assertIsInstance(current, dict)
            self.assertIsNot(current, outer_cache)
            observed.append(current)
            return original(objects)

        with rr.export_identity_lookup_scope():
            outer_cache = rr._EXPORT_IDENTITY_LOOKUP_CACHE
            self.assertEqual(rr.get_asset_meshes(root), [root])
            self.assertTrue(outer_cache)
            with rr.suspend_export_identity_lookup_cache():
                self.assertIsNone(rr._EXPORT_IDENTITY_LOOKUP_CACHE)
                self.assertEqual(outer_cache, {})
                with mock.patch.object(rr, "get_export_roots", side_effect=inspect_reentrant_scope):
                    self.assertEqual(rr.collect_export_identity_conflicts(root), [])
                # The diagnostic's own synchronous lookup window must finish
                # before suspension resumes; it does not replace the outer cache.
                self.assertIsNone(rr._EXPORT_IDENTITY_LOOKUP_CACHE)
                self.assertFalse(rr.OBJECT_MANAGER_REPAIR_ALLOWED)
                self.assertEqual(outer_cache, {})
            self.assertEqual(len(observed), 1)
            self.assertTrue(observed[0])
            self.assertIs(rr._EXPORT_IDENTITY_LOOKUP_CACHE, outer_cache)
            self.assertEqual(outer_cache, {})
            self.assertFalse(rr.OBJECT_MANAGER_REPAIR_ALLOWED)
        self.assert_scope_restored()

    def test_export_roots_preserve_first_seen_order_and_exclude_descendants(self):
        first = mesh_object("LookupOrderFirst")
        second = mesh_object("LookupOrderSecond")
        container = empty_object("LookupOrderContainer")
        child = mesh_object("LookupOrderChild", container)
        collider = collider_object("LookupOrderCollider", target=first)
        for objects, expected in (
            ([second, child, None, first, container, second, collider], [second, first, container]),
            ([child, container, first, second, child], [container, first, second]),
            ([first, second, first], [first, second]),
        ):
            with self.subTest(order=[obj.name if obj else None for obj in objects]):
                with rr.export_identity_lookup_scope():
                    self.assertEqual(rr.get_export_roots(objects), expected)


def main():
    if not bpy.app.background or "--factory-startup" not in sys.argv or bpy.data.filepath:
        raise RuntimeError("Use a disposable --background --factory-startup Blender process.")
    if hasattr(bpy.types.Scene, "rr_builder_export_settings"):
        raise RuntimeError("Run lookup regressions without an already registered add-on.")
    canonical = ROOT / "addons" / "random_realm_builder_exporter" / "__init__.py"
    if Path(rr.__file__).resolve() != canonical.resolve():
        raise RuntimeError("Import the canonical repository add-on source.")
    names = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    suite = (unittest.defaultTestLoader.loadTestsFromNames(names, sys.modules[__name__]) if names
             else unittest.defaultTestLoader.loadTestsFromTestCase(LookupPerformanceTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("RR lookup performance regressions failed.")
    print(f"RR_LOOKUP_PERFORMANCE_BLENDER_PASS tests={result.testsRun}")


if __name__ == "__main__":
    main()
