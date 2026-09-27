"""Run with Blender --background --python tools/convert_tatuus.py -- INPUT_FBX OUTPUT_GLB.

Converts the locally unpacked Tatuus FA01; does not alter the source assets.
Keeps four wheel pivots, removes duplicate cockpit/blur meshes, bakes +X forward.
"""
import configparser
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector

source, destination = map(Path, sys.argv[sys.argv.index('--') + 1:][:2])
source, destination = source.resolve(), destination.resolve()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.fbx(filepath=str(source), use_anim=False)
ini = configparser.ConfigParser(interpolation=None, strict=False)
ini.read(str(source) + '.ini')
textures = {p.name.lower(): p for p in (source.parent / 'texture').iterdir()}
for section in ini.sections():
    if not section.startswith('MATERIAL_') or section == 'MATERIAL_LIST':
        continue
    data = ini[section]
    mat = bpy.data.materials.get(data['NAME'])
    if mat is None:
        continue
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    shader = nodes.new('ShaderNodeBsdfPrincipled')
    shader.inputs['Roughness'].default_value = .55
    shader.inputs['Metallic'].default_value = .05
    output = nodes.new('ShaderNodeOutputMaterial')
    links.new(shader.outputs['BSDF'], output.inputs['Surface'])
    for i in range(int(data.get('RESCOUNT', 0))):
        if data.get(f'RES_{i}_NAME') != 'txDiffuse':
            continue
        path = textures.get(data[f'RES_{i}_TEXTURE'].lower())
        if not path:
            continue
        image = bpy.data.images.load(str(path), check_existing=True)
        size = 2048 if data['NAME'] == 'RT_Skin' else 1024
        if max(image.size) > size:
            factor = size / max(image.size)
            image.scale(int(image.size[0]*factor), int(image.size[1]*factor))
        tex = nodes.new('ShaderNodeTexImage'); tex.image = image
        links.new(tex.outputs['Color'], shader.inputs['Base Color'])
        if int(data.get('ALPHABLEND', 0)) or int(data.get('ALPHATEST', 0)):
            links.new(tex.outputs['Alpha'], shader.inputs['Alpha'])
            mat.surface_render_method = 'DITHERED'

groups = {}
rotation = Matrix.Rotation(math.pi/2, 4, 'Z')
for obj in list(bpy.context.scene.objects):
    if obj.type != 'MESH':
        continue
    ancestors = [obj]
    while ancestors[-1].parent:
        ancestors.append(ancestors[-1].parent)
    names = [o.name.upper() for o in ancestors]
    if any('RIM_BLUR' in n or n in ('COCKPIT_LR','STEER_LR','CINTURE_OFF') for n in names):
        continue
    wheel = next((n for n in names if n.startswith('WHEEL_')), 'Body')
    mesh = obj.data.copy()
    mesh.transform(rotation @ obj.matrix_world)
    new = bpy.data.objects.new('converted', mesh)
    bpy.context.collection.objects.link(new)
    groups.setdefault(wheel, []).append(new)
for obj in list(bpy.context.scene.objects):
    if not obj.name.startswith('converted'):
        bpy.data.objects.remove(obj, do_unlink=True)

for name, objects in groups.items():
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects: obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    obj = bpy.context.object
    obj.name = name
    if name != 'Body':
        bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')

meshes = list(bpy.context.scene.objects)
corners = [o.matrix_world @ Vector(v) for o in meshes for v in o.bound_box]
bottom = min(v.z for v in corners)
for obj in meshes: obj.location.z -= bottom
bpy.context.view_layer.update()
triangles = sum(sum(len(p.vertices)-2 for p in o.data.polygons) for o in meshes)
destination.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.export_scene.gltf(filepath=str(destination), export_format='GLB', export_animations=False,
                         export_yup=True, export_apply=True)
print('EXPORT', destination, 'triangles', triangles, 'nodes', [(o.name,tuple(o.dimensions)) for o in meshes])

# A small local preview also checks textures and FBX orientation before browser use.
scene=bpy.context.scene
scene.render.engine='CYCLES'; scene.cycles.samples=16
scene.render.resolution_x=900; scene.render.resolution_y=600; scene.render.resolution_percentage=100
scene.world.color=(.3,.3,.3)
bpy.ops.object.camera_add(location=(6,-6,4))
camera=bpy.context.object
camera.rotation_euler=(Vector((0,0,.4))-camera.location).to_track_quat('-Z','Y').to_euler()
camera.data.type='ORTHO'; camera.data.ortho_scale=6.2; scene.camera=camera
bpy.ops.object.light_add(type='AREA', location=(1,-3,6))
bpy.context.object.data.energy=1500; bpy.context.object.data.shape='DISK'; bpy.context.object.data.size=5
preview = Path(__file__).resolve().parents[1] / 'data/runtime/tatuus-preview.png'
preview.parent.mkdir(parents=True, exist_ok=True)
scene.render.filepath=str(preview)
bpy.ops.render.render(write_still=True)
