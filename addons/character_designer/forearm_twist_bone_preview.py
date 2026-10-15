"""Experimental paired-bone backend for an explicitly disposable preview.

This module has no registration, handlers, operators, export, or save entry point.
The pure plan expresses the existing post-skin target, not its float32 inverse
Shape Key implementation. Native construction is restricted to background
Blender and independent mesh/rig copies. Native geometry equivalence and timing
remain mandatory before this experiment can become an artist playback backend.

For a sample with ratio r and influence a, the additional deform bones implement
L @ R(r*theta) and H @ R((r-1)*theta). Their weights are a*wL and a*wH; the
original terms keep (1-a)*wL and (1-a)*wH. All unrelated contributions remain.
This preserves wrist swing and translation, unlike a single lower-follow bone.
"""

import ast
import json
import math
import re
import uuid
from dataclasses import dataclass, replace
from weakref import WeakKeyDictionary, WeakSet, WeakValueDictionary


RECORD_KEY = "character_designer_forearm_twist_v1"
OWNER_KEY = "character_designer_forearm_bone_preview_owner"
MAX_ANGLE = math.radians(120.0)
_ISSUED_PLANS = WeakValueDictionary()
_PLAN_TEMPLATES = WeakKeyDictionary()
_BUILT_PREVIEWS = WeakSet()
MATRIX_TOLERANCE = 2e-6
_BONE_PATH = re.compile(r"(?:pose\.)?bones\[((?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'))\]")


class BonePreviewError(ValueError):
    """The isolated input cannot safely use this experimental backend."""


def _require(condition, message):
    if not condition:
        raise BonePreviewError(message)


def _check_identity_rest(matrix):
    expected = tuple(tuple(float(row == column) for column in range(4)) for row in range(4))
    _require(tuple(tuple(row) for row in matrix) == expected,
             "The native identity-Rest experiment did not create an exact identity matrix")


def _number(value, label):
    _require(isinstance(value, (int, float)) and not isinstance(value, bool),
             label + " must be a finite number")
    value = float(value)
    _require(math.isfinite(value), label + " must be finite")
    return value


def _name(value, label):
    _require(isinstance(value, str) and bool(value), label + " must be nonempty")
    return value


def _vector(value, label):
    _require(isinstance(value, (tuple, list)) and len(value) == 3,
             label + " must have three components")
    return tuple(_number(item, label) for item in value)


@dataclass(frozen=True)
class Side:
    name: str
    lower: str
    hand: str
    axis: tuple
    pivot: tuple
    enabled: bool
    samples: tuple  # (vertex index, ratio, influence)


@dataclass(frozen=True)
class Helper:
    key: tuple  # (side name, lower/hand role, exact sampled ratio)
    source_bone: str
    fraction: float
    axis: tuple
    pivot: tuple


@dataclass(frozen=True)
class Rewrite:
    index: int
    source_weights: tuple  # exact source normalized deform weights, native order
    target_weights: tuple  # original name or Helper.key, normalized amount
    transfers: tuple  # original name, Helper.key, influence


@dataclass(frozen=True, eq=False)
class Plan:
    # Native issuance is object identity, including the WeakKeyDictionary key.
    # Separate captures can produce equal numeric fields with different trusted
    # certificates; they must never alias or share weak-key cleanup.
    vertex_count: int
    sides: tuple
    helpers: tuple
    rewrites: tuple
    managed_key_names: tuple
    record_text: str
    source_ids: tuple = ()


def build_plan(sides, weights, *, vertex_count, managed_key_names=(), record_text=""):
    """Compile immutable validated source samples without reading Blender.

    Callers supply normalized *all-deform* weights from the authoring preflight.
    The native builder independently checks them against its source and clone.
    Ratios are never quantized, and two close ratios never share a helper.
    """
    _require(isinstance(vertex_count, int) and not isinstance(vertex_count, bool)
             and vertex_count > 0, "Vertex count must be a positive integer")
    weight_rows = tuple(weights.items()) if isinstance(weights, dict) else tuple(weights)
    by_index = {}
    for index, row in weight_rows:
        _require(isinstance(index, int) and not isinstance(index, bool)
                 and 0 <= index < vertex_count and index not in by_index,
                 "Weights need unique in-range vertex indices")
        row = tuple(row.items()) if isinstance(row, dict) else tuple(row)
        clean, names = [], set()
        for name, amount in row:
            name = _name(name, "Deform bone")
            amount = _number(amount, "Deform weight")
            _require(amount > 0.0 and name not in names,
                     "Weights must be positive and have unique bone names")
            names.add(name)
            clean.append((name, amount))
        _require(clean and abs(sum(amount for _name_, amount in clean) - 1.0) <= 1e-4,
                 "Weights must be normalized across every deform influence")
        by_index[index] = tuple(clean)

    frozen_sides, helpers, rewrites = [], {}, []
    claimed, side_names = set(), set()
    for source in tuple(sides):
        name = _name(source.name, "Side")
        lower, hand = _name(source.lower, "Lower bone"), _name(source.hand, "Hand bone")
        _require(name not in side_names and lower != hand, "Sides and chain roles must be unique")
        side_names.add(name)
        axis, pivot = _vector(source.axis, "Axis"), _vector(source.pivot, "Pivot")
        axis_length = math.hypot(*axis)
        _require(math.isfinite(axis_length) and axis_length >= 1e-6, "Axis has invalid length")
        _require(isinstance(source.enabled, bool), "Side enabled must be a boolean")
        samples = []
        for index, ratio, influence in tuple(source.samples):
            _require(isinstance(index, int) and not isinstance(index, bool)
                     and 0 <= index < vertex_count and index not in claimed,
                     "Samples must be disjoint and have in-range indices")
            _require(index in by_index, "A sample is missing its source deform weights")
            claimed.add(index)
            ratio, influence = _number(ratio, "Ratio"), _number(influence, "Influence")
            _require(0.0 <= ratio <= 1.0 and 0.0 <= influence <= 1.0,
                     "Ratio and influence must be in [0, 1]")
            samples.append((index, ratio, influence))
            if not source.enabled or influence == 0.0:
                continue
            targets, transfers = [], []
            for bone_name, amount in by_index[index]:
                role = "lower" if bone_name == lower else "hand" if bone_name == hand else None
                fraction = ratio if role == "lower" else ratio - 1.0 if role == "hand" else 0.0
                if role is None or fraction == 0.0:
                    targets.append((bone_name, amount))
                    continue
                key = (name, role, ratio)
                helpers.setdefault(key, Helper(key, bone_name, fraction, axis, pivot))
                if influence < 1.0:
                    targets.append((bone_name, amount * (1.0 - influence)))
                targets.append((key, amount * influence))
                transfers.append((bone_name, key, influence))
            if transfers:
                rewrites.append(Rewrite(index, by_index[index], tuple(targets), tuple(transfers)))
        _require(bool(samples), "Each side needs at least one captured sample")
        frozen_sides.append(Side(name, lower, hand, axis, pivot, source.enabled, tuple(samples)))
    _require(bool(frozen_sides), "At least one captured side is required")
    names = tuple(_name(name, "Managed key") for name in managed_key_names)
    _require(len(set(names)) == len(names), "Managed key names must be unique")
    _require(isinstance(record_text, str), "Record text must be a string")
    return Plan(vertex_count, tuple(frozen_sides), tuple(helpers.values()), tuple(rewrites), names, record_text)


def plan_from_template(template, *, vertex_count=None):
    """Read the playback core's frozen validated template without consuming it."""
    from . import forearm_twist_playback_core as core
    core.check_template(template)
    if vertex_count is None:
        vertex_count = template.vertex_count
    _require(vertex_count == template.vertex_count, "Template vertex count differs")
    record_text = template.record_text
    _require(isinstance(record_text, str) and bool(record_text), "A captured calibration is required")
    try:
        records = json.loads(record_text)
        names = tuple(record["key"] for record in records.values())
    except (TypeError, ValueError, KeyError, AttributeError) as error:
        raise BonePreviewError("Captured calibration is malformed") from error
    plan = replace(build_plan(template.sides, template.weights, vertex_count=vertex_count,
                              managed_key_names=names, record_text=record_text),
                   source_ids=template.source_ids)
    _ISSUED_PLANS[id(plan)] = plan
    _PLAN_TEMPLATES[plan] = template
    return plan


def discard_plan(plan):
    """Idempotently release an unused exact plan and its trusted template."""
    if type(plan) is Plan and _ISSUED_PLANS.get(id(plan)) is plan:
        _ISSUED_PLANS.pop(id(plan), None)
        _PLAN_TEMPLATES.pop(plan, None)


def _consume_plan(plan):
    issued = _ISSUED_PLANS.pop(id(plan), None)
    _require(type(plan) is Plan and issued is plan,
             "Use an unused plan_from_template result for native construction")
    template = _PLAN_TEMPLATES.pop(plan, None)
    _require(template is not None, "The trusted template beside this plan is missing")
    return template


def check_modifier_types(types):
    """Only Armature followed by Subsurf has no downstream group consumer."""
    types = tuple(types)
    _require(bool(types) and types[0] == "ARMATURE"
             and all(kind == "SUBSURF" for kind in types[1:]),
             "Bone preview supports Armature followed only by Subsurf")


def check_helper_paths(names, paths):
    """Reject orphan Action/NLA/driver paths before new bones make them valid."""
    names = frozenset(names)
    for path in tuple(paths):
        _require(isinstance(path, str), "Animation paths must be strings")
        for match in _BONE_PATH.finditer(path):
            try:
                target = ast.literal_eval(match.group(1))
            except (ValueError, SyntaxError) as error:
                raise BonePreviewError("Unsupported quoted bone animation path") from error
            _require(target not in names, "A prospective helper is referenced by an existing animation path: " + path)


def _helper_names(plan, occupied_bones, occupied_groups):
    occupied = set(occupied_bones) | set(occupied_groups)
    names = {}
    for number, helper in enumerate(plan.helpers, 1):
        candidate = "Forearm Preview %s %s %02d" % (helper.key[0], helper.key[1].title(), number)
        while candidate in occupied:
            number += 1
            candidate = "Forearm Preview %s %s %02d" % (helper.key[0], helper.key[1].title(), number)
        _require(len(candidate.encode("utf-8")) <= 63, "A prospective helper name exceeds the native bone-name limit")
        occupied.add(candidate)
        names[helper.key] = candidate
    return names


def _existing_animation_paths(rig):
    from .limb_ik import _fcurves_for_action
    paths, actions = [], {}

    def strips(items):
        for strip in items:
            action = getattr(strip, "action", None)
            if action is not None:
                actions[int(action.as_pointer())] = action
            children = getattr(strip, "strips", None)
            if children is not None:
                strips(children)

    for owner in (rig, rig.data):
        animation = owner.animation_data
        if animation is None:
            continue
        if animation.action is not None:
            actions[int(animation.action.as_pointer())] = animation.action
        for track in animation.nla_tracks:
            strips(track.strips)
        for curve in animation.drivers:
            paths.append(curve.data_path)
            for variable in curve.driver.variables:
                for target in variable.targets:
                    if target.id in (rig, rig.data):
                        paths.append(target.data_path)
                        if target.bone_target:
                            paths.append("pose.bones[" + json.dumps(target.bone_target) + "].matrix")
    for action in actions.values():
        paths.extend(curve.data_path for curve in _fcurves_for_action(action))
    return tuple(paths)


def matrix_difference(actual, expected, *, tolerance=MATRIX_TOLERANCE):
    """Small scalar matrix oracle, also usable in pure tests."""
    tolerance = _number(tolerance, "Matrix tolerance")
    _require(0.0 <= tolerance <= MATRIX_TOLERANCE,
             "Matrix oracle tolerance cannot be relaxed")
    actual, expected = tuple(tuple(row) for row in actual), tuple(tuple(row) for row in expected)
    _require(len(actual) == len(expected) == 4
             and all(len(row) == 4 for row in actual + expected), "Expected finite 4x4 matrices")
    pairs = tuple((_number(a, "Matrix value"), _number(b, "Matrix value"))
                  for left, right in zip(actual, expected) for a, b in zip(left, right))
    differences = tuple(abs(a - b) for a, b in pairs)
    return {"exact": all(a == b for a, b in pairs), "max_abs": max(differences),
            "rms": math.sqrt(sum(value * value for value in differences) / 16.0),
            "tolerance": tolerance, "within_tolerance": max(differences) <= tolerance}


def check_scene_ownership(scene_pointer, mesh_scenes, rig_scenes, members, originals):
    """Pure ownership/exclusion gate for a separately built preview scene."""
    _require(isinstance(scene_pointer, int) and not isinstance(scene_pointer, bool)
             and scene_pointer > 0, "An explicit isolated scene identity is required")
    _require(set(mesh_scenes) == set(rig_scenes) == {scene_pointer},
             "Preview mesh and rig must belong exclusively to the same isolated scene")
    _require(not (set(members) & set(originals)), "Original source objects are present in the preview scene")


def _native_weights(mesh, rig, index):
    names = {group.index: group.name for group in mesh.vertex_groups
             if group.name in rig.data.bones and rig.data.bones[group.name].use_deform}
    raw = tuple((names[group.group], float(group.weight))
                for group in mesh.data.vertices[index].groups
                if group.group in names and group.weight > 0.0)
    total = sum(amount for _name_, amount in raw)
    _require(raw and total > 0.0, "A captured vertex has no current deform weights")
    return raw, tuple((name, amount / total) for name, amount in raw)


def _clone_guard(mesh, rig, source_mesh, source_rig, plan, disposable, isolated_scene):
    import bpy
    _require(disposable is True and bpy.app.background,
             "Bone preview construction is restricted to explicitly disposable background scenes")
    _require(isolated_scene is not None and isinstance(isolated_scene, bpy.types.Scene),
             "An explicit isolated scene is required")
    _require(mesh.type == source_mesh.type == "MESH" and rig.type == source_rig.type == "ARMATURE",
             "Expected mesh and armature source/clone pairs")
    _require(mesh != source_mesh and rig != source_rig and mesh.data != source_mesh.data
             and rig.data != source_rig.data, "Source and preview must own independent datablocks")
    _require(mesh.matrix_world == source_mesh.matrix_world and rig.matrix_world == source_rig.matrix_world,
             "Clone/source object transforms differ")
    _require(mesh.library is None and rig.library is None and mesh.data.library is None
             and rig.data.library is None and mesh.override_library is None
             and rig.override_library is None, "Linked or override preview data is unsupported")
    _require(all(obj == mesh for obj in bpy.data.objects if obj.type == "MESH" and obj.data == mesh.data)
             and all(obj == rig for obj in bpy.data.objects if obj.type == "ARMATURE" and obj.data == rig.data),
             "Preview mesh/rig data has another object owner")
    _require(len(mesh.data.vertices) == len(source_mesh.data.vertices) == plan.vertex_count,
             "Preview/source vertex counts changed")
    keys, source_keys = mesh.data.shape_keys, source_mesh.data.shape_keys
    _require(keys is not None and source_keys is not None and keys != source_keys and keys.use_relative,
             "Preview requires an independent relative Shape Key datablock")
    _require(keys.library is None, "Linked preview Shape Keys are unsupported")
    current_source_ids = tuple((getattr(item, "session_uid", None), int(item.as_pointer()))
                               for item in (source_mesh, source_mesh.data, source_rig,
                                            source_rig.data, source_keys))
    _require(current_source_ids == plan.source_ids, "Source identities differ from captured template")
    _require(not mesh.show_only_shape_key, "Pinned Shape Key preview is unsupported")
    _require(OWNER_KEY not in mesh and OWNER_KEY not in rig, "Preview has already been built")
    _require(plan.record_text and source_mesh.get(RECORD_KEY) == plan.record_text,
             "Authoritative source calibration changed after capture")
    _require(bool(plan.managed_key_names) and all(name in keys.key_blocks for name in plan.managed_key_names),
             "Managed keys are missing on the clone")
    _require(all(key.relative_key.name not in plan.managed_key_names for key in keys.key_blocks
                 if key.name not in plan.managed_key_names),
             "Foreign Shape Keys relative to a managed corrective need a separate dependency backend")
    check_modifier_types(modifier.type for modifier in mesh.modifiers)
    mod = mesh.modifiers[0]
    _require(sum(item.type == "ARMATURE" for item in mesh.modifiers) == 1
             and mod.object in (source_rig, rig) and mod.use_vertex_groups
             and not (mod.use_bone_envelopes or mod.use_deform_preserve_volume
                      or mod.use_multi_modifier or mod.vertex_group)
             and mod.show_viewport and mod.show_render,
             "Only ordinary unmasked LBS is supported")
    _require(mesh.mode == rig.mode == "OBJECT" and bpy.context.mode == "OBJECT",
             "Construct the disposable preview in Object Mode")
    _require(mesh.name in bpy.context.view_layer.objects and rig.name in bpy.context.view_layer.objects,
             "Preview objects must be in the active disposable view layer")
    _require(bpy.context.scene == isolated_scene,
             "The active background context must use the explicit isolated scene")
    original_pointers = {int(source_mesh.as_pointer()), int(source_rig.as_pointer())}
    for scene in set(source_mesh.users_scene) | set(source_rig.users_scene):
        original_pointers.update(int(obj.as_pointer()) for obj in scene.objects)
    check_scene_ownership(int(isolated_scene.as_pointer()),
                          (int(scene.as_pointer()) for scene in mesh.users_scene),
                          (int(scene.as_pointer()) for scene in rig.users_scene),
                          (int(obj.as_pointer()) for obj in isolated_scene.objects), original_pointers)
    for side in plan.sides:
        _require(side.lower in rig.data.bones and side.hand in rig.data.bones,
                 "A captured chain bone is missing")
        for index, _ratio, _influence in side.samples:
            expected = next((row.source_weights for row in plan.rewrites if row.index == index), None)
            if expected is None:
                continue
            _require(_native_weights(source_mesh, source_rig, index)[1] == expected
                     and _native_weights(mesh, rig, index)[1] == expected,
                     "Source or clone weights changed after capture")
            _require(all(rig.data.bones[name].bbone_segments == 1 for name, _weight in expected),
                     "Segmented B-Bone influences are unsupported")
    return frozenset(original_pointers)


def _preserve_shape_masks(mesh, rig, rewrites, managed_names):
    """Copy affected foreign masks before transferring clone deform weights."""
    affected = {name for rewrite in rewrites for name, _key, _a in rewrite.transfers}
    keys = mesh.data.shape_keys.key_blocks
    mask_names = {key.vertex_group for key in keys
                  if key.name not in managed_names and key.vertex_group in affected}
    remapping = {}
    for number, name in enumerate(sorted(mask_names), 1):
        source = mesh.vertex_groups.get(name)
        _require(source is not None, "A foreign Shape Key mask is missing")
        candidate = "Forearm Preview Mask %02d" % number
        while candidate in mesh.vertex_groups or candidate in rig.data.bones:
            number += 1
            candidate = "Forearm Preview Mask %02d" % number
        target = mesh.vertex_groups.new(name=candidate)
        _require(target.name not in rig.data.bones,
                 "A copied foreign mask must remain a non-deform group")
        for vertex in mesh.data.vertices:
            amount = next((float(item.weight) for item in vertex.groups if item.group == source.index), 0.0)
            if amount > 0.0:
                target.add([vertex.index], amount, "REPLACE")
        remapping[name] = target.name
    for key in keys:
        if key.name not in managed_names and key.vertex_group in remapping:
            key.vertex_group = remapping[key.vertex_group]
    return remapping


class IsolatedBonePreview:
    """Owned helper matrices in a fixed disposable clone; no implicit updates."""

    def __init__(self, mesh, rig, plan, token, helpers, mask_copies, isolated_scene, original_pointers):
        self.mesh, self.rig, self.plan, self.token = mesh, rig, plan, token
        self.helper_names = dict(helpers)
        self.mask_copies = dict(mask_copies)
        self._scene = isolated_scene
        self._scene_pointer = int(isolated_scene.as_pointer())
        self._original_pointers = original_pointers
        self._ids = tuple((getattr(item, "session_uid", None), int(item.as_pointer()))
                          for item in (mesh, mesh.data, mesh.data.shape_keys, rig, rig.data, isolated_scene))
        self._rest = {key: rig.data.bones[name].matrix_local.copy() for key, name in helpers.items()}
        self._pointers = {key: rig.data.bones[name].as_pointer() for key, name in helpers.items()}
        self._closed = self._failed = False
        self._verification_pending = False
        self._desired_deformations = None
        self.last_error = None
        self.last_basis_report = self.last_verification = None

    def close(self):
        """Release controller-only state; the caller disposes its private scene."""
        _BUILT_PREVIEWS.discard(self)
        self._closed = True
        self._desired_deformations = None

    def _guard(self):
        check_built_preview(self)
        _require(tuple((getattr(item, "session_uid", None), int(item.as_pointer()))
                       for item in (self.mesh, self.mesh.data, self.mesh.data.shape_keys,
                                    self.rig, self.rig.data, self._scene)) == self._ids,
                 "Preview native identities changed")
        _require(self.mesh.get(OWNER_KEY) == self.token and self.rig.get(OWNER_KEY) == self.token,
                 "Preview ownership changed")
        _require(self.mesh.mode == self.rig.mode == "OBJECT", "Preview editing is unsupported")
        check_scene_ownership(self._scene_pointer,
                              (int(scene.as_pointer()) for scene in self.mesh.users_scene),
                              (int(scene.as_pointer()) for scene in self.rig.users_scene),
                              (int(obj.as_pointer()) for obj in self._scene.objects), self._original_pointers)

    def _helper(self, helper):
        name = self.helper_names[helper.key]
        bone, pose = self.rig.data.bones.get(name), self.rig.pose.bones.get(name)
        _require(bone is not None and pose is not None and bone.as_pointer() == self._pointers[helper.key]
                 and bone.parent is None and bone.get(OWNER_KEY) == self.token,
                 "Owned helper identity or parent changed")
        _require(bone.matrix_local == self._rest[helper.key] and not pose.constraints
                 and pose.rotation_mode == "QUATERNION",
                 "Owned helper rest, constraints or rotation mode changed")
        return bone, pose

    def update_from_deformations(self, transforms):
        """Set helper basis from evaluated armature-space source deformations.

        Read/evaluate original lower and hand first, call this method, then let
        Blender evaluate the clone. This method never seeks, updates a depsgraph,
        reads vertices, writes keys/groups, or installs a callback.
        """
        try:
            self._guard()
            _require(not self._verification_pending,
                     "Verify the evaluated helpers before advancing to another update")
            return self._update_from_deformations(transforms)
        except Exception as error:
            self._failed = True
            self.last_error = str(error)
            raise

    def _update_from_deformations(self, transforms):
        from mathutils import Matrix, Quaternion, Vector
        from .forearm_twist_math import TwistMathError, twist_angle
        angles = {}
        for side in self.plan.sides:
            if not side.enabled:
                angles[side.name] = 0.0
                continue
            _require(side.lower in transforms and side.hand in transforms,
                     "A current deformation matrix is missing")
            try:
                angle = twist_angle(transforms[side.lower], transforms[side.hand], side.axis)
            except (TwistMathError, ValueError, TypeError) as error:
                raise BonePreviewError("Current deformation is unsupported: " + str(error)) from error
            _require(abs(angle) <= MAX_ANGLE + 1e-5, "Twist exceeds the calibrated +/-120 degree range")
            angles[side.name] = angle
        pending, desired_deformations = [], {}
        for helper in self.plan.helpers:
            _bone, pose = self._helper(helper)
            rest = self._rest[helper.key]
            axis, pivot = Vector(helper.axis).normalized(), Vector(helper.pivot)
            rotation = (Matrix.Translation(pivot)
                        @ Quaternion(axis, helper.fraction * angles[helper.key[0]]).to_matrix().to_4x4()
                        @ Matrix.Translation(-pivot))
            # Unparented bone pose = Rest @ basis. Thus pose @ Rest^-1 = T @ R.
            desired = transforms[helper.source_bone] @ rotation
            basis = rest.inverted() @ desired @ rest
            _require(all(math.isfinite(value) for row in basis for value in row),
                     "A helper basis became non-finite")
            desired_deformations[helper.key] = desired.copy()
            pending.append((helper, pose, basis, pose.matrix_basis.copy()))
        written = []
        report = {"completed": False, "tolerance": MATRIX_TOLERANCE, "helpers": []}
        self.last_basis_report = report
        try:
            for helper, pose, basis, previous in pending:
                written.append((pose, previous))
                pose.matrix_basis = basis
                difference = matrix_difference(pose.matrix_basis, basis)
                report["helpers"].append({"name": self.helper_names[helper.key], **difference})
                _require(difference["within_tolerance"], "Helper basis assignment readback exceeded tolerance")
        except Exception:
            for pose, previous in reversed(written):
                pose.matrix_basis = previous
            raise
        report["completed"] = True
        report["all_exact"] = all(item["exact"] for item in report["helpers"])
        report["max_abs"] = max((item["max_abs"] for item in report["helpers"]), default=0.0)
        self._desired_deformations = desired_deformations
        self._verification_pending = True
        return tuple((side.name, angles[side.name]) for side in self.plan.sides)

    def verify_evaluated(self, depsgraph):
        """After caller native update, certify each helper's evaluated T @ R.

        A failure permanently invalidates this disposable controller. This is a
        matrix oracle only: source/clone geometry, normals and Subsurf still need
        separate native evidence. Its tolerance is fixed and cannot be waived.
        """
        report = {"completed": False, "within_tolerance": False,
                  "all_exact": False, "tolerance": MATRIX_TOLERANCE, "helpers": []}
        self.last_verification = report
        try:
            self._guard()
            _require(self._verification_pending and self._desired_deformations is not None,
                     "No pending helper update is available for verification")
            scene = getattr(depsgraph.scene, "original", depsgraph.scene)
            _require(int(scene.as_pointer()) == self._scene_pointer,
                     "Verify with the explicit isolated scene dependency graph")
            evaluated_rig = self.rig.evaluated_get(depsgraph)
            for helper in self.plan.helpers:
                self._helper(helper)
                name = self.helper_names[helper.key]
                pose = evaluated_rig.pose.bones.get(name)
                _require(pose is not None, "An evaluated helper is missing")
                deformation = pose.matrix @ self._rest[helper.key].inverted()
                difference = matrix_difference(deformation, self._desired_deformations[helper.key])
                report["helpers"].append({"name": name, **difference})
                _require(difference["within_tolerance"], "Evaluated helper deformation exceeded tolerance: " + name)
            report.update(completed=True, within_tolerance=True,
                          all_exact=all(item["exact"] for item in report["helpers"]),
                          max_abs=max((item["max_abs"] for item in report["helpers"]), default=0.0),
                          rms=math.sqrt(sum(item["rms"] ** 2 for item in report["helpers"])
                                        / len(report["helpers"])) if report["helpers"] else 0.0)
            self._verification_pending = False
            return report
        except Exception as error:
            self._failed = True
            self.last_error = report["error"] = str(error)
            raise


def check_built_preview(preview):
    """Check successful builder issuance and current controller lifecycle.

    This is not a current deformation or geometry proof. Bake consumers still
    need the native identity guard and independent frame/matrix/TRS checks.
    A constructor call alone never issues a built preview.
    """
    _require(type(preview) is IsolatedBonePreview and preview in _BUILT_PREVIEWS,
             "Supply the exact controller issued by a successful isolated build")
    _require(not preview._closed and not preview._failed,
             "Preview is closed or failed; discard the disposable graph")
    return preview


def build_isolated_preview(mesh, rig, plan, *, source_mesh, source_rig, disposable=False,
                           isolated_scene=None, template=None, helper_rest_mode="current"):
    """Modify only verified disposable copies; construction failures discard them.

    Caller owns destruction of a failed clone and source graph proof. No authored
    object, original group, key, rig, action or file is changed. Helper groups
    receive native raw weights, preserving all unrelated native group weights.
    The optional identity Rest is a private precision experiment. Source twist
    axis/pivot and the desired-deformation formula remain unchanged in both modes.
    """
    _require(type(helper_rest_mode) is str and helper_rest_mode in ("current", "identity"),
             "Helper Rest mode must be current or identity")
    trusted_template = _consume_plan(plan)
    _require(template is None or template is trusted_template,
             "The supplied template is not the trusted certificate beside this plan")
    import bpy
    from mathutils import Vector
    from . import forearm_twist_playback_core as core
    original_pointers = _clone_guard(mesh, rig, source_mesh, source_rig, plan, disposable, isolated_scene)
    names = _helper_names(plan, rig.data.bones.keys(), mesh.vertex_groups.keys())
    check_helper_paths(names.values(), _existing_animation_paths(rig))
    # No native mutation precedes these complete fresh source and clone checks.
    # The retained certificate never substitutes for a current content witness.
    core._assert_target_content(source_mesh, source_rig, trusted_template)
    core._assert_target_content(mesh, rig, trusted_template)
    token = uuid.uuid4().hex
    mesh[OWNER_KEY] = rig[OWNER_KEY] = token
    mesh.modifiers[0].object = rig
    # A copied authoring record must never enroll this clone in the existing
    # authoring handler or apply the inverse Shape Key on top of helper skinning.
    if RECORD_KEY in mesh:
        del mesh[RECORD_KEY]
    for name in plan.managed_key_names:
        key = mesh.data.shape_keys.key_blocks[name]
        key.value, key.mute = 0.0, True
    masks = _preserve_shape_masks(mesh, rig, plan.rewrites, set(plan.managed_key_names))
    selected, active = tuple(bpy.context.selected_objects), bpy.context.view_layer.objects.active
    try:
        for obj in selected:
            obj.select_set(False)
        rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
        result = bpy.ops.object.mode_set(mode="EDIT")
        _require("FINISHED" in result and rig.mode == "EDIT", "Could not edit the disposable rig")
        for helper in plan.helpers:
            candidate = names[helper.key]
            bone = rig.data.edit_bones.new(candidate)
            _require(bone.name == candidate, "The admitted helper name changed during native creation")
            if helper_rest_mode == "identity":
                bone.head = Vector((0.0, 0.0, 0.0))
                bone.tail = Vector((0.0, 0.05, 0.0))
                bone.roll = 0.0
            else:
                bone.head = Vector(helper.pivot)
                bone.tail = bone.head + Vector(helper.axis).normalized() * 0.05
            bone.parent, bone.use_connect, bone.use_deform = None, False, True
        result = bpy.ops.object.mode_set(mode="OBJECT")
        _require("FINISHED" in result and rig.mode == "OBJECT", "Could not finish the disposable rig")
    finally:
        if rig.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        rig.select_set(False)
        for obj in selected:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = active
    for name in names.values():
        if helper_rest_mode == "identity":
            _check_identity_rest(rig.data.bones[name].matrix_local)
        rig.data.bones[name][OWNER_KEY] = token
        rig.pose.bones[name].rotation_mode = "QUATERNION"
        _require(mesh.vertex_groups.new(name=name).name == name,
                 "A helper group name could not match its owned bone")
    for rewrite in plan.rewrites:
        raw = dict(_native_weights(mesh, rig, rewrite.index)[0])
        for original, helper_key, influence in rewrite.transfers:
            amount = raw[original]
            group = mesh.vertex_groups[original]
            if influence == 1.0:
                group.remove([rewrite.index])
            else:
                group.add([rewrite.index], amount * (1.0 - influence), "REPLACE")
            mesh.vertex_groups[names[helper_key]].add([rewrite.index], amount * influence, "REPLACE")
    preview = IsolatedBonePreview(mesh, rig, plan, token, names, masks, isolated_scene, original_pointers)
    # Registration follows every native construction step and successful
    # controller initialization. Partial builds and direct constructors do not
    # qualify; weak membership cannot keep a discarded controller alive.
    _BUILT_PREVIEWS.add(preview)
    return preview
