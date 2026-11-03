"""Fresh complete content proofs for one synchronous wrist transaction.

Typed bulk digests are ephemeral and never compared with persisted legacy
fingerprints. A Current-Rig Source is first authenticated by the real revision
validator. Later revision reads retain every original check and accept that
legacy digest only after freshly proving identical complete Source content.
There is no cross-operation cache or replacement of a product module/global.
"""
from types import SimpleNamespace


POLICY = 'wrist-fresh-content/1'


def _require(value, message):
    if not value:
        raise ValueError(message)


def _modules():
    from . import control_pose_offset, limb_ik_wrist_content
    return control_pose_offset, limb_ik_wrist_content


def action_hash(api, action, handle):
    return _modules()[1].action_digest(action, handle)


def unaffected(api, action, slot, affected):
    return _modules()[1].unaffected_digest(action, slot, affected)


def _slots(action):
    return tuple(s.handle for s in action.slots if s.target_id_type == 'OBJECT')


def _source(plan):
    _require(action_hash(plan['api'], plan['source'], plan['source_slot']) == plan['source_hash'],
             'The complete Source changed during the wrist reset; its original data was kept.')


class _AuthenticatedSource:
    """Read adapter scoped to a single fresh full revision validation."""
    def __init__(self, original, plan):
        self.original, self.plan = original, plan

    def __getattr__(self, name):
        return getattr(self.original, name)

    def fingerprint_action(self, action, handle):
        plan = self.plan
        _require(action is plan['source'] and handle == plan['source_slot'],
                 'The revision requested a different Source content proof.')
        _source(plan)
        return dict(fingerprint_known=True,
                    action_fingerprint=plan['revision_source_legacy'],
                    proof_reason='Fresh complete Source matches this operation\'s formally authenticated legacy content.')


class _InitialSource:
    """Compute the real persisted fingerprint through the byte-identical bulk reader."""
    def __init__(self, original, api, source, handle, expected):
        self.original, self.api = original, api
        self.source, self.handle, self.expected = source, handle, expected
        self.legacy = None

    def __getattr__(self, name):
        return getattr(self.original, name)

    def fingerprint_action(self, action, handle):
        _require(action is self.source and handle == self.handle and self.legacy is None,
                 'Authenticate this exact Source once with its real persisted fingerprint.')
        _require(action_hash(self.api, action, handle) == self.expected,
                 'The Source changed before its baseline fingerprint was read.')
        self.legacy = _modules()[1].legacy_action_hash(action, handle)
        _require(action_hash(self.api, action, handle) == self.expected,
                 'The Source changed during its baseline fingerprint read.')
        return dict(fingerprint_known=True, action_fingerprint=self.legacy,
                    proof_reason='Complete original-policy Source fingerprint from native bulk fields.')


def revision_validate(context, item_id, plan):
    _require(plan.get('proof_policy') == POLICY and context is plan['context']
             and item_id == plan['item_id'] and plan['revision_value'] is not None,
             'This wrist transaction has no authenticated current revision.')
    api = plan['api']
    item = api.worklist._item(api.worklist.state(context), item_id)
    _require(bool(getattr(item, 'current_rig_revision', '')),
             'The current revision owner changed during wrist reset.')
    original = api.revision._modules()
    delegated = SimpleNamespace(**vars(original))
    delegated.fingerprint = _AuthenticatedSource(original.fingerprint, plan)
    # Same revision validator, including checkpoint bytes, references, current
    # Rest/graph, full row identity, actual library baseline and native paths.
    return api.revision._read(context, item, delegated)


def prepare_variant(context, rig):
    offset, _content = _modules()
    api, saved, item, original, slot = offset._variant_admission(context, rig, label='Wrist Neutral')
    source_hash = action_hash(api, item.source_action, item.source_slot)
    value = None
    legacy = None
    if getattr(item, 'current_rig_revision', ''):
        original_api = api.revision._modules()
        delegated = SimpleNamespace(**vars(original_api))
        initial = _InitialSource(original_api.fingerprint, api, item.source_action, item.source_slot, source_hash)
        delegated.fingerprint = initial
        value = api.revision._read(context, item, delegated)
        legacy = initial.legacy
        _require(legacy is not None, 'The current Source baseline was not fully authenticated.')
    else:
        api.worklist._check_baseline(item)
        _require(not any(key in original for key in (api.revision.TOKEN, api.revision.CHECKPOINT)),
                 'This Action lost its current-Rig row ownership; recovery was kept.')
    _require(action_hash(api, item.source_action, item.source_slot) == source_hash,
             'The Source changed while its baseline was being authenticated.')
    capture = api.target._capture(context, rig)
    api.target._validate_capture(context, capture)
    handles = _slots(original)
    return dict(variant_api=1, proof_policy=POLICY, phase='prepared', api=api,
        context=context, rig=rig, saved=saved, item=item, item_id=item.item_id,
        original=original, slot=slot.handle, slots=handles,
        row_identity={key: value for key, value in api.revision._row(item).items()
                      if key in api.revision._IDENTITY_FIELDS},
        source=item.source_action, source_slot=item.source_slot, source_hash=source_hash,
        hashes={handle: action_hash(api, original, handle) for handle in handles},
        capture=capture, previous=(saved.active_index, saved.status, saved.has_error),
        previous_target=context.window_manager.character_designer_animation.target,
        frame=context.scene.frame_current + context.scene.frame_subframe,
        asset_name='Wrist Neutral', variant_label='Wrist Neutral', revision_value=value,
        revision_source_legacy=legacy)


def validate_variant(plan, *, final=False):
    _require(type(plan) is dict and plan.get('proof_policy') == POLICY,
             'Prepare a fresh wrist content transaction before applying it.')
    offset, _content = _modules()
    context, rig, api = plan['context'], plan['rig'], plan['api']
    # Recheck all original owner and policy admission, without constructing and
    # immediately discarding another whole-animation preparation.
    offset._variant_admission(context, rig, label='Wrist Neutral')
    item = offset._variant_owner(plan, final=False)
    _require(_slots(plan['original']) == plan['slots'], 'The Custom Action slot inventory changed.')
    if final:
        api.target._validate_capture(context, plan['capture'])
        _require(all(action_hash(api, plan['original'], handle) == digest
                     for handle, digest in plan['hashes'].items()),
                 'The original complete Custom content changed during wrist reset.')
    if plan['revision_value'] is not None:
        revision_validate(context, plan['item_id'], plan)
    else:
        _require(not getattr(item, 'current_rig_revision', '')
                 and not any(key in plan['original'] for key in (api.revision.TOKEN, api.revision.CHECKPOINT)),
                 'The Custom revision ownership changed during wrist reset.')
        api.worklist._check_baseline(item)
        _source(plan)
    return item
