"""Prepared native Forearm definition/2 fixture; trusted owned launch only.

No top-level import of the older Forearm test module (it executes tests). Reuse
only its fixture AST. Never opens artist/Unity assets or registers the add-on.
"""
import argparse
import ast
import hashlib
import importlib
import json
import math
from pathlib import Path
import runpy
import sys
import tempfile
import traceback

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'addons/character_designer/unity_export.py'
GENERIC = ROOT / 'tests/test_unity_rig_source_snapshot_blender.py'
FOREARM = ROOT / 'tests/test_unity_forearm_blender.py'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(result):
    if result.exists(): raise RuntimeError('Preserve the previous native result')
    scope = runpy.run_path(str(GENERIC), run_name='snapshot_fixture_only')
    exporter = scope['exporter']
    twist = importlib.import_module('character_designer.forearm_twist')
    tree = ast.parse(FOREARM.read_text(encoding='utf8'))
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == 'fixture')
    fixture_scope = {'bpy': bpy, 'math': math, 'ft': twist}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(FOREARM), 'exec'), fixture_scope)
    report = {'schema': 'x.rig-source-forearm-rest-native/1', 'source_sha256': sha(SOURCE),
        'script_sha256': sha(__file__), 'generic_fixture_sha256': sha(GENERIC), 'forearm_fixture_sha256': sha(FOREARM),
        'blender_version': bpy.app.version_string, 'config': bpy.utils.user_resource('CONFIG'), 'checks': [],
        'scope': 'Owned tiny fixtures; bounded author/context/matrix-world read witness only. No artist/Unity/registration/deployment.'}

    def fixture():
        rig, body, records = fixture_scope['fixture']()
        body.parent, body.parent_type = rig, 'OBJECT'
        rig.matrix_basis = Matrix.Translation((2, -3, 1)) @ Matrix.Rotation(.4, 4, 'Z') @ Matrix.Scale(1.3, 4)
        body.matrix_parent_inverse = Matrix.Translation((-.1, .2, .1)) @ Matrix.Rotation(-.1, 4, 'Y') @ Matrix.Scale(.8, 4)
        body.matrix_basis = Matrix.Translation((.2, .1, -.05)) @ Matrix.Rotation(.2, 4, 'Z') @ Matrix.Scale(1.2, 4)
        for record in records.values(): record['enabled'] = True
        twist._write_records(body, records)
        bpy.context.view_layer.update()
        return rig, body, records

    def read(rig, body):
        before = (scope['author'](rig, body), tuple(v for obj in (rig, body) for row in obj.matrix_world for v in row))
        try: return exporter.rig_source_snapshot(rig, [rig, body])
        finally:
            after = (scope['author'](rig, body), tuple(v for obj in (rig, body) for row in obj.matrix_world for v in row))
            assert before == after, 'Definition read changed fixture author/context/cached-world data'

    def changed(mutation):
        rig, body, records = fixture()
        before = scope['digest'](read(rig, body))
        mutation(rig, body, records)
        assert scope['digest'](read(rig, body)) != before, 'Authored definition change was omitted'

    def refused(mutation):
        rig, body, records = fixture()
        read(rig, body)
        mutation(rig, body, records)
        try: read(rig, body)
        except ValueError: return
        raise AssertionError('Unsupported Forearm definition was admitted')

    def check(name, callback):
        row = {'name': name}
        try: callback(); row['passed'] = True
        except Exception: row.update(passed=False, error=traceback.format_exc())
        report['checks'].append(row)

    def rest_recipe():
        rig, body, _ = fixture()
        definition = read(rig, body)
        assert definition['schema'] == 'cdesigner.rig-source-definition/2', 'Expected explicit new recipe schema'
        mesh_from_arm = body.matrix_basis.inverted() @ body.matrix_parent_inverse.inverted()
        payload = definition['objects'][body.name]['mesh']['forearm_definition']
        recipe = payload['rest_frame_definition']
        assert recipe['schema'] == 'cdesigner.forearm-source-rest-frame/1'
        assert recipe['recipe'] == 'inverse(mesh.matrix_basis) @ inverse(mesh.matrix_parent_inverse)'
        assert recipe['parent'] == rig.name and recipe['mesh'] == body.name and recipe['parent_type'] == 'OBJECT'
        assert recipe['mesh_and_rig_object_constraints'] == 'none'
        assert max(abs(recipe['matrix'][row][column] - mesh_from_arm[row][column]) for row in range(4) for column in range(4)) < 1e-7
        sides = payload['sides']
        for side in sides:
            lower, hand = rig.data.bones[side['lowerBone']], rig.data.bones[side['handBone']]
            axis = (mesh_from_arm.to_3x3() @ (lower.tail_local - lower.head_local)).normalized()
            pivot = mesh_from_arm @ hand.head_local
            assert (Vector(tuple(side['axis'][key] for key in 'xyz')) - axis).length < 1e-6
            assert (Vector(tuple(side['pivot'][key] for key in 'xyz')) - pivot).length < 1e-6
    check('explicit_definition2_authored_parent_native_rest_recipe', rest_recipe)

    def basis(rig, body, records): body.matrix_basis.translation.x += .02
    def parent_inverse(rig, body, records): body.matrix_parent_inverse.translation.y += .02
    check('body_basis_change_detected', lambda: changed(basis))
    check('body_parent_inverse_change_detected', lambda: changed(parent_inverse))
    check('rig_global_transform_stays_in_definition', lambda: changed(lambda rig, body, records: setattr(rig.location, 'z', rig.location.z + .02)))

    def native_rest(rig, body, records):
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode='EDIT')
        rig.data.edit_bones['Lower.L'].tail.y += .01
        bpy.ops.object.mode_set(mode='OBJECT')
    check('native_rest_tail_change_changes_definition', lambda: changed(native_rest))
    check('unknown_parent_refuses', lambda: refused(lambda rig, body, records: setattr(body, 'parent', None)))
    def bone_parent(rig, body, records): body.parent_type, body.parent_bone = 'BONE', 'Lower.L'
    check('bone_parent_refuses', lambda: refused(bone_parent))
    check('body_constraint_refuses', lambda: refused(lambda rig, body, records: body.constraints.new('COPY_LOCATION')))
    check('rig_object_constraint_refuses', lambda: refused(lambda rig, body, records: rig.constraints.new('COPY_LOCATION')))
    check('wrong_armature_binding_refuses', lambda: refused(lambda rig, body, records: setattr(body.modifiers[0], 'object', None)))

    def flags():
        rig, body, records = fixture()
        initial = read(rig, body)
        assert {side['name']: side['enabled'] for side in initial['objects'][body.name]['mesh']['forearm_definition']['sides']} == {'L': True, 'R': True}
        for side in ('L', 'R'):
            before = scope['digest'](read(rig, body))
            records[side]['enabled'] = False
            twist._write_records(body, records)
            assert scope['digest'](read(rig, body)) != before
        assert {side['name']: side['enabled'] for side in read(rig, body)['objects'][body.name]['mesh']['forearm_definition']['sides']} == {'L': False, 'R': False}
    check('literal_left_and_right_enabled_flags_detected', flags)

    def unused_authored_record():
        rig, body, records = fixture()
        portable = importlib.import_module('character_designer.unity_forearm')
        before_definition = scope['digest'](read(rig, body))
        before_capture = portable.capture(body)
        records['L']['fingerprint_author_input_witness'] = 'changed'
        twist._write_records(body, records)
        assert portable.capture(body) == before_capture, 'Witness field unexpectedly changed flattened export calibration'
        definition = read(rig, body)
        assert definition['objects'][body.name]['mesh']['forearm_definition']['authored_calibration_records']['L']['fingerprint_author_input_witness'] == 'changed'
        assert scope['digest'](definition) != before_definition, 'Unused authored calibration field was omitted'
        del records['L']['fingerprint_author_input_witness']
        twist._write_records(body, records)
        assert scope['digest'](read(rig, body)) == before_definition, 'Restoring authored records failed to restore definition'
    check('unused_authored_calibration_field_retained', unused_authored_record)

    def pose_frame():
        rig, body, _ = fixture()
        before = scope['digest'](read(rig, body))
        rig.pose.bones['Lower.L'].rotation_mode = 'XYZ'
        rig.pose.bones['Lower.L'].rotation_euler.z += .3
        bpy.context.scene.frame_current = 37
        bpy.context.view_layer.update()
        assert scope['digest'](read(rig, body)) == before, 'Pose/frame leaked into native Rest recipe'
    check('pose_and_frame_independent', pose_frame)

    def unlinked_library():
        rig, body, _ = fixture()
        before_definition = read(rig, body)
        before = scope['digest'](before_definition)
        def authored_matrices():
            return {name: [[float(value) for value in row] for row in matrix] for name, matrix in (
                ('rig_basis', rig.matrix_basis), ('mesh_basis', body.matrix_basis), ('mesh_parent_inverse', body.matrix_parent_inverse))}
        before_authored = authored_matrices()
        names = (rig.name, body.name)
        with tempfile.TemporaryDirectory(prefix='forearm-rest-library-', dir=result.parent) as directory:
            library = Path(directory) / 'owned_forearm.blend'
            bpy.data.libraries.write(str(library), {rig, body}, path_remap='ABSOLUTE', fake_user=False, compress=False)
            bpy.ops.wm.read_factory_settings(use_empty=True)
            with bpy.data.libraries.load(str(library), link=False) as (_source, destination):
                destination.objects = list(names)
            rig, body = (bpy.data.objects[name] for name in names)
            assert not rig.users_scene and not body.users_scene, 'Test accidentally linked the library objects'
            # Do not assign matrix_world/reset matrix_basis: those setters may
            # re-decompose authored TRS at float32 precision. Natural unlinked
            # loading alone reproduces the world-cache behavior under review.
            report['library_authored_matrices_exact'] = authored_matrices() == before_authored
            assert report['library_authored_matrices_exact'], 'Library fixture changed authored matrices'
            stable = body.matrix_basis.inverted() @ body.matrix_parent_inverse.inverted()
            cached = body.matrix_world.inverted() @ rig.matrix_world
            residual = max(abs(stable[row][column] - cached[row][column]) for row in range(4) for column in range(4))
            report['unlinked_cached_world_residual'] = residual
            assert residual > 1e-4, 'Fixture did not produce the intended stale native world cache'
            after_definition = read(rig, body)
            if scope['digest'](after_definition) != before:
                changes = []
                def diff(old, new, path=''):
                    if len(changes) >= 40: return
                    if type(old) is not type(new): changes.append({'path': path, 'before': old, 'after': new})
                    elif isinstance(old, dict):
                        for key in sorted(old.keys() | new.keys()):
                            if key not in old or key not in new: changes.append({'path': path + '/' + key, 'before': old.get(key), 'after': new.get(key)})
                            else: diff(old[key], new[key], path + '/' + key)
                    elif isinstance(old, (list, tuple)):
                        if len(old) != len(new): changes.append({'path': path, 'before': old, 'after': new})
                        else:
                            for index, (a, b) in enumerate(zip(old, new)): diff(a, b, path + '/' + str(index))
                    elif old != new: changes.append({'path': path, 'before': old, 'after': new})
                diff(before_definition, after_definition)
                report['library_definition_differences'] = changes
                raise AssertionError('Unlinked cached-world data changed the portable definition')
    check('unlinked_private_library_ignores_cached_world', unlinked_library)
    report['source_unchanged'] = sha(SOURCE) == report['source_sha256']
    report['passed'] = report['source_unchanged'] and all(row['passed'] for row in report['checks'])
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'result': str(result)}))
    if not report['passed']: raise RuntimeError('Forearm Rest definition check failed; inspect retained result')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    main(args.result.resolve())
