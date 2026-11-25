"""Native same-frame regression. Run using the trusted Blender launcher and the Wiggle 2 fixture arguments."""
from pathlib import Path
import sys
import json
from unittest.mock import patch
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_hair_wiggle2_blender08 as n


def run():
    from mathutils import Quaternion
    import addon_utils
    c = n.bpy.context
    path = n.args.wiggle_file.resolve(strict=True)
    assert n.hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest() == n.skirt.BACKEND_SHA256
    sys.path.insert(0, str(path.parent))
    n.BACKEND = addon_utils.enable('wiggle_2', default_set=False, persistent=False)
    assert n.BACKEND is n.skirt._backend()
    result = {}
    try:
        for initial in (0.0, 1.0):
            source, dress, rig, data, profiles = n.fixture()
            rec = n.skirt_rig.read_record(dress)
            holder, _, _ = n.skirt_rig.physics_control(dress)
            assert not n.control._read(rig)['enabled']
            holder['physics_influence'] = initial
            names = n.dress_names(dress)
            phys = rig.pose.bones[names[0]]
            phys.rotation_mode = 'QUATERNION'
            phys.rotation_quaternion = Quaternion((0, 1, 0), .12)
            handle = rig.pose.bones[rec['controls']['hem']]
            for frame, x in ((8, .0), (16, .24)):
                handle.location.x = x
                handle.keyframe_insert(data_path='location', frame=frame)
            def frame_set(frame):
                c.scene.frame_set(frame)
                c.view_layer.update()
            refs = {}
            for frame in (8, 12, 14, 16):
                frame_set(frame)
                refs[frame] = n.points(dress)
            assert n.delta(refs[8], refs[16]) > 1e-4
            frame_set(8)
            author = n.author(source, dress, rig)
            n.control.set_enabled(c, True)
            n.start_skirt(dress)
            # The historical Hair guard rejects the later footwear UI callback.
            # This fixture has no footwear. Scope that known interaction out of
            # the shared solver test without changing the product Hair guard.
            from character_designer import footwear_display
            n.bpy.app.handlers.frame_change_post.remove(footwear_display.update)
            n.start_hair(source, data, profiles)
            n.advance(4)
            moving = n.delta(refs[12], n.points(dress))
            assert moving > 1e-6, moving
            hair = n.native_state(rig, n.hair_names(data))
            n.control.set_component(c, 'SKIRT', False)
            assert n.native_state(rig, n.hair_names(data)) == hair
            assert n.facade.status()['active'] and not n.facade.status()['paused']
            errors = [n.delta(refs[12], n.points(dress))]
            n.control.set_component(c, 'SKIRT', False)
            n.advance(2)
            errors.append(n.delta(refs[14], n.points(dress)))
            n.control.set_component(c, 'SKIRT', True)
            n.advance(2)
            before = n.points(dress)
            fn = n.control._perform
            def fault(ctx, action, *, reverse=False):
                if action[0] == 'HAIR' and not reverse:
                    raise RuntimeError('injected peer failure')
                return fn(ctx, action, reverse=reverse)
            with patch.object(n.control, '_perform', side_effect=fault):
                try:
                    n.control.set_enabled(c, False)
                except RuntimeError as exc:
                    assert 'injected peer' in str(exc), exc
                else:
                    raise AssertionError('Failed to exercise rollback')
            assert n.delta(before, n.points(dress)) < 2e-6
            assert n.skirt.status(c,dress)['running'] and not n.facade.status()['paused']
            n.control.set_enabled(c, False)
            errors.append(n.delta(refs[16], n.points(dress)))
            assert max(errors) < 2e-6, errors
            assert c.scene.frame_current == 16 and not n.runtime._members(c.scene)
            # A save stops the preview but retains its Automatic choice.
            # Reconnecting that stopped preview must carry the original baseline.
            n.control.set_enabled(c, True)
            n.advance(1)
            n.skirt._save_pre()
            n.skirt._save_post()
            assert not n.skirt.preview_state(dress)['active']
            n.control.set_component(c, 'SKIRT', True)
            n.advance(1)
            n.control.set_component(c, 'SKIRT', False)
            errors.append(n.delta(refs[16], n.points(dress)))
            assert errors[-1] < 2e-6, errors
            n.assert_author(author, source, dress, rig)
            result[str(initial)] = dict(simulated_difference=moving, off_errors=errors, frames=[12,14,16])
        return result
    finally:
        n.cleanup()
        addon_utils.disable('wiggle_2', default_set=False)


if __name__ == '__main__':
    try:
        output = dict(passed=True, cases=run())
    except BaseException:
        output = dict(passed=False, error=traceback.format_exc())
        raise
    finally:
        n.args.report_json.write_text(json.dumps(output, indent=2))
