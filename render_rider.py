import bpy, math, os, sys
from mathutils import Vector

OUT = r'C:\Users\Admin\Documents\garmin\garmin-ai\frontend\public\rider'
os.makedirs(OUT, exist_ok=True)
GLTF = r'C:\Users\Admin\Documents\garmin\garmin-ai\cyclist_on_bike\scene.gltf'

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=GLTF)

meshes = [o for o in bpy.data.objects if o.type == 'MESH']

# --- Decimate: 2.6M polys is overkill for a ~150px sprite; 8x reduction keeps silhouette crisp
mod_count = 0
for o in meshes:
    m = o.modifiers.new('dec', 'DECIMATE')
    m.ratio = 0.12
    mod_count += 1

# --- Combined world bbox
mins = Vector((1e9,)*3); maxs = Vector((-1e9,)*3)
for o in meshes:
    for corner in o.bound_box:
        wc = o.matrix_world @ Vector(corner)
        mins = Vector(map(min, mins, wc)); maxs = Vector(map(max, maxs, wc))
center = (mins + maxs) / 2
dims = maxs - mins
size = max(dims)
print('BBOX size:', tuple(round(d,3) for d in dims))

# --- Acid material: dark body + strong emission in brand green
mat = bpy.data.materials.new('AcidRider')
mat.use_nodes = True
nt = mat.node_tree
nt.nodes.clear()
out_n  = nt.nodes.new('ShaderNodeOutputMaterial')
emis_n = nt.nodes.new('ShaderNodeEmission')
emis_n.inputs['Color'].default_value = (0.718, 0.960, 0.259, 1)
emis_n.inputs['Strength'].default_value = 2.2
diff_n = nt.nodes.new('ShaderNodeBsdfDiffuse')
diff_n.inputs['Color'].default_value = (0.03, 0.05, 0.03, 1)
mix_n  = nt.nodes.new('ShaderNodeMixShader')
fres_n = nt.nodes.new('ShaderNodeFresnel')
fres_n.inputs['IOR'].default_value = 1.45
geo_n  = nt.nodes.new('ShaderNodeNewGeometry')
nt.links.new(fres_n.outputs['Fac'],      mix_n.inputs['Fac'])
nt.links.new(diff_n.outputs['BSDF'],     mix_n.inputs[1])
nt.links.new(emis_n.outputs['Emission'], mix_n.inputs[2])
nt.links.new(mix_n.outputs['Shader'],    out_n.inputs['Surface'])
for m in bpy.data.materials:
    if m is not mat:
        bpy.data.materials.remove(m)
for o in meshes:
    o.data.materials.clear()
    o.data.materials.append(mat)

# --- Sun for gentle shading variation
sun = bpy.data.lights.new('Sun', 'SUN'); sun.energy = 2.5
sun_obj = bpy.data.objects.new('Sun', sun)
bpy.context.collection.objects.link(sun_obj)

scene = bpy.context.scene
scene.render.engine = 'BLENDER_EEVEE'
scene.render.film_transparent = True
scene.render.resolution_x = 560
scene.render.resolution_y = 560
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'

def make_cam(name, loc, rot_z_deg=0.0, look_from=None):
    cd = bpy.data.cameras.new(name); cd.type = 'ORTHO'
    cd.ortho_scale = size * 1.18
    cam = bpy.data.objects.new(name, cd)
    bpy.context.collection.objects.link(cam)
    cam.location = loc
    d = Vector(center) - Vector(loc)
    cam.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
    if rot_z_deg:
        cam.rotation_euler.rotate_axis('Z', math.radians(rot_z_deg))
    return cam

# 'Front' view: what a driver sees when the cyclist approaches/passes (bike heading toward -Y is assumed; verify below)
# We render BOTH candidate orientations so we can pick: front_a (looking from -Y), front_b (looking from +Y)
cams = {
    'top':    make_cam('top',    (center.x, center.y, center.z + size*3)),
    'front_a':make_cam('front_a',(center.x, center.y - size*3, center.z)),
    'front_b':make_cam('front_b',(center.x, center.y + size*3, center.z)),
}

scene.camera = cams['top']
scene.render.filepath = os.path.join(OUT, 'top.png')
bpy.ops.render.render(write_still=True)
print('WROTE', scene.render.filepath)

scene.camera = cams['front_a']
scene.render.filepath = os.path.join(OUT, 'front_a.png')
bpy.ops.render.render(write_still=True)
print('WROTE', scene.render.filepath)

scene.camera = cams['front_b']
scene.render.filepath = os.path.join(OUT, 'front_b.png')
bpy.ops.render.render(write_still=True)
print('WROTE', scene.render.filepath)

# crop stats per image
import numpy as np
for name in ('top.png','front_a.png','front_b.png'):
    p = os.path.join(OUT, name)
    img = bpy.data.images.load(p)
    px = np.array(img.pixels[:]).reshape(img.size[1], img.size[0], 4)
    a = px[:,:,3]
    ys, xs = np.where(a > 0.02)
    if len(xs):
        print('CROP', name, 'x', int(xs.min()), int(xs.max()), 'y', int(ys.min()), int(ys.max()),
              'w', int(xs.max()-xs.min()), 'h', int(ys.max()-ys.min()), 'bytes', os.path.getsize(p))
    else:
        print('CROP', name, 'EMPTY')
print('RENDER_DONE')
