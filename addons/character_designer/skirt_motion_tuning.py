"""Explicit Dress tuning transactions over the owned native Cloth surface.

No frame handler, bone transform, original Action or skin weight is written.
An existing sealed cache must be reset before changing material parameters;
mode switches retain that cache. Direct Manual previews the input; its edits
require an explicit Reset and replay instead of changing the sealed result.
"""
import copy
import math

from . import skirt_motion_profiles as profiles


def _choice_service():
    from . import animation_physics
    return animation_physics


def _clear_choice_errors(sources, choices):
    """Clear only each successful source's proved owner, never UI selection."""
    for source in sources:
        try:
            owner = choices._owner(source, 'SKIRT')
            if owner is not None:
                choices._LAST_ERRORS.pop(choices._error_key(owner, 'SKIRT', source), None)
        except (ValueError, RuntimeError, ReferenceError, AttributeError, TypeError):
            # If error ownership cannot be proved after a successful native
            # action, retain that error rather than clearing another owner's.
            pass

_SETTINGS = {
    "quality": "quality", "mass": "mass", "stretch": "tension_stiffness",
    "shear": "shear_stiffness", "bend": "bending_stiffness",
    "damping": "tension_damping", "bend_damping": "bending_damping",
    "air_damping": "air_damping", "pin_stiffness": "pin_stiffness",
}
_COLLISION = {
    "collision_quality": "collision_quality", "self_collision": "use_self_collision",
    "self_friction": "self_friction",
}


def _modules():
    from . import skirt_physics, skirt_rig
    return skirt_physics, skirt_rig


def _height(record):
    height = record["fit"]["height_world"]
    if not isinstance(height, (int, float)) or not math.isfinite(height) or height <= 0:
        raise profiles.DressMotionError("The saved Dress height is invalid.")
    return height


def _local(source, rig):
    if (source is None or rig is None
            or any(item.library or item.override_library or not item.is_editable for item in (source, rig))
            or rig.data.library or rig.data.override_library):
        raise profiles.DressMotionError("Keep the Dress source and its Main Rig local and editable before tuning.")


def _native(record, cloth, previous=None):
    values = dict(profiles.DEFAULTS)
    if previous is not None:
        for key in ("waist_depth", "transition", "recovery"):
            values[key] = previous["settings"][key]
    values.update({key: getattr(cloth.settings, attribute) for key, attribute in _SETTINGS.items()})
    values.update({key: getattr(cloth.collision_settings, attribute) for key, attribute in _COLLISION.items()})
    values["gravity"] = cloth.settings.effector_weights.gravity
    height = _height(record)
    values["collision_margin"] = cloth.collision_settings.distance_min / height
    values["self_margin"] = cloth.collision_settings.self_distance_min / height
    return profiles.native_settings(values)


def _profile(source, record, rig, cloth, *, capability=None):
    physics, skirt = _modules()
    if physics.backend(record) in physics.SURFACE_BACKENDS:
        holder, _driver_id, _path = skirt.physics_control(source)
        value = holder.get("physics_influence", 0.0)
        if type(value) not in (int, float) or value not in (0.0, 1.0):
            raise profiles.DressMotionError("The Dress surface supports Automatic or Manual; an intermediate Physics Blend has no validated meaning.")
    previous = profiles.read(source, record)
    if previous is not None:
        if capability is not None and previous["capability"] != capability:
            if previous["capability"] == "MANUAL" and capability == "BOTH" and cloth:
                previous = profiles.edited(previous, record, capability="BOTH", mode="AUTOMATIC")
            else:
                raise profiles.DressMotionError("Use the existing Dress generation choice; its saved controls are retained.")
        return profiles.edited(previous, record, _native(record, cloth, previous) if cloth else {})
    if physics.backend(record) == physics.DIRECT_SURFACE_BACKEND:
        raise profiles.DressMotionError("Restore the Direct Dress motion profile before tuning; its old bone-physics influence is not its surface mode.")
    holder, _driver_id, _path = skirt.physics_control(source)
    value = holder.get("physics_influence", 0.0)
    if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise profiles.DressMotionError("The Dress physics influence is invalid.")
    mode = "AUTOMATIC" if cloth and value > 0 else "MANUAL"
    capability = capability or ("BOTH" if cloth else "MANUAL")
    return profiles.fresh(record, capability=capability, mode=mode,
                          values=_native(record, cloth) if cloth else None)


def initialize(source, *, capability=None, context=None):
    """Adopt current native settings; never overwrite an artist's tuning."""
    physics, skirt = _modules()
    record = skirt.read_record(source)
    if record is None:
        raise profiles.DressMotionError("Create the Dress setup first.")
    rig = source.get(skirt.RIG_KEY)
    _local(source, rig)
    cloth, proxy = None, None
    if record.get("physics"):
        record, rig, proxy, cloth = physics.validate_physics(source)
    profile = _profile(source, record, rig, cloth, capability=capability)
    holder, _driver_id, path = skirt.physics_control(source)
    previous = profiles.read(source, record)
    direct = physics.backend(record) == physics.DIRECT_SURFACE_BACKEND
    surface_change = ((direct and previous is not None and previous["mode"] != profile["mode"])
                      or (physics.backend(record) == physics.ACTUAL_SURFACE_BACKEND
                          and holder.get("physics_influence") != float(profile["mode"] == "AUTOMATIC")))
    if not surface_change:
        return profiles.write(source, profile, record)
    # Adding physics to a Manual-only setup promotes its capability. Commit
    # the saved mode and both native endpoints together, retaining the cache.
    if not (previous and previous["capability"] == "MANUAL" and capability == "BOTH"):
        raise profiles.DressMotionError("The saved Dress mode differs from its native output; restore it before initializing.")
    skirt._require_controls_for_setup(source)
    _influence_editable(rig, path)
    if context is None:
        import bpy
        context = bpy.context
    item = _snapshot(source, copy.deepcopy(record), rig, proxy, cloth)
    item["change_native"] = False
    try:
        holder["physics_influence"] = 0.0 if direct else float(profile["mode"] == "AUTOMATIC")
        item["surface"].set_mode(source, record, profile["mode"])
        result = profiles.write(source, profile, record)
        rig.update_tag(refresh={"OBJECT"})
        context.view_layer.update()
        physics.validate_physics(source)
        return result
    except Exception as error:
        failures = []
        try:
            _restore(item)
        except Exception as rollback_error:
            failures.append(str(rollback_error))
        try:
            context.view_layer.update()
        except Exception as rollback_error:
            failures.append(str(rollback_error))
        if failures:
            raise profiles.DressMotionError("Dress initialization failed; rollback also needs recovery: "
                                           + "; ".join(failures)) from error
        raise


def effective(source):
    """Explicit action read; drawing should read saved metadata instead."""
    physics, skirt = _modules()
    record = skirt.read_record(source)
    if record is None:
        raise profiles.DressMotionError("Create the Dress setup first.")
    rig = source.get(skirt.RIG_KEY)
    cloth = None
    if record.get("physics"):
        record, rig, _proxy, cloth = physics.validate_physics(source)
    return _profile(source, record, rig, cloth)


def _actions(rig):
    from . import limb_ik
    animation = rig.animation_data
    if not animation:
        return ()
    actions = {animation.action} if animation.action else set()
    pending = [strip for track in animation.nla_tracks for strip in track.strips]
    while pending:
        strip = pending.pop()
        if strip.action:
            actions.add(strip.action)
        pending.extend(getattr(strip, "strips", ()))
    return tuple(curve for action in actions for curve in limb_ik._fcurves_for_action(action))


def _influence_editable(rig, path):
    curves = list(rig.animation_data.drivers) if rig.animation_data else []
    curves.extend(_actions(rig))
    if any(curve.data_path == path for curve in curves):
        raise profiles.DressMotionError("Dress physics influence has animation or a driver; preserve it before switching modes.")


def _weights(proxy, group):
    return [next((item.weight for item in vertex.groups if item.group == group.index), 0.0)
            for vertex in proxy.data.vertices]


def _set_weights(proxy, group, values):
    # Only the owned proxy's goal group is touched, never source skin weights.
    group.remove(list(range(len(proxy.data.vertices))))
    buckets = {}
    for index, value in enumerate(values):
        if value > 0:
            buckets.setdefault(value, []).append(index)
    for value, indices in buckets.items():
        group.add(indices, value, "REPLACE")


def _snapshot(source, record, rig, proxy, cloth):
    physics, skirt = _modules()
    holder, _driver_id, path = skirt.physics_control(source)
    snapshot = {"source": source, "record": record, "rig": rig, "proxy": proxy, "cloth": cloth,
                "profile": source.get(profiles.PROFILE_KEY), "record_raw": source[skirt.RECORD_KEY],
                "holder": holder, "influence": holder.get("physics_influence"), "path": path,
                "native_touched": False}
    if physics.backend(record) in physics.SURFACE_BACKENDS:
        snapshot["surface"] = physics._surface_module(record)
        snapshot["surface_mode"] = snapshot["surface"].capture_mode(source, record)
    if cloth:
        group = proxy.vertex_groups.get(cloth.settings.vertex_group_mass)
        snapshot.update(group=group, weights=_weights(proxy, group),
                        native={attribute: getattr(cloth.settings, attribute)
                                for attribute in set(_SETTINGS.values()) | {"compression_stiffness", "compression_damping", "shear_damping"}},
                        collision={attribute: getattr(cloth.collision_settings, attribute)
                                   for attribute in set(_COLLISION.values()) | {"distance_min", "self_distance_min"}},
                        gravity=cloth.settings.effector_weights.gravity)
    return snapshot


def _restore(item):
    physics, skirt = _modules()
    cloth, source = item["cloth"], item["source"]
    restore_native = cloth and item["change_native"]
    if physics.backend(item["record"]) == physics.DIRECT_SURFACE_BACKEND:
        # A pre-write failure must not invalidate an otherwise untouched RAM
        # cache through rollback setters. Other backends retain their behavior.
        restore_native = restore_native and item["native_touched"]
    if restore_native:
        for attribute, value in item["native"].items():
            setattr(cloth.settings, attribute, value)
        for attribute, value in item["collision"].items():
            setattr(cloth.collision_settings, attribute, value)
        cloth.settings.effector_weights.gravity = item["gravity"]
        _set_weights(item["proxy"], item["group"], item["weights"])
    if item["influence"] is None:
        if "physics_influence" in item["holder"]:
            del item["holder"]["physics_influence"]
    else:
        item["holder"]["physics_influence"] = item["influence"]
    source[skirt.RECORD_KEY] = item["record_raw"]
    if item["profile"] is None:
        if profiles.PROFILE_KEY in source:
            del source[profiles.PROFILE_KEY]
    else:
        source[profiles.PROFILE_KEY] = item["profile"]
    if "surface_mode" in item:
        item["surface"].restore_mode(source, item["record"], item["surface_mode"])
        item["rig"].update_tag(refresh={"OBJECT"})
    if "choice_key" in item:
        if item["choice_raw"] is None:
            source.pop(item["choice_key"], None)
        else:
            source[item["choice_key"]] = item["choice_raw"]


def _assignments(item, values, changes):
    cloth = item["cloth"]
    assignments = []
    for key, attribute in _SETTINGS.items():
        if key in changes:
            assignments.append((cloth.settings, attribute, values[key]))
    if "stretch" in changes:
        assignments.append((cloth.settings, "compression_stiffness", values["stretch"]))
    if "damping" in changes:
        assignments.extend((cloth.settings, attribute, values["damping"])
                           for attribute in ("compression_damping", "shear_damping"))
    if "gravity" in changes:
        assignments.append((cloth.settings.effector_weights, "gravity", values["gravity"]))
    for key, attribute in _COLLISION.items():
        if key in changes:
            assignments.append((cloth.collision_settings, attribute, values[key]))
    height = _height(item["record"])
    if "collision_margin" in changes:
        assignments.append((cloth.collision_settings, "distance_min", values["collision_margin"] * height))
    if "self_margin" in changes:
        assignments.append((cloth.collision_settings, "self_distance_min", values["self_margin"] * height))
    for owner, attribute, value in assignments:
        prop = owner.bl_rna.properties[attribute]
        if prop.is_readonly or (type(value) is not bool and not prop.hard_min <= value <= prop.hard_max):
            raise profiles.DressMotionError(f"Dress {attribute} cannot be represented at this model's scale.")
    return assignments


def _apply_native(item, values, changes):
    cloth = item["cloth"]
    for owner, attribute, value in item["assignments"]:
        # RNA setters may mutate or invalidate cache before raising. Record the
        # attempt first; restoring coefficients cannot restore that RAM payload.
        item["native_touched"] = True
        setattr(owner, attribute, value)
        actual = getattr(owner, attribute)
        if (actual != value if type(value) in (bool, int) else
                abs(actual - value) > max(1.0e-12, abs(value) * 2.0e-6)):
            raise profiles.DressMotionError(f"Blender did not apply Dress {attribute} exactly enough.")
    physics = item["record"]["physics"]
    if set(changes) & {"waist_depth", "transition", "recovery"}:
        service, _skirt = _modules()
        weights = (profiles.surface_pin_weights(values, item["record"]["fit"], physics["rows"], physics["columns"])
                    if service.backend(item["record"]) in service.SURFACE_BACKENDS
                    else profiles.pin_weights(values, physics["rows"], physics["columns"]))
        item["native_touched"] = True
        _set_weights(item["proxy"], item["group"], weights)
        physics["pin_weights"] = _weights(item["proxy"], item["group"])
    physics["baked_range"] = None


def apply(context, sources, changes=None, *, mode=None, _clear_errors=True):
    """Validate the entire batch first, then edit only owned native settings.

    A failed write restores parameters, goals, influence and saved records.
    Unsealed caches may be invalidated and need sequential simulation again;
    sealed caches are never discarded by this transaction.
    """
    physics, skirt = _modules()
    choices = _choice_service() if mode is not None else None
    changes = profiles.settings(changes or {}, partial=True)
    sources = tuple(sources)
    if not sources or len({source.as_pointer() for source in sources}) != len(sources):
        raise profiles.DressMotionError("Choose distinct Dress sources for tuning.")
    staged = []
    for source in sources:
        if mode == "AUTOMATIC":
            from .animation_physics import require_enabled
            require_enabled(source, context=context, component="SKIRT")
        skirt._require_controls_for_setup(source)
        record = skirt.read_record(source)
        if record is None:
            raise profiles.DressMotionError("Create every requested Dress setup before batch tuning.")
        rig, proxy, cloth = source[skirt.RIG_KEY], None, None
        _local(source, rig)
        if record.get("physics"):
            record, rig, proxy, cloth = physics.validate_physics(source)
        elif changes or mode == "AUTOMATIC":
            raise profiles.DressMotionError("Add automatic physics to this Dress setup first.")
        before = _profile(source, record, rig, cloth)
        profile = profiles.edited(before, record, changes, mode=mode)
        # Mode changes write only influence/profile/output state. Material and
        # goal snapshots are needed only when those settings will be changed.
        item = _snapshot(source, copy.deepcopy(record), rig,
                         proxy if changes else None, cloth if changes else None)
        item["after"] = profile
        item["change_native"] = bool(changes)
        if changes and cloth.point_cache.is_baked:
            raise profiles.DressMotionError("Reset the baked Dress simulation before tuning its material.")
        if mode is not None:
            _influence_editable(rig, item["path"])
            choices._skirt_choice(source, record, before["mode"])
            item.update(choice_key=choices.SKIRT_CHOICE, choice_raw=source.get(choices.SKIRT_CHOICE))
        if changes:
            item["assignments"] = _assignments(item, profile["settings"], changes)
        staged.append(item)
    touched = []
    try:
        for item in staged:
            touched.append(item)
            if item["change_native"]:
                _apply_native(item, item["after"]["settings"], changes)
                skirt.write_record(item["source"], item["record"])
                # Verify written native goals and graph before exposing success.
                physics.validate_physics(item["source"])
            if mode is not None:
                # Direct Cloth is the final vertex writer; reopening the old
                # bone physics here would feed its output back into its input.
                item["holder"]["physics_influence"] = (
                    0.0 if physics.backend(item["record"]) == physics.DIRECT_SURFACE_BACKEND
                    else float(mode == "AUTOMATIC"))
                if "surface_mode" in item:
                    item["surface"].set_mode(item["source"], item["record"], mode)
            profiles.write(item["source"], item["after"], item["record"])
            if mode is not None:
                choices._write_skirt_choice(item["source"], {
                    "version": 1, "owner": item["record"]["owner"], "mode": mode, "pending": False})
        for item in staged:
            if item["change_native"]:
                # Native settings assignments invalidate the unsealed cache;
                # ensure the saved bake status also reflects that change.
                physics.clear_cache(context, item["source"])
                if (physics.backend(item["record"]) == physics.DIRECT_SURFACE_BACKEND
                        and item["native_touched"]):
                    item["surface"].invalidate_result(item["source"], item["record"])
            item["rig"].update_tag(refresh={"OBJECT"})
        context.view_layer.update()
        for item in staged:
            if "surface_mode" in item:
                physics.validate_physics(item["source"])
    except Exception as error:
        failures = []
        for item in reversed(touched):
            try:
                _restore(item)
            except Exception as rollback_error:
                failures.append(f"{item['source'].name}: {rollback_error}")
            if (physics.backend(item["record"]) == physics.DIRECT_SURFACE_BACKEND
                    and item["native_touched"]):
                # restore_mode can recover the old readiness flags, but native
                # coefficient/goal writes have already invalidated its cache.
                # Attempt withdrawal even if another restoration step failed.
                try:
                    item["surface"].invalidate_result(item["source"], item["record"])
                except Exception as rollback_error:
                    failures.append(f"{item['source'].name}: result invalidation: {rollback_error}")
        try:
            context.view_layer.update()
        except Exception as rollback_error:
            failures.append(str(rollback_error))
        if failures:
            raise profiles.DressMotionError("Dress tuning failed; rollback also needs recovery: "
                                           + "; ".join(failures)) from error
        raise
    if mode is not None and _clear_errors:
        _clear_choice_errors(sources, choices)
    return tuple(copy.deepcopy(item["after"]) for item in staged)


def select_manual_controls(context, source):
    """Enter Direct's input preview before exposing its editable controls.

    Selection can fail after a successful mode switch. Preserve that switch's
    captured state as well as the artist's selection; never clear or seek cache.
    """
    physics, skirt = _modules()
    skirt._require_controls_for_setup(source)
    record = skirt.read_record(source)
    if physics.backend(record) != physics.DIRECT_SURFACE_BACKEND:
        raise profiles.DressMotionError("Manual input selection requires a Direct Dress setup.")
    rig = source[skirt.RIG_KEY]
    original_context = skirt._context_state(context)
    if rig.mode not in {"OBJECT", "POSE"} or original_context[1] not in {"OBJECT", "POSE"}:
        raise profiles.DressMotionError("Choose Object or Pose Mode before selecting Dress controls.")
    # Mode-only rollback has no material/goal writes. Avoid capturing all
    # Cloth parameters and vertex weights for this selection operation.
    saved = _snapshot(source, copy.deepcopy(record), rig, None, None)
    saved["change_native"] = False
    choices = _choice_service()
    saved.update(choice_key=choices.SKIRT_CHOICE, choice_raw=source.get(choices.SKIRT_CHOICE))
    visibility = []
    for name in sorted(set.union(*skirt._bone_collection_layout(record))):
        bone, pose = rig.data.bones[name], rig.pose.bones.get(name)
        visibility.append((bone, bone.hide, bone.hide_select, pose,
                           pose.hide if pose is not None and hasattr(pose, "hide") else None))
    selection = {
        "context": original_context, "rig_mode": rig.mode,
        "selected": [(bone, bone.select) for bone in rig.pose.bones],
        "active": rig.data.bones.active, "hidden": rig.hide_get(), "visibility": visibility,
        "shapes": rig.data.show_bone_custom_shapes,
        "collections": [(collection, collection.is_visible, collection.is_solo)
                        for collection in rig.data.collections_all],
    }
    # apply owns its own failure rollback; selection has not begun if it fails.
    apply(context, (source,), mode="MANUAL", _clear_errors=False)
    try:
        result = skirt.select_controls(context, source)
    except Exception as error:
        failures = []
        try:
            _restore(saved)
        except Exception as rollback_error:
            failures.append("motion mode: " + str(rollback_error))
        try:
            active, mode, selected = selection["context"]
            if selection["rig_mode"] == "POSE" and active is not rig:
                # An unselected Armature may already be in Pose Mode while
                # another Object is active. The generic context restore first
                # exits the current Rig's Pose Mode, losing that valid state.
                if rig.mode != selection["rig_mode"]:
                    context.view_layer.objects.active = rig
                    skirt._activate(context, rig, selection["rig_mode"])
                if active is not None and active.mode != mode:
                    context.view_layer.objects.active = active
                    skirt._activate(context, active, mode)
                for candidate in context.view_layer.objects:
                    candidate.select_set(candidate in selected)
                context.view_layer.objects.active = active
            else:
                skirt._restore_context(context, selection["context"])
        except Exception as rollback_error:
            failures.append("object context: " + str(rollback_error))
        try:
            for bone, selected in selection["selected"]:
                bone.select = selected
            rig.data.bones.active = selection["active"]
            for collection, visible, solo in selection["collections"]:
                if collection.is_visible != visible:
                    collection.is_visible = visible
                if collection.is_solo != solo:
                    collection.is_solo = solo
            for bone, hidden, unselectable, pose, pose_hidden in selection["visibility"]:
                if bone.hide != hidden:
                    bone.hide = hidden
                if bone.hide_select != unselectable:
                    bone.hide_select = unselectable
                if pose_hidden is not None and pose.hide != pose_hidden:
                    pose.hide = pose_hidden
            if rig.data.show_bone_custom_shapes != selection["shapes"]:
                rig.data.show_bone_custom_shapes = selection["shapes"]
            rig.hide_set(selection["hidden"])
            context.view_layer.update()
        except Exception as rollback_error:
            failures.append("control selection/display: " + str(rollback_error))
        if failures:
            raise profiles.DressMotionError("Dress control selection failed; rollback also needs recovery: "
                                           + "; ".join(failures)) from error
        raise
    _clear_choice_errors((source,), choices)
    return result
