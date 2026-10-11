"""
photogrammetry_headless_processor.py

Headless-friendly postprocessor for MapsModelsImporter .rdc imports.

Usage patterns:
- Called from an import wrapper that passes the imported collection:
    import photogrammetry_headless_processor as pp
    pp.process(bpy, import_collection, rdc_filepath="/path/to/001_E_530.8909_N_123.4567.rdc")

- Or invoked directly from the console (assumes scene already has the imported collection present
  or the active collection is the one to process):
    blender -b --python photogrammetry_headless_processor.py -- --in "/path/to/001_E_530.8909_N_123.4567.rdc"

Behavior summary:
- Respects the base filename (without .rdc) and uses it to name the created collections/objects:
    <base>_combined and <base>_cleanup
- Parses translations from filenames of the form:
    <shot_index>_E_<y_translation>_N_<x_translation>.rdc
  and sets the final object's X/Y location accordingly (N => X, E => Y).
- Tries to run UV unwrap using the built-in operator; if that fails in headless mode it falls back to a
  simple planar bounding-box UV projection.
- Uses bpy.data.images.new instead of UI operators and saves baked image to disk if requested.

This file is intended to be imported and called by your existing import wrapper (pass --process-script
or call the module directly). Keep absolute paths when running in -b.

"""

import bpy
import os
import re
import datetime
import traceback
import time

# Stripped UI-dependent photogrammetry cleanup script
# Only UI stuff removed (no popup, no VIEW_3D area search). Minimal fallback added for final timing prints.
import math
from mathutils import Vector

def tile_process(index : str):
    starttime = datetime.datetime.now()
    print("Cleanup started at ", starttime)

    # user controls
    bake = True
    res = 8192
    rem_doub = True
    emission_shader = False
    limited_dissolve = False
    hide_old_data = True
    delete_old_data = False
    uv_margin = 0.001
    bake_margin = 1
    cage_extrusion = 0.1 #if the cleanup material texture appears black, increase this
    shade_flat = True
    save_image_disk = True

    target_min = 0.6   # tiles must not fit inside a 0.5m cube
    target_max = 1.4   # each tile should be smaller than 1.5m per axis

    # define collections, user should start with one collection selected in the outliner
    act_coll = bpy.context.view_layer.active_layer_collection.collection

    # check if empty
    if not act_coll.objects:
        raise Exception("Active collection is empty. Please select the correct collection.")

    comb_coll = bpy.data.collections.new(f'{index}_combined')
    clean_coll = bpy.data.collections.new(f'{index}_cleanup')

    # link to scene
    bpy.context.scene.collection.children.link(comb_coll)
    bpy.context.scene.collection.children.link(clean_coll)

    #Recursively traverse layer_collection for a particular name
    def recurLayerCollection(layerColl, collName):
        found = None
        if (layerColl.name == collName):
            return layerColl
        for layer in layerColl.children:
            found = recurLayerCollection(layer, collName)
            if found:
                return found

    # clean out non-mesh objects
    for ob in list(act_coll.objects):
        if ob.type != 'MESH':
            bpy.data.objects.remove(ob, do_unlink=True)

    # create clean mesh collection and copy data into it (unlinked)
    for ob in list(act_coll.objects):
        obj_copy = ob.copy()
        obj_copy.name = 'Mesh Combined'
        obj_copy.data = ob.data.copy()

        # NOTE the use of 'coll.name' to account for potential automatic renaming
        layer_collection = bpy.context.view_layer.layer_collection.children[comb_coll.name]
        bpy.context.view_layer.active_layer_collection = layer_collection
        bpy.context.collection.objects.link(obj_copy)
        
    # create bake collection and copy data into it (unlinked)
    for ob in list(act_coll.objects):
        obj_copy = ob.copy()
        obj_copy.name = 'Mesh Cleanup'
        obj_copy.data = ob.data.copy()

        # NOTE the use of 'collection.name' to account for potential automatic renaming
        layer_collection = bpy.context.view_layer.layer_collection.children[clean_coll.name]
        bpy.context.view_layer.active_layer_collection = layer_collection
        bpy.context.collection.objects.link(obj_copy)

    #delete old data
    if delete_old_data == True:
        print('Step 0 | Deleting duplicate objects.')
        for ob in list(act_coll.objects):
            bpy.data.objects.remove(ob, do_unlink=True)

    def get_world_bbox_xy(obj):
        bbox_world = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        xs = [v.x for v in bbox_world]
        ys = [v.y for v in bbox_world]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        return width, height

    bboxs = []
    for ob in comb_coll.objects:
        bbox = get_world_bbox_xy(ob)
        bboxs.append(bbox)

    sorted_bboxs = sorted(bboxs, key=lambda b: b[0])

    #bboxs = bboxs.sort(key=lambda b: b[0])
    med_bbox = sorted_bboxs[int(len(sorted_bboxs)/2)]

    print(f"Selected median for bbox check: {med_bbox}")

    def compute_scaling_factor_to_standard(bbox):
        # use x coordinate as tile size for factor computation
        factor = 1.0
        # if chosen median is too small, multiply by 2 until within threshold
        while bbox[0] * factor < target_min:
            factor *= 2.0
            if factor > 1024:
                print("[tile normalise] factor exceeded safety cap; breaking")
                break

        # if chosen median is too large, divide by 2 until within threshold
        if factor == 1.0: # has not passed target_min
            while bbox[0] * factor > target_max:
                factor /= 2.0
                if factor < 1.0 / 1024.0:
                    print("[tile normalise] factor lower-bound safety cap reached; breaking")
                    break
        return factor

    factor = compute_scaling_factor_to_standard(med_bbox)

    # set an active object
    # assume comb_coll has at least one object (copied above)
    bpy.context.view_layer.objects.active = comb_coll.objects[0]

    # join objects in bake collection
    print("Step 1 | Joining objects, this could take a little while if there are many objects.")

    for ob in comb_coll.objects:
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
    bpy.ops.object.join()
    bpy.context.object.name = f'{index}_combined'
    bpy.context.object.data.name = f'{index}_combined'

    bpy.ops.object.transform_apply(location=False,rotation=True, scale=True)
    bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')

    comb_coll.objects[0].location = (0,0,0)
    comb_coll.objects[0].scale *= factor
    bpy.ops.object.transform_apply(location=False,rotation=True, scale=True)

    # define objects in new clean collection
    objs = clean_coll.all_objects
    num_objs = len(objs)

    for obj in bpy.context.selected_objects:
        obj.select_set(False)

    # select and join objects
    for ob in objs:        
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
    bpy.ops.object.join()
    bpy.context.object.name = f'{index}_cleanup'
    bpy.context.object.data.name = f'{index}_cleanup'
    bpy.ops.object.transform_apply(location=False,rotation=True, scale=True)
    bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')

    clean_coll.objects[0].location = (0,0,0)
    clean_coll.objects[0].scale *= factor
    bpy.ops.object.transform_apply(location=False,rotation=True, scale=True)

    # remove doubles
    if rem_doub == True:
        print('Step 1.1 | Removing doubles.')
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.remove_doubles(threshold=0.001)
        bpy.ops.object.mode_set(mode='OBJECT')


    idx = 0
    slots_length = len(bpy.context.object.material_slots)

    # remove all new materials from the original object
    print('Step 2 | Removing materials from duplicate.')
    for i in range(0,len(bpy.context.object.material_slots)):
        bpy.context.object.active_material_index = 1
        bpy.ops.object.material_slot_remove()
        print(
        "Step 2 |", idx + 1,
        "/", num_objs,
        "|", round((((idx + 1)/slots_length) * 100),2),"%",
        "| Removed material."  
        )
        idx += 1
        
    bpy.ops.object.mode_set(mode = 'EDIT') 
    bpy.ops.mesh.select_all(action = 'SELECT')
    bpy.ops.object.material_slot_assign()
    bpy.ops.object.mode_set(mode = 'OBJECT')

    # add a material to the clean bake object
    clean = clean_coll.objects[0]
    print('Step 3 | Creating new material.')
    mat = bpy.data.materials.new(name=f'{index}_material')

    # add the material to the object
    clean.data.materials.append(mat)
    mat.use_nodes = True
    principlednode = mat.node_tree.nodes["Principled BSDF"]
    outputnode = mat.node_tree.nodes['Material Output']

    # add an emission shader
    emissionnode = mat.node_tree.nodes.new(type="ShaderNodeEmission")
    emissionnode.location = (500, 0)

    # add image texture
    texture = bpy.ops.image.new(name=f'{index}_texture',width=res,height=res,color=(0.0,0.0,0.0,0.0), alpha=True)

    # add texture node
    texturenode = mat.node_tree.nodes.new(type = 'ShaderNodeTexImage')
    texturenode.image = bpy.data.images[f'{index}_texture']
    texturenode.location = (-300,200)

    # UV Unwrapping
    print('Step 4 | UV Unwrapping.')
    bpy.ops.object.editmode_toggle()
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(island_margin=0)
    bpy.ops.uv.pack_islands(margin=0)
    bpy.ops.object.editmode_toggle()

    # bake settings
    # set renderer to cycles
    bpy.context.scene.render.engine = 'CYCLES'
    try:
        bpy.context.scene.cycles.device = 'CPU'
    except Exception:
        pass
    # influence settings
    bpy.context.scene.render.bake.use_pass_direct = False
    bpy.context.scene.render.bake.use_pass_indirect = False
    bpy.context.scene.render.bake.use_pass_color = True
    # enable selected to active
    bpy.context.scene.render.bake.use_selected_to_active = True
    # set cage distance
    bpy.context.scene.render.bake.cage_extrusion = cage_extrusion
    # set bake margin
    bpy.context.scene.render.bake.margin = bake_margin
    # cycles render settings
    bpy.context.scene.cycles.adaptive_threshold = 1.0
    bpy.context.scene.cycles.samples = 10
    bpy.context.scene.cycles.time_limit = 15

    orig = comb_coll.objects[0]

    # select original
    orig.select_set(True)
    # select active
    clean.select_set(True)

    if shade_flat == True:
        bpy.ops.object.shade_flat()

    # bake
    if bake == True:
        # --- New Logic Here ---
        # First, check for an emission shader in the original materials
        has_emission = False
        for ob in comb_coll.objects:
            for mat_slot in ob.material_slots:
                if mat_slot.material and mat_slot.material.use_nodes:
                    if 'Emission' in mat_slot.material.node_tree.nodes:
                        has_emission = True
                        break
                if has_emission:
                    break
            if has_emission:
                break

        bake_type = 'DIFFUSE'
        if has_emission:
            bake_type = 'EMIT'
            print("Detected emission shader, setting bake type to 'EMIT'.")
        else:
            print("No emission shader detected, setting bake type to 'DIFFUSE'.")

        poly_count = len(bpy.context.object.data.polygons)
        print('Step 5 | Baking texture, this may take some time...')

        print('This process takes about 6 minutes using a upp computer with a 4090 and x3d 7950 desktop on a mesh with 3mil triangles.')
        print('Your mesh has ', poly_count, 'triangles.')
        print('Assuming your computer is as slow as mine, this could take', 
        str(round((poly_count/3800000/45*3600), 2)), 
        "minutes...")
        print(datetime.datetime.now())
        
        bpy.context.object.data.uv_layers[0].active_render = True
        # The actual bake command with the conditional type
        bpy.ops.object.bake(type=bake_type)
        print('The new image is called:',f'{index}_texture')

        # connect nodes
        if emission_shader == True:
            mat.node_tree.links.new(texturenode.outputs[0], emissionnode.inputs[0])
            mat.node_tree.links.new(emissionnode.outputs[0], outputnode.inputs[0])
        else:
            mat.node_tree.links.new(texturenode.outputs[0], principlednode.inputs[0])

    # hide data
    if hide_old_data == True:  
        print('Step 6 | Hiding unused collections.')
        
        # hide original collections when finished    
        layer_collection = bpy.context.view_layer.layer_collection    
        layerColl = recurLayerCollection(layer_collection, act_coll.name)
        bpy.context.view_layer.active_layer_collection = layerColl
        bpy.context.view_layer.active_layer_collection.exclude = True
        
        layer_collection = bpy.context.view_layer.layer_collection 
        layerColl = recurLayerCollection(layer_collection, comb_coll.name)
        bpy.context.view_layer.active_layer_collection = layerColl
        bpy.context.view_layer.active_layer_collection.exclude = True
        
        # give feedback on process duration
        endtime = datetime.datetime.now()
        duration = endtime - starttime

    # delete old data
    if delete_old_data == True:
        print('Step 7 | Deleting old data.')
        for ob in comb_coll.objects:
            bpy.data.objects.remove(ob, do_unlink=True)    

    # safe fallback if hide_old_data was False (to avoid NameError for endtime/duration)
    if 'endtime' not in locals():
        endtime = datetime.datetime.now()
        duration = endtime - starttime

    print('cleanup started at',starttime)
    print('cleanup finished at',endtime)
    print('process duration: ', duration)

# ---------------------------
# Configuration defaults
# ---------------------------
DEFAULTS = {
    'bake': True,
    'res': 8192,
    'rem_doub': True,
    'emission_shader': False,
    'limited_dissolve': False,
    'hide_old_data': True,
    'delete_old_data': False,
    'uv_margin': 0.001,
    'bake_margin': 2,
    'cage_extrusion': 0.01,
    'shade_flat': True,
    'save_image_disk': True,
}

# ---------------------------
# Utility helpers
# ---------------------------

def _parse_translations_from_filename(filename):
    """Parse <shot>_E_<y>_N_<x> from a filename (without extension).
    Returns (x_float, y_float) or (None, None) if parsing fails.
    Note: filename may contain other underscores; this regex looks for the E_ and N_ markers.
    """
    # Allow possible negative and decimal numbers
    rx = re.compile(r'^(?:\+?(\d+(?:\.\d+)?))_E_([-+]?\d+(?:\.\d+)?)_N_([-+]?\d+(?:\.\d+)?)$')

    m = rx.match(filename)
    if m:
        print(m.groups())  # ('1', '0.5000', '10.9000')
    #m = re.search(r'_E_([-\d\.]+)_N_([-\d\.]+)$', filename)
    if not m:
        return None, None
    try:
        i = int(m.group(1))
        y = float(m.group(2))
        x = float(m.group(3))
        return x, y, i
    except Exception:
        return None, None

# ---------------------------
# Main processing function
# ---------------------------
def collect_objects_recursively(coll, objs_set):
    for o in coll.objects:
        objs_set.add(o)
    for child in coll.children:
        collect_objects_recursively(child, objs_set)

def process(bpy_module, import_collection, rdc_filepath=None, options=None, output_folder = "./export"):
    """Process an imported photogrammetry collection.

    Parameters
    - bpy_module: typically pass the `bpy` module
    - import_collection: the collection object that contains imported meshes
    - rdc_filepath: optional path to the original .rdc file (used for naming & parsing translations)
    - options: dict overriding DEFAULTS

    This function is safe to import and call from another script. It will raise exceptions
    on fatal errors so the caller can handle/log them.
    """
    opts = DEFAULTS.copy()
    if options:
        opts.update(options)

    starttime = datetime.datetime.now()
    print("[pp] start:", starttime)

    scene = bpy_module.context.scene

    # Determine base name from rdc_filepath or fallback to collection name
    file = None
    if rdc_filepath:
        print("Reading .rdc file...")
        file = bpy.ops.import_rdc.google_maps(filepath=rdc_filepath)
        t0 = time.time()
        while len(import_collection.objects) == 0 and time.time() - t0 < 60:
            time.sleep(0.5)
            print("Waiting for import")
        #time.sleep(5) # sleep for 5 seconds to wait for load
        base_name = os.path.splitext(os.path.basename(rdc_filepath))[0]
    else:
        print("RDC file not found, using import collection...")
        base_name = import_collection.name

    # parse translations
    tx, ty, index = _parse_translations_from_filename(base_name)
    if tx is None or ty is None:
        print(f"[pp] Translation not found in filename '{base_name}', skipping translate step.")
    else:
        print(f"[pp] Parsed translation X={tx}, Y={ty} from filename.")

    tile_process(index)

    print("Set roughness = 1.0 for all visible mesh materials.")

    
    # get current view layer
    view_layer = bpy.context.view_layer

    # Iterate through all visible mesh objects in the current view layer
    for obj in view_layer.objects:
        if obj.type != 'MESH' or not obj.visible_get():
            continue

        for slot in obj.material_slots:
            mat = slot.material
            if not mat or not mat.use_nodes:
                continue

            # find the Principled BSDF node
            for node in mat.node_tree.nodes:
                if node.type == 'BSDF_PRINCIPLED':
                    node.inputs['Roughness'].default_value = 1.0
                    break

    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    # Apply to all selected objects
    bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
    
    
    obj = bpy.context.active_object
    obj.location.x = -tx # blender coordinate system
    obj.location.y = ty
    obj.location.z = 0


    # find collections not excluded from the current view layer
    visible_collections = []
    for layer_coll in view_layer.layer_collection.children:
        def recurse_layer(layer):
            if not layer.exclude:
                coll = layer.collection
                # check if it has at least one visible object in this view layer
                has_visible_obj = any(o.visible_get() for o in coll.objects)
                if has_visible_obj:
                    visible_collections.append(coll)
                for child in layer.children:
                    recurse_layer(child)
        recurse_layer(layer_coll)

    if len(visible_collections) != 1:
        names = [c.name for c in visible_collections]
        raise RuntimeError(f"Expected exactly 1 visible collection, found {len(visible_collections)}: {names}")

    target_coll = visible_collections[0]

    # gather objects from target collection (including children)
    objs = set()
    collect_objects_recursively(target_coll, objs)
    # filter to exportable types (adjust as needed)
    objs = [o for o in objs if o.type in {'MESH', 'EMPTY', 'LIGHT', 'CAMERA'}]

    if not objs:
        raise RuntimeError(f"No exportable objects found in collection '{target_coll.name}'")

    # deselect all, select target objects
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)

    # set an active object
    bpy.context.view_layer.objects.active = objs[0]

    output_path = f"{output_folder}/{base_name}"
    # Export as separate glTF (produces .gltf, .bin and texture files)
    bpy.ops.export_scene.gltf(
        filepath=output_path,
        export_format='GLTF_SEPARATE',  # <-- separate .gltf/.bin/textures
        collection=f"{index}_cleanup"
    )

    print(f"Exported visible collection '{target_coll.name}' -> {output_path}")
    print("Files produced: .gltf (json), .bin (binary buffers), and texture image files next to the .gltf")

# ---------------------------
# CLI entrypoint
# ---------------------------
if __name__ == '__main__':
    import sys
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--in', dest='inpath', help='original .rdc path (optional)', required=False)
    parser.add_argument('--out', dest='outpath', help='output path')
    parser.add_argument('--collection', default="Collection", dest='collection', help='collection name to process (optional). If omitted, active collection is used.', required=False)
    parser.add_argument('--save-image', dest='save_image', action='store_true', help='force saving baked image (overrides option)') # does nothing atm
    args = parser.parse_known_args(sys.argv[sys.argv.index('--') + 1:])[0] if '--' in sys.argv else None

    # determine collection
    if args and getattr(args, 'collection', None):
        coll_name = args.collection
        if coll_name in bpy.data.collections:
            coll = bpy.data.collections[coll_name]
        else:
            raise SystemExit(f"Collection '{coll_name}' not found in scene.")
    else:
        coll = bpy.context.view_layer.active_layer_collection.collection

    opts = DEFAULTS.copy()
    if args and getattr(args, 'save_image', False):
        opts['save_image_disk'] = True

    process(bpy, coll, rdc_filepath=(args.inpath if args else None), output_folder=args.outpath, options=opts)
