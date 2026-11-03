"""Focused native Add ownership derivative; use a frozen package, never artist.

The inherited model importer fixture builds a tiny real FK skeleton and runs
the real packet baker. The controller reader/writer are real except for one
deliberate owned-copy write failure. No child exporter or Unity is launched.
"""

import argparse
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


def main():
    import bpy
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    if not bpy.app.background or bpy.data.filepath or '--enable-autoexec' not in sys.argv:
        raise RuntimeError('Use a fresh trusted private background process.')
    if args.report.exists():
        raise RuntimeError('Preserve the existing report; choose a new path.')
    sys.path[:0]=[str(args.source_root.resolve().parent),str(Path(__file__).resolve().parent)]
    import character_designer
    if Path(character_designer.__file__).resolve().parent!=args.source_root.resolve():
        raise RuntimeError('The native fixture escaped its explicitly selected package.')
    import test_animation_worklist_blender as fixture
    from character_designer import controller_animation as service
    from character_designer import animation_worklist as worklist, animation_link as links
    from character_designer import animation_link_source as source, unity_animation as ua

    class ImportTests(fixture.WorklistTests):
        def test_controller_zero_generated_retains_native_FK_and_linked_Source(self):
            row=self.add()
            self.assertIsNotNone(row.source_action.library)
            self.assertIsNot(row.custom_action,row.source_action)
            self.assertNotIn(service.RECORD,row.custom_action)
            self.assertEqual(fixture.action_content(row.custom_action),fixture.action_content(row.source_action))
            self.assertIn('Native FK controls are retained',worklist.state(bpy.context).status)
            self.assertEqual(fixture.digest(row.source_file),row.source_hash)
            self.assert_original_unchanged()

        def legacy(self):
            imported=source.import_source(bpy.context,self.links[0],str(self.model))
            edited=imported.action
            curve=next(c for c in fixture.animation_retarget._curves(edited)
                       if 'forearm.L' in c.data_path and 'rotation' in c.data_path)
            curve.keyframe_points[-1].co.y+=.25
            curve.update()
            ua.restore_preview(bpy.context,imported.rig)
            edited['artist_note']='Keep this original edited Action and all its keys.'
            edited.use_fake_user=False
            worklist.connect(bpy.context,str(self.workspace))
            return imported.rig,edited

        def test_controller_legacy_edit_is_retained_then_copy_is_adopted(self):
            rig,edited=self.legacy()
            before=(edited.name,fixture.action_content(edited),dict(edited.items()),edited.as_pointer())
            row=self.add()
            self.assertIs(row.rig,rig)
            self.assertIsNot(row.custom_action,edited)
            self.assertEqual(fixture.action_content(row.custom_action),before[1])
            self.assertEqual((edited.name,fixture.action_content(edited),dict(edited.items()),edited.as_pointer()),before)
            self.assertTrue(edited.use_fake_user)
            self.assertIs(rig[links.ACTION_KEY],row.custom_action)
            self.assertEqual(self.model_imports,1)
            self.assert_original_unchanged()

        def test_controller_failed_owned_copy_write_rolls_back_Add(self):
            rig,edited=self.legacy()
            before=(fixture.action_content(edited),dict(edited.items()),bool(edited.use_fake_user),
                    dict(rig.items()),rig.data,ua._snapshot(bpy.context,rig))
            ids=set(bpy.data.user_map())
            files=set(self.folder.rglob('*.blend'))
            def failed(action,handle,built):
                self.assertIsNot(action,edited)
                self.assertEqual(action.users-int(action.use_fake_user),0)
                action['partial_private_controller_write']=True
                raise ValueError('injected owned-copy writer failure')
            with patch.object(service,'apply_to_action',side_effect=failed):
                with self.assertRaisesRegex(ValueError,'owned-copy writer failure'):
                    self.add()
            self.assertFalse(worklist.state(bpy.context).items)
            self.assertEqual((fixture.action_content(edited),dict(edited.items()),bool(edited.use_fake_user),
                              dict(rig.items()),rig.data,ua._snapshot(bpy.context,rig)),before)
            self.assertEqual(set(bpy.data.user_map()),ids)
            self.assertEqual(set(self.folder.rglob('*.blend')),files)
            self.assert_original_unchanged()

    suite=unittest.TestSuite(ImportTests(name) for name in unittest.defaultTestLoader.getTestCaseNames(ImportTests)
                             if name.startswith('test_controller_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    payload=dict(schema='x.controller-animation.import-native/1',passed=result.wasSuccessful(),
        tests=result.testsRun,errors=[str(e) for _test,e in result.errors],
        failures=[str(e) for _test,e in result.failures],source_root=str(args.source_root.resolve()),
        limits=['Tiny native FK fixture does not prove generated-limb trajectory quality; the backend suite owns it.',
                'Model import boundary is inherited and explicit; this is not artist Original acceptance.',
                'Captured Rigging remains on its existing special route; no generic IK claim is made.'],
        artist_operations=False,child_workers=False)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    with args.report.open('x',encoding='utf8') as stream:json.dump(payload,stream,indent=2)
    character_designer.unregister()
    if not result.wasSuccessful():
        raise RuntimeError('Native controller import integration failed; preserve its report.')


if __name__=='__main__':
    main()
