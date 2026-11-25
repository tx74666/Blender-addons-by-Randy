"""Update the owned limb solvers which produce the character's visible foot pose."""
import copy

import bpy

from . import natural_bind_graph as graph


class _SolverChanges:
    def __init__(self, rig, helpers, changes, cancels_old_roll, record):
        self.rig = rig
        self.helpers = helpers
        self.owners = frozenset(obj for obj, _, _, _ in helpers)
        self.changes = changes
        self.cancels_old_roll = cancels_old_roll
        self.record = copy.deepcopy(record)
        self.plants = []
        self.context = None

    def apply(self, context):
        from . import foot_roll_hinge_plant as plant
        self.context = context
        try:
            for curve, expression, ref, target, _, _ in self.changes:
                curve.driver.expression = expression
                if ref.id != target:
                    ref.id = target
            for obj, branch, key, _ in self.helpers:
                if self.record.get('auto_follow') == 1:
                    added = plant.new_record()
                    self.plants.append((obj, added))
                    plant.install(context, obj, self.record, added, input_rig=self.rig)
                obj.update_tag()
                branch[key] = graph._solver_signature(obj, self.rig)
        except Exception:
            self.rollback()
            raise

    def rollback(self):
        from . import foot_roll_hinge_plant as plant
        while self.plants:
            obj, added = self.plants.pop()
            plant.rollback(self.context, obj, added)
        for curve, _, ref, _, old_expression, old_id in reversed(self.changes):
            curve.driver.expression = old_expression
            if ref.id != old_id:
                ref.id = old_id
        for obj, branch, key, signature in self.helpers:
            obj.update_tag()
            branch[key] = signature


def prepare(rig, record):
    """Authenticate all direct, Stable and frozen copies before changing any of them."""
    if graph.KEY not in rig:
        return None
    from . import foot_roll_hinge, natural_bind_reference, natural_stable_solver
    binding = graph.validate(rig)
    if binding.get('stable'):
        natural_stable_solver.verify_owner(rig, binding)
    if graph._rig_references(rig, binding) != list(binding['rig_references']):
        raise ValueError('The limb solvers have additional references on this character.')
    primary = foot_roll_hinge.driver_changes(rig, record)
    ball = rig.pose.bones[record['bones']['BALL_PIVOT']].path_from_id('rotation_euler')
    helpers, changes = [], []
    cancels_old_roll = False
    for branch in (binding, binding.get('stable')):
        if not branch:
            continue
        if branch.get('neutral_source'):
            natural_bind_reference.verify_owner(rig, branch)
        for field, key in (('source', 'solver_signature'), ('neutral_source', 'neutral_signature')):
            obj = branch.get(field)
            if not obj:
                continue
            if (obj.library or obj.override_library or obj.data.library or obj.data.users != 1
                    or tuple(obj.users_collection) != (binding['collection'],)
                    or graph._solver_signature(obj, rig) != branch[key]):
                raise ValueError(f'The registered limb solver {obj.name} has changed.')
            helpers.append((obj, branch, key, branch[key]))
            for entry, original, expression in primary:
                curve = obj.animation_data.drivers.find(entry['path'], index=entry['index'])
                if (not curve or curve.driver.type != 'SCRIPTED'
                        or curve.driver.expression != original.driver.expression
                        or len(curve.driver.variables) != 1):
                    raise ValueError(f'The foot mechanism in {obj.name} has changed.')
                variable = curve.driver.variables[0]
                ref = variable.targets[0]
                if (variable.type != 'SINGLE_PROP' or ref.id not in {rig, obj}
                        or entry['variables'].get(variable.name) != ref.data_path):
                    raise ValueError(f'The foot input in {obj.name} has changed.')
                target = obj if entry['path'] == ball and entry['index'] == 0 else ref.id
                if field == 'neutral_source' and target == obj and ref.id == rig:
                    cancels_old_roll = True
                changes.append((curve, expression, ref, target, curve.driver.expression, ref.id))
    # Extra external users may depend on the previous private solver's result.
    objects = {obj for obj, _, _, _ in helpers}
    collection = binding['collection']
    allowed = {rig, collection, *collection.objects, *rig.users_scene}
    users = bpy.data.user_map(subset=objects | {obj.data for obj in objects})
    if any(user not in allowed for group in users.values() for user in group):
        raise ValueError('Another datablock uses the private foot solver.')
    return _SolverChanges(rig, helpers, changes, cancels_old_roll, record)


def replace_cloned_reference(context, source, rig, foot):
    """The new Stable IK uses a different shin constraint from its Direct parent."""
    from . import foot_roll_hinge_plant as plant
    bones = {role: name for role, name in foot['bones'].items()
             if role.startswith(('UNROLLED_', 'HEEL_RISE_'))}
    if not bones:
        return
    names = set(bones.values())
    paths = [source.pose.bones[name].path_from_id() + '.' for name in names]
    constraints = []
    for row in foot['constraints']:
        if row['owner'] in names or row['name'] == 'CD Heel Lift Plant':
            owner = source.pose.bones[row['owner']]
            con = owner.constraints[row['name']]
            paths.append(con.path_from_id() + '.')
            constraints.append(row)
    drivers = [dict(path=f.data_path,index=f.array_index) for f in source.animation_data.drivers
               if f.data_path.startswith(tuple(paths))]
    plant.rollback(context, source, dict(bones=bones, constraints=constraints, drivers=drivers))
    plant.install(context, source, foot, plant.new_record(), input_rig=rig)
