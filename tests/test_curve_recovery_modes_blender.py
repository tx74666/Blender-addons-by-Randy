"""Recover actual converted profiles, including their longitudinal connectivity."""
import sys, unittest
from pathlib import Path
from unittest.mock import patch
import bpy,bmesh
from mathutils.kdtree import KDTree

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'addons'),str(ROOT/'tests')]
import character_designer as cd
from character_designer import curve_tools
import test_character_designer_blender as fixtures


def vertices(obj):
    evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh=evaluated.to_mesh()
    try: return [v.co.copy() for v in mesh.vertices]
    finally: evaluated.to_mesh_clear()


def geometry(obj):
    evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh=evaluated.to_mesh()
    try:
        return (
            [v.co.copy() for v in mesh.vertices],
            {frozenset(edge.vertices) for edge in mesh.edges},
            {frozenset(face.vertices) for face in mesh.polygons},
        )
    finally: evaluated.to_mesh_clear()


class RecoveryModes(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cd.register()

    @classmethod
    def tearDownClass(cls): cd.unregister()

    def setUp(self):
        fixtures.reset_scene()
        self.settings=bpy.context.window_manager.character_designer
        self.settings.ui_page='MODELING'
        self.settings.curve_tools_mode='GENERAL'
        self.settings.recovery_curve_mode='EXACT'
        self.settings.recovery_source_action='ADD'

    def source(self,kind,resolution=2,extrude=0.,*,coordinates=None,tilts=None):
        data=bpy.data.curves.new('Authored profile','CURVE');data.dimensions='3D'
        data.resolution_u=1;data.bevel_depth=.12;data.bevel_resolution=resolution
        data.extrude=extrude
        data.bevel_mode='ROUND'
        data.fill_mode='HALF' if kind=='HALF_ROUND' else 'FULL'
        if kind=='RECTANGLE': cd._configure_rectangle_bevel_profile(data)
        spline=data.splines.new('POLY');spline.points.add(3)
        for i,p in enumerate(spline.points):
            p.co=(*coordinates[i],1) if coordinates is not None else (0,0,i*.6,1)
            p.radius=(1.,.9,.7,.5)[i]
            if tilts is not None: p.tilt=tilts[i]
        obj=bpy.data.objects.new('Authored profile',data)
        bpy.context.scene.collection.objects.link(obj)
        obj.select_set(True);bpy.context.view_layer.objects.active=obj
        bpy.context.view_layer.update()
        return obj,curve_tools.mesh_copy(bpy.context)

    def select_source(self,mesh):
        fixtures.select_vertices(mesh,range(len(mesh.data.vertices)))
        bm=bmesh.from_edit_mesh(mesh.data)
        start=min(v.co.z for v in bm.verts)
        edge=next(e for e in bm.edges if all(abs(v.co.z-start)<1e-5 for v in e.verts))
        bm.select_history.clear();bm.select_history.add(edge)
        bmesh.update_edit_mesh(mesh.data,loop_triangles=False,destructive=False)

    def recover(self,kind,resolution=2,extrude=0.):
        source,mesh=self.source(kind,resolution,extrude)
        original=vertices(mesh)
        self.select_source(mesh)
        result=bpy.ops.character_designer.recover_applied_curve()
        self.assertEqual(result,{'FINISHED'})
        curves=[o for o in bpy.context.scene.objects if cd._is_recovered_curve(o)]
        self.assertEqual(len(curves),1)
        recovered=curves[0]
        if not (kind=='RECTANGLE' and resolution==0):
            self.assertEqual(recovered[cd.RECOVERY_PROFILE_KEY],kind)
        self.assertEqual(recovered.data.fill_mode,'HALF' if kind=='HALF_ROUND' else 'FULL')
        self.assertIn(mesh.name,bpy.context.scene.objects)
        self.assertIn(source.name,bpy.context.scene.objects)
        points=vertices(recovered)
        self.assertEqual(len(points),len(original))
        tree=KDTree(len(points))
        for i,v in enumerate(points): tree.insert(v,i)
        tree.balance()
        self.assertLess(max(tree.find(v)[2] for v in original),2e-5)

    def test_half_round_general_recovers_half(self): self.recover('HALF_ROUND')
    def test_full_round_general_recovers_full(self): self.recover('ROUND_FULL')
    def test_rectangle_general_recovers_rectangle(self): self.recover('RECTANGLE')

    def assert_connected_geometry(self,original,recovered):
        source_points,source_edges,source_faces=original
        points,edges,faces=geometry(recovered)
        self.assertEqual(len(points),len(source_points))
        tree=KDTree(len(source_points))
        for i,v in enumerate(source_points): tree.insert(v,i)
        tree.balance()
        mapping={}
        for i,point in enumerate(points):
            _co,nearest,distance=tree.find(point)
            self.assertLess(distance,2e-5)
            mapping[i]=nearest
        self.assertEqual(len(set(mapping.values())),len(source_points),
                         'Recovered points do not have a one-to-one source correspondence')
        mapped_edges={frozenset(mapping[i] for i in edge) for edge in edges}
        mapped_faces={frozenset(mapping[i] for i in face) for face in faces}
        self.assertEqual(mapped_edges,source_edges,'A recovered edge connects different source columns')
        self.assertEqual(mapped_faces,source_faces,'Recovery changed the surface between source rings')

    def test_bent_round_with_gradual_tilt_preserves_source_columns(self):
        source,mesh=self.source(
            'ROUND_FULL',resolution=4,
            coordinates=((0,0,0),(0,0,.6),(.14,.07,1.2),(.36,.19,1.8)),
            tilts=(.13,.58,1.03,1.48),
        )
        original=geometry(mesh)
        self.select_source(mesh)
        self.assertEqual(bpy.ops.character_designer.recover_applied_curve(),{'FINISHED'})
        recovered=next(o for o in bpy.context.scene.objects if cd._is_recovered_curve(o))
        self.assert_connected_geometry(original,recovered)
        self.assertIn(source.name,bpy.context.scene.objects)

    def test_bent_profiles_with_tilt_preserve_columns_or_refuse_atomically(self):
        # Every fixture comes from a real Poly Curve. Conservative rejection is
        # acceptable if the recovery model cannot fit a profile; accepting it
        # must preserve the actual surface, including inter-ring connections.
        for kind in ('HALF_ROUND','ROUND_FULL','RECTANGLE'):
            for extrude in (0.,.05):
                with self.subTest(kind=kind,extrude=extrude):
                    self.setUp()
                    source,mesh=self.source(
                        kind,resolution=4,extrude=extrude,
                        coordinates=((0,0,0),(0,0,.6),(.14,.07,1.2),(.36,.19,1.8)),
                        tilts=(.13,.58,1.03,1.48),
                    )
                    original=geometry(mesh)
                    source_geometry=geometry(source)
                    source_points=tuple((tuple(point.co),point.radius,point.tilt)
                                        for point in source.data.splines[0].points)
                    self.select_source(mesh)
                    bm=bmesh.from_edit_mesh(mesh.data)
                    selected=tuple(vertex.index for vertex in bm.verts if vertex.select)
                    before=(set(bpy.data.objects),set(bpy.data.meshes),set(bpy.data.curves))
                    result=bpy.ops.character_designer.recover_applied_curve()
                    if result=={'FINISHED'}:
                        recovered=next(o for o in bpy.context.scene.objects if cd._is_recovered_curve(o))
                        self.assert_connected_geometry(original,recovered)
                    else:
                        self.assertEqual(result,{'CANCELLED'})
                        self.assertTrue(self.settings.last_message,
                                        'An unsupported profile must explain why it was refused')
                        self.assertEqual(before,(set(bpy.data.objects),set(bpy.data.meshes),set(bpy.data.curves)))
                        self.assertEqual(bpy.context.mode,'EDIT_MESH')
                        self.assertIs(bpy.context.edit_object,mesh)
                        bm=bmesh.from_edit_mesh(mesh.data)
                        self.assertEqual(tuple(vertex.index for vertex in bm.verts if vertex.select),selected)
                        self.assertEqual([tuple(v) for v in geometry(mesh)[0]],
                                         [tuple(v) for v in original[0]])
                    self.assertEqual(tuple((tuple(point.co),point.radius,point.tilt)
                                           for point in source.data.splines[0].points),source_points)
                    self.assertEqual([tuple(v) for v in geometry(source)[0]],
                                     [tuple(v) for v in source_geometry[0]])
                    self.assertIn(source.name,bpy.context.scene.objects)

    def test_tiny_extrude_is_exact_or_refused_without_writes(self):
        for kind in ('HALF_ROUND','ROUND_FULL','RECTANGLE'):
            for extrude in (1e-8,1e-6):
                with self.subTest(kind=kind,extrude=extrude):
                    self.setUp()
                    source,mesh=self.source(kind,extrude=extrude)
                    original=geometry(mesh)
                    self.select_source(mesh)
                    bm=bmesh.from_edit_mesh(mesh.data)
                    selected=tuple(vertex.index for vertex in bm.verts if vertex.select)
                    before=(set(bpy.data.objects),set(bpy.data.meshes),set(bpy.data.curves))
                    result=bpy.ops.character_designer.recover_applied_curve()
                    if result=={'FINISHED'}:
                        recovered=next(o for o in bpy.context.scene.objects if cd._is_recovered_curve(o))
                        self.assert_connected_geometry(original,recovered)
                    else:
                        self.assertEqual(result,{'CANCELLED'})
                        self.assertEqual(before,(set(bpy.data.objects),set(bpy.data.meshes),set(bpy.data.curves)))
                        self.assertEqual(bpy.context.mode,'EDIT_MESH')
                        self.assertIs(bpy.context.edit_object,mesh)
                        bm=bmesh.from_edit_mesh(mesh.data)
                        self.assertEqual(tuple(vertex.index for vertex in bm.verts if vertex.select),selected)
                        self.assertEqual([tuple(v) for v in geometry(mesh)[0]],
                                         [tuple(v) for v in original[0]])
                    self.assertIn(source.name,bpy.context.scene.objects)

    def test_resolution_and_extrude_geometry_matrix(self):
        for kind in ('HALF_ROUND','ROUND_FULL','RECTANGLE'):
            for resolution in (0,1,4):
                for extrude in (0.,.05):
                    with self.subTest(kind=kind,resolution=resolution,extrude=extrude):
                        self.setUp()
                        self.recover(kind,resolution,extrude)

    def test_recovery_failure_keeps_mesh_and_selection(self):
        source,mesh=self.source('ROUND_FULL')
        self.select_source(mesh)
        obj,bm,records=cd._infer_selected_recovery_components(bpy.context)
        plans=cd._build_recovery_plans(bpy.context,obj,bm,records)
        before=set(bpy.data.objects),set(bpy.data.curves)
        with patch.object(cd,'_configure_recovered_curve_data',side_effect=RuntimeError('injected curve write')):
            with self.assertRaisesRegex(RuntimeError,'injected'):
                cd._commit_recovered_curve_batch(bpy.context,plans)
        self.assertEqual(before,(set(bpy.data.objects),set(bpy.data.curves)))
        self.assertEqual(bpy.context.mode,'EDIT_MESH')
        self.assertIs(bpy.context.edit_object,mesh)


if __name__=='__main__':
    result=unittest.main(argv=[__file__],exit=False).result
    if not result.wasSuccessful(): raise RuntimeError('Curve recovery tests failed')
