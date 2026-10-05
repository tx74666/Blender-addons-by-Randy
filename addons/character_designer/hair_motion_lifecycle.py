"""Retain proven Hair source identity across same-object remove/rebind.

Only two source-object ID properties and one private Text holder are owned here.
The holder's source pointer survives Object.copy without being remapped to the
copy, unlike a self-reference stored on an Object. Geometry, skin weights,
Shape Keys, Actions and native bones are never changed by this service. A copied
Object's inherited metadata cannot authorize taking over the original identity.
Native binding proof is required to initialize the durable ownership marker.
"""
import hashlib
import json
import uuid

import bmesh
import bpy


OWNER_KEY = 'character_designer_hair_motion_owner'
IDENTITY_KEY = 'character_designer_hair_motion_identity_v1'
REGISTRY_KEY = 'character_designer_hair_strands_v1'
PROFILE_KEY = 'character_designer_hair_motion_v1'
VERSION = 1
_KEYS = (OWNER_KEY, IDENTITY_KEY)
HOLDER_SOURCE_KEY = 'character_designer_hair_motion_identity_source'
HOLDER_UID_KEY = 'character_designer_hair_motion_identity_uid'
HOLDER_VERSION_KEY = 'character_designer_hair_motion_identity_version'
HOLDER_OWNER_KEY = 'character_designer_hair_motion_identity_holder'
HOLDER_OWNER_VALUE = 'character_designer.hair_motion_identity.v1'
_HOLDER_BODY = ('Character Designer Hair Motion Identity\n'
                'Internal ownership metadata; not an executable script.\n')


class HairMotionLifecycleError(ValueError):
    """A copied, incomplete or changed source cannot inherit motion identity."""


def _source(source):
    if not isinstance(source, bpy.types.Object) or source.type != 'MESH':
        raise HairMotionLifecycleError('Choose the original Hair mesh for its motion identity.')
    return source


def _uid(value):
    try:
        return uuid.UUID(str(value)).hex
    except (ValueError, TypeError, AttributeError) as exc:
        raise HairMotionLifecycleError('The saved Hair source identity is not a valid UUID.') from exc


def _topology(source):
    """Same connectivity fingerprint as the registry; coordinates are irrelevant."""
    if source.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(source.data)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        count = len(bm.verts)
        edges = [tuple(vertex.index for vertex in edge.verts) for edge in bm.edges]
        faces = [tuple(vertex.index for vertex in face.verts) for face in bm.faces]
    else:
        count = len(source.data.vertices)
        edges = [tuple(edge.vertices) for edge in source.data.edges]
        faces = [tuple(face.vertices) for face in source.data.polygons]
    payload = (count, tuple(sorted(tuple(sorted(edge)) for edge in edges)), tuple(faces))
    return hashlib.sha256(repr(payload).encode('ascii')).hexdigest()


def snapshot(source):
    """A local rollback token; ID pointers are preserved, never deep-copied."""
    _source(source)
    return {'source': source, 'properties': {key: (key in source, source.get(key)) for key in _KEYS},
            'holders': tuple(holder for holder in bpy.data.texts if _owned_holder(holder, source))}


def restore(source, token):
    """Restore owned keys, then discard only newly created unused holders."""
    _source(source)
    if (not isinstance(token, dict) or token.get('source') != source
            or not isinstance(token.get('properties'), dict) or set(token['properties']) != set(_KEYS)
            or not isinstance(token.get('holders'), tuple)):
        raise HairMotionLifecycleError('The Hair identity rollback token belongs to another source.')
    for key in _KEYS:
        present, value = token['properties'][key]
        if present:
            source[key] = value
        elif key in source:
            del source[key]
    # The restored source pointer must be detached before inspecting users.
    # Existing orphan holders are protected by the pre-operation inventory.
    for holder in tuple(bpy.data.texts):
        if (holder not in token['holders'] and _owned_holder(holder, source)
                and holder.users == 0 and holder.as_string() == _HOLDER_BODY
                and not holder.use_module and not holder.use_fake_user):
            bpy.data.texts.remove(holder)


def _owned_holder(holder, source, source_uid=None):
    if (not isinstance(holder, bpy.types.Text) or holder.library or holder.override_library
            or holder.get(HOLDER_OWNER_KEY) != HOLDER_OWNER_VALUE
            or type(holder.get(HOLDER_VERSION_KEY)) is not int
            or holder.get(HOLDER_VERSION_KEY) != VERSION
            or holder.get(HOLDER_SOURCE_KEY) != source):
        return False
    try:
        saved_uid = holder.get(HOLDER_UID_KEY)
        return saved_uid == _uid(saved_uid) and (source_uid is None or saved_uid == source_uid)
    except HairMotionLifecycleError:
        return False


def _new_holder(source, source_uid):
    existing = tuple(holder for holder in bpy.data.texts if _owned_holder(holder, source))
    if existing:
        if len(existing) != 1 or not _owned_holder(existing[0], source, source_uid):
            raise HairMotionLifecycleError('The Hair source has conflicting retained identity holders; restore its original proof.')
        return existing[0]
    holder = bpy.data.texts.new('Hair Motion Identity')
    try:
        # Text datablocks default to a fake user in some Blender versions.
        # The published Object pointer is the durable user; an unpublished
        # owned holder must be removable after a transaction rollback.
        holder.use_fake_user = False
        holder.use_module = False
        holder[HOLDER_SOURCE_KEY] = source
        holder[HOLDER_OWNER_KEY] = HOLDER_OWNER_VALUE
        holder[HOLDER_VERSION_KEY] = VERSION
        holder[HOLDER_UID_KEY] = source_uid
        holder.write(_HOLDER_BODY)
        return holder
    except Exception:
        # This exact pointer was created here and was never published. Retain
        # any externally referenced holder rather than deleting shared data.
        if holder.users == 0 and holder.get(HOLDER_SOURCE_KEY) == source:
            bpy.data.texts.remove(holder)
        raise


def _retained(source):
    if not any(key in source for key in _KEYS):
        return None
    if (not all(key in source for key in _KEYS)
            or not isinstance(source.get(IDENTITY_KEY), str)):
        raise HairMotionLifecycleError('Copied or incomplete Hair ownership cannot inherit the original motion identity.')
    try:
        data = json.loads(source[IDENTITY_KEY])
        if (not isinstance(data, dict) or set(data) != {'version', 'source_uid', 'topology'}
                or type(data['version']) is not int or data['version'] != VERSION
                or data['source_uid'] != _uid(data['source_uid'])
                or not isinstance(data['topology'], str) or len(data['topology']) != 64
                or any(char not in '0123456789abcdef' for char in data['topology'])):
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise HairMotionLifecycleError('The retained Hair motion identity is incomplete or damaged.') from exc
    if not _owned_holder(source.get(OWNER_KEY), source, data['source_uid']):
        raise HairMotionLifecycleError('Copied or incomplete Hair ownership cannot inherit the original motion identity.')
    if data['topology'] != _topology(source):
        raise HairMotionLifecycleError('Hair topology changed; do not reuse its retained motion proof automatically.')
    _unique_owner(source, data['source_uid'])
    return data


def _unique_owner(source, source_uid):
    # A copied Object shares the holder, whose authoritative SOURCE remains the
    # original. Also reject distinct valid holders presenting the same UUID.
    for other in bpy.data.objects:
        if (other == source or other.type != 'MESH'
                or not _owned_holder(other.get(OWNER_KEY), other, source_uid)):
            continue
        try:
            data = json.loads(other.get(IDENTITY_KEY, 'null'))
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and data.get('source_uid') == source_uid:
            raise HairMotionLifecycleError('Another owned Hair object has this UUID; copied identities are ambiguous.')


def _saved_settings(source, source_uid, topology):
    """An unbound registry's native Rest is stale, but its identity must be exact."""
    from . import hair_strand_registry, hair_motion_profiles
    saved = None
    if REGISTRY_KEY in source:
        saved = hair_strand_registry.read(source, validate=False)
        if saved is None or saved['source_uid'] != source_uid or saved['topology'] != topology:
            raise HairMotionLifecycleError('Saved strand settings belong to a different Hair identity or topology.')
    if PROFILE_KEY in source:
        if saved is None:
            raise HairMotionLifecycleError('Hair motion settings lack their exact saved strand registry.')
        hair_motion_profiles.read(source, registry=saved)
    return saved


def _bound_proof(source, record=None, *, strict_registry=False):
    from . import hair_bones_rig, hair_strand_registry
    record = hair_bones_rig._read_records(source) if record is None else record
    if record is None:
        raise HairMotionLifecycleError('A live owned Hair binding is required to claim its source identity.')
    armature = source.get(hair_bones_rig.RIG_KEY)
    if not isinstance(armature, bpy.types.Object) or armature.type != 'ARMATURE':
        raise HairMotionLifecycleError('The authoritative Hair armature is missing.')
    # This verifies actual source ID pointers, OWN/SIGNATURE, current native Rest,
    # parenting and the binding record. A copied JSON string is never proof.
    hair_bones_rig._validate_owned(source, armature, record, hair_bones_rig._mesh_snapshot(source))
    source_uid, topology = _uid(record['source_id']), _topology(source)
    retained = _retained(source)
    if retained is not None and retained['source_uid'] != source_uid:
        raise HairMotionLifecycleError('The new native binding changed the retained Hair source UUID.')
    _saved_settings(source, source_uid, topology)
    if strict_registry and REGISTRY_KEY in source:
        # Used before removal, while the exact old bones still exist. Rebind
        # commits intentionally wait for explicit reconcile of old segment Rest.
        hair_strand_registry.read(source, validate=True)
    _unique_owner(source, source_uid)
    return {'version': VERSION, 'source_uid': source_uid, 'topology': topology}


def source_uid_for_bind(source, record=None):
    """Choose an identity read-only; fresh UUIDs become durable only on success."""
    _source(source)
    from . import hair_bones_rig
    record = hair_bones_rig._read_records(source) if record is None else record
    if record is not None:
        return _bound_proof(source, record)['source_uid']
    retained = _retained(source)
    if retained is not None:
        _saved_settings(source, retained['source_uid'], retained['topology'])
        return retained['source_uid']
    if REGISTRY_KEY in source or PROFILE_KEY in source:
        raise HairMotionLifecycleError('Unbound legacy settings have no proven source owner; restore the original binding first.')
    return uuid.uuid4().hex


def _store(source, key, value):
    source[key] = value


def claim_bound(source, *, strict_registry=False):
    """Atomically anchor a configured source after its native bind succeeds.

    Plain unconfigured bindings retain their existing reversible behavior and
    receive no additional permanent properties. Call after initialization too.
    On re-segmentation, saved Rest is reconciled by the caller after this claim.
    """
    _source(source)
    proof = _bound_proof(source, strict_registry=strict_registry)
    if not any(key in source for key in (REGISTRY_KEY, PROFILE_KEY, *_KEYS)):
        return proof['source_uid']
    if source.library or source.override_library or not source.is_editable:
        raise HairMotionLifecycleError('Make the configured Hair source local and editable before retaining its identity.')
    token = snapshot(source)
    try:
        holder = source.get(OWNER_KEY)
        if holder is None:
            holder = _new_holder(source, proof['source_uid'])
        _store(source, OWNER_KEY, holder)
        _store(source, IDENTITY_KEY, json.dumps(proof, sort_keys=True, separators=(',', ':'), allow_nan=False))
        if _retained(source) != proof:
            raise HairMotionLifecycleError('The Hair source identity could not be retained exactly.')
    except Exception as exc:
        try:
            restore(source, token)
        except Exception as rollback:
            raise HairMotionLifecycleError('Hair identity write failed and rollback needs attention: ' + str(rollback)) from exc
        if isinstance(exc, HairMotionLifecycleError):
            raise
        raise HairMotionLifecycleError('Hair identity could not be saved: ' + str(exc)) from exc
    return proof['source_uid']


def remember_before_remove(source):
    """Strictly prove then retain configured identity before old bones vanish."""
    return claim_bound(source, strict_registry=True)


def before_mutation(context, source):
    """End the owned preview before bind/remove/capture takes any snapshots."""
    _source(source)
    from . import hair_wiggle_adapter
    if hair_wiggle_adapter.status(context)['active']:
        try:
            hair_wiggle_adapter.stop_preview(context, reason='Stopped before changing the Hair source binding/capture.')
        except ValueError as exc:
            raise HairMotionLifecycleError('Hair preview could not restore the author state: ' + str(exc)) from exc
        if hair_wiggle_adapter.status(context)['active']:
            raise HairMotionLifecycleError('Stop the active Hair preview before changing its binding/capture.')
