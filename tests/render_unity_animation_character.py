"""Render diagnostic poses from a disposable animation review .blend."""
import argparse
import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
import character_designer
from character_designer import forearm_twist as ft


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--blend', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    bpy.ops.wm.open_mainfile(filepath=args.blend, use_scripts=False, load_ui=False)
    character_designer.register()
    scene = bpy.context.scene
    rig = bpy.data.objects['CoshaRig']
    kept = {'Cosha', 'Clothes', 'Stocking'}
    for obj in scene.objects:
        obj.hide_render = obj.type == 'MESH' and obj.name not in kept or obj.type in {'CURVE', 'FONT'}
    for collection in bpy.data.collections:
        collection.hide_render = False
    for layer in bpy.context.view_layer.layer_collection.children:
        layer.exclude = False
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'MATERIAL'
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.background_type = 'WORLD'
    scene.world.color = (.08, .09, .11)
    scene.render.resolution_x, scene.render.resolution_y = 800, 1000
    scene.render.resolution_percentage = 100
    camera = bpy.data.objects.new('Animation validation camera', bpy.data.cameras.new('Animation validation camera'))
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera.data.type = 'ORTHO'
    depsgraph = bpy.context.evaluated_depsgraph_get()
    coords = []
    for name in kept:
        obj = bpy.data.objects.get(name)
        if not obj:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        coords.extend(evaluated.matrix_world @ Vector(c) for c in evaluated.bound_box)
    low = Vector(tuple(min(c[i] for c in coords) for i in range(3)))
    high = Vector(tuple(max(c[i] for c in coords) for i in range(3)))
    center = (low + high) / 2
    camera.data.ortho_scale = (high.z-low.z)*1.25
    camera.location = center + Vector((1.2, -3.2, .3))
    camera.rotation_euler = (center-camera.location).to_track_quat('-Z', 'Y').to_euler()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    rendered = []
    for frame in (1.0, 1+(.9666666984558106/2)*scene.render.fps/scene.render.fps_base):
        integer = int(frame)
        scene.frame_set(integer, subframe=frame-integer)
        ft.update_runtime(scene, bpy.context.evaluated_depsgraph_get())
        bpy.context.view_layer.update()
        path = output / ('Blender_Walk_%s.png' % ('start' if frame == 1 else 'half'))
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        rendered.append(str(path))
    print(json.dumps({'images': rendered, 'saved_source': False}))


if __name__ == '__main__':
    main()
