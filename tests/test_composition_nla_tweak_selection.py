"""Pure regression for RNA wrapper aliases at Composition's native Tweak boundary."""
import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'addons' / 'character_designer' / 'animation_composition.py'


def load_tweak(source=SOURCE):
    tree = ast.parse(source.read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_tweak')
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


class RNAWrapper:
    """Like StructRNA equality, aliases compare native storage and exact type."""
    def __init__(self, native):
        object.__setattr__(self, '_native', native)

    def __eq__(self, other):
        return type(self) is type(other) and self._native is other._native

    def __getattr__(self, name):
        return getattr(self._native, name)

    def __setattr__(self, name, value):
        if name == 'active':
            raise AssertionError('NlaStrip/NlaTrack.active is readonly')
        if name == 'select' and self._native.ignore_selection:
            return
        setattr(self._native, name, value)


class Strip(RNAWrapper):
    pass


class Track(RNAWrapper):
    @property
    def strips(self):
        return [Strip(s) for s in self._native.strips]


class Tracks:
    def __init__(self, natives):
        self.natives = natives
        self._active = None
        self.ignore_active = False

    def __iter__(self):
        # Every iteration returns new Python objects, including repeated lookups.
        return iter([Track(t) for t in self.natives])

    @property
    def active(self):
        return Track(self._active) if self._active else None

    @active.setter
    def active(self, track):
        if not self.ignore_active:
            self._active = track._native


def native_track(name='same display name', count=1, action=True):
    strips = [SimpleNamespace(action=object() if action else None, action_slot_handle=13,
        select=True, ignore_selection=False, active=False) for _ in range(count)]
    return SimpleNamespace(name=name, strips=strips, select=True, ignore_selection=False,
        mute=False, is_solo=False, active=False)


class Fixture:
    def __init__(self, *, tracks=None):
        self.native = native_track()
        self.other = native_track()
        self.tracks = Tracks(tracks if tracks is not None else [self.other, self.native])
        self.ad = SimpleNamespace(nla_tracks=self.tracks, use_tweak_mode=False,
            action=None, action_slot_handle=0)
        self.rig = SimpleNamespace(animation_data=self.ad, mode='POSE')
        self.context = object()
        self.target = Track(self.native)
        self.calls = []
        self.events = []
        self.result = {'FINISHED'}
        self.raise_enter = False
        self.wrong_action = False
        self.wrong_slot = False
        self.namespace = load_tweak()

        @contextmanager
        def nla_context(context, rig):
            assert context is self.context and rig is self.rig
            self.events.append('context_enter')
            try:
                yield
            finally:
                self.events.append('context_exit')

        self.namespace['_nla_context'] = nla_context
        self.namespace['bpy'] = SimpleNamespace(ops=SimpleNamespace(nla=SimpleNamespace(
            tweakmode_enter=self.enter, tweakmode_exit=self.exit)))

    def enter(self, *args, **kwargs):
        self.calls.append(('enter', args, kwargs))
        # Blender's documented selected-strip fallback on the active track.
        active = self.tracks.active
        selected = [s for s in active.strips if s.select]
        if not selected:
            raise RuntimeError('No active strip(s) to enter tweak mode on')
        assert len(selected) == 1 and active == self.target
        assert not any(t.select or any(s.select for s in t.strips)
                       for t in self.tracks if t != self.target)
        if self.raise_enter:
            raise RuntimeError('native operator failure')
        if self.result == {'FINISHED'}:
            self.ad.use_tweak_mode = True
            self.ad.action = object() if self.wrong_action else selected[0].action
            self.ad.action_slot_handle = 999 if self.wrong_slot else selected[0].action_slot_handle
        return self.result

    def exit(self, *args, **kwargs):
        self.calls.append(('exit', args, kwargs))
        self.ad.use_tweak_mode = False
        return {'FINISHED'}

    def run(self, *, enabled=True):
        return self.namespace['_tweak'](self.context, self.rig, self.target if enabled else None,
                                      enabled=enabled)


class TweakSelectionTests(unittest.TestCase):
    def test_distinct_native_equal_wrappers_enter_exact_full_stack_action_slot(self):
        f = Fixture()
        alias = list(f.tracks)[1]
        self.assertIsNot(alias, f.target)
        self.assertEqual(alias, f.target)
        f.run()
        self.assertEqual(f.calls, [('enter', ('EXEC_DEFAULT', False),
            {'isolate_action': False, 'use_upper_stack_evaluation': True})])
        self.assertTrue(f.ad.use_tweak_mode)
        self.assertIs(f.ad.action, f.native.strips[0].action)
        self.assertEqual(f.ad.action_slot_handle, 13)
        self.assertFalse(f.other.select)
        self.assertFalse(f.other.strips[0].select)
        self.assertEqual(f.events, ['context_enter', 'context_exit'])
        self.assertEqual(f.rig.mode, 'POSE')
        self.assertFalse(f.native.active)  # readonly ACTIVE is never written

    def test_same_named_foreign_track_rejected_before_selection_or_operator(self):
        f = Fixture()
        f.target = Track(native_track())
        with self.assertRaisesRegex(ValueError, 'exact owned track'):
            f.run()
        self.assertEqual(f.calls, [])
        self.assertTrue(f.other.select)
        self.assertIsNone(f.tracks.active)

    def test_multiple_or_missing_strip_and_missing_action_fail_before_writes(self):
        for count, action in [(0, True), (2, True), (1, False)]:
            with self.subTest(count=count, action=action):
                f = Fixture()
                f.native.strips = native_track(count=count, action=action).strips
                with self.assertRaisesRegex(ValueError, 'single Action strip'):
                    f.run()
                self.assertTrue(f.other.select)
                self.assertIsNone(f.tracks.active)
                self.assertEqual(f.calls, [])

    def test_ambiguous_same_native_membership_rejected(self):
        f = Fixture()
        f.tracks.natives.append(f.native)
        with self.assertRaisesRegex(ValueError, 'exact owned track'):
            f.run()
        self.assertEqual(f.calls, [])

    def test_selection_and_active_assignment_readback_fail_before_operator(self):
        for kind in ['strip', 'track', 'active']:
            with self.subTest(kind=kind):
                f = Fixture()
                f.native.select = False
                f.native.strips[0].select = False
                if kind == 'strip':
                    f.native.strips[0].ignore_selection = True
                elif kind == 'track':
                    f.native.ignore_selection = True
                else:
                    f.tracks.ignore_active = True
                with self.assertRaisesRegex(ValueError, 'select the exact composition strip'):
                    f.run()
                self.assertEqual(f.calls, [])

    def test_operator_cancelled_is_not_success(self):
        f = Fixture()
        f.result = {'CANCELLED'}
        with self.assertRaisesRegex(ValueError, 'could not start'):
            f.run()
        self.assertFalse(f.ad.use_tweak_mode)
        self.assertEqual(f.events, ['context_enter', 'context_exit'])

    def test_operator_exception_keeps_context_cleanup_and_original_error(self):
        f = Fixture()
        f.raise_enter = True
        with self.assertRaisesRegex(RuntimeError, 'native operator failure'):
            f.run()
        self.assertEqual(f.events, ['context_enter', 'context_exit'])

    def test_wrong_action_or_slot_cannot_report_success(self):
        for kind in ['action', 'slot']:
            with self.subTest(kind=kind):
                f = Fixture()
                setattr(f, 'wrong_' + kind, True)
                with self.assertRaisesRegex(ValueError, 'could not start'):
                    f.run()

    def test_existing_tweak_rejected_without_selection_changes(self):
        f = Fixture()
        f.ad.use_tweak_mode = True
        with self.assertRaisesRegex(ValueError, 'Another NLA Tweak'):
            f.run()
        self.assertEqual(f.calls, [])
        self.assertTrue(f.other.select)

    def test_exit_and_already_exited_paths_unchanged(self):
        f = Fixture()
        f.run(enabled=False)
        self.assertEqual(f.calls, [])
        f.ad.use_tweak_mode = True
        f.run(enabled=False)
        self.assertEqual(f.calls, [('exit', ('EXEC_DEFAULT', False), {'isolate_action': False})])
        self.assertFalse(f.ad.use_tweak_mode)
        self.assertTrue(f.other.select)


if __name__ == '__main__':
    unittest.main()
