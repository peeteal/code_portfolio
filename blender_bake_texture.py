import bpy, os, math
from mathutils import Vector
#TODO: BAKE TRANSFORMS BEFORE BAKING TEXTURE IN THE FUTURE!
def bake_texture(meters_per_pixel = 0.5, output_dir = "//tmp_topdown"):
    # ---------------- USER SETTINGS ----------------
    IMAGE_SIZE = 2048                     # output resolution (square)
    #OUTPUT_DIR = "//tmp_topdown"         # Blender-relative (//) or absolute
    PADDING = 1.05                        # extra margin around object in ortho camera (used only for warnings)
    USE_TRANSPARENT_BG = True
    RENDER_ENGINE = 'BLENDER_EEVEE_NEXT'              # 'CYCLES' or 'BLENDER_EEVEE'

    # Set the meters-per-pixel you want (world units per image pixel).
    # Example: 0.005 => 5 mm per pixel. Adjust this to taste.
    METERS_PER_PIXEL = meters_per_pixel
    # optionally override minimal camera height; we will compute a safe height from scene if needed
    MIN_CAM_HEIGHT = 10.0
    # ------------------------------------------------

    scene = bpy.context.scene
    sel_objs = [o for o in bpy.context.selected_objects if o.type == 'MESH']
    if not sel_objs:
        raise RuntimeError("No mesh objects selected. Select at least one mesh.")

    for obj in sel_objs:
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
        
    # ensure output dir exists
    out_dir = bpy.path.abspath(output_dir)
    os.makedirs(out_dir, exist_ok=True)

    # backup scene settings to restore later
    orig_engine = scene.render.engine
    orig_camera = scene.camera
    orig_resolution_x = scene.render.resolution_x
    orig_resolution_y = scene.render.resolution_y
    orig_film_transparent = getattr(scene.render, "film_transparent", False)

    scene.render.engine = RENDER_ENGINE
    scene.render.resolution_x = IMAGE_SIZE
    scene.render.resolution_y = IMAGE_SIZE
    if hasattr(scene.render, "film_transparent"):
        scene.render.film_transparent = USE_TRANSPARENT_BG

    # create a single ortho camera for all renders
    cam_data = bpy.data.cameras.new("TD_Ortho_Cam")
    cam_obj = bpy.data.objects.new("TD_Ortho_Cam", cam_data)
    cam_data.type = 'ORTHO'
    scene.collection.objects.link(cam_obj)

    # helper: world bbox (min,max) for an object
    def world_bbox_minmax(obj):
        local_bbox = [Vector(b) for b in obj.bound_box]
        world_coords = [obj.matrix_world @ v for v in local_bbox]
        mins = Vector((min([v.x for v in world_coords]),
                    min([v.y for v in world_coords]),
                    min([v.z for v in world_coords])))
        maxs = Vector((max([v.x for v in world_coords]),
                    max([v.y for v in world_coords]),
                    max([v.z for v in world_coords])))
        return mins, maxs

    # compute global maximum diagonal (to pick a safe camera height)
    global_max_diag = 0.0
    for obj in sel_objs:
        mins, maxs = world_bbox_minmax(obj)
        diag = (maxs - mins).length
        if diag > global_max_diag:
            global_max_diag = diag
        


    # derive ortho scale from meters-per-pixel
    ortho_scale = METERS_PER_PIXEL * IMAGE_SIZE
    if ortho_scale <= 0:
        raise RuntimeError("ORTHO_SCALE computed <= 0. Set METERS_PER_PIXEL and IMAGE_SIZE appropriately.")

    # choose camera height large enough to clear objects (distance doesn't affect ortho scale)
    cam_height = max(global_max_diag * 2.0, MIN_CAM_HEIGHT)

    # apply fixed ortho scale once
    cam_data.ortho_scale = ortho_scale

    # set camera pointing straight down (camera -Z looks to world -Z when rotation is identity)
    cam_obj.rotation_euler = (0.0, 0.0, 0.0)

    # For safety, store mapping from object -> original materials
    orig_materials = {}
    temp_materials_created = []

    try:
        for obj in sel_objs:
            # store original materials
            orig_materials[obj.name] = [slot.material for slot in obj.material_slots]
            #bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
            # duplicate materials and convert duplicates to emission-based materials that sample the same image node (if present)
            new_mats = []
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None:
                    new_mats.append(None)
                    continue

                new = mat.copy()
                new.name = f"{mat.name}_TDemit"
                temp_materials_created.append(new)

                # Ensure nodes exist
                new.use_nodes = True
                nt = new.node_tree

                # find Material Output node
                output = None
                for n in nt.nodes:
                    if n.type == 'OUTPUT_MATERIAL':
                        output = n
                        break
                if not output:
                    output = nt.nodes.new('ShaderNodeOutputMaterial')

                # find an Image Texture node linked (directly) to Principled Base Color, or the first Image Texture node
                image_node = None
                principled = None
                for n in nt.nodes:
                    if n.type == 'BSDF_PRINCIPLED':
                        principled = n
                        break
                if principled:
                    bc = principled.inputs.get('Base Color')
                    if bc and bc.is_linked:
                        from_node = bc.links[0].from_node
                        if from_node.type == 'TEX_IMAGE':
                            image_node = from_node
                if not image_node:
                    for n in nt.nodes:
                        if n.type == 'TEX_IMAGE':
                            image_node = n
                            break

                # create emission shader and wire it
                if output.inputs['Surface'].is_linked:
                    for l in list(output.inputs['Surface'].links):
                        nt.links.remove(l)

                emission = nt.nodes.new('ShaderNodeEmission')
                emission.location = (200, 200)

                if image_node:
                    nt.links.new(image_node.outputs['Color'], emission.inputs['Color'])
                else:
                    if principled:
                        color = principled.inputs['Base Color'].default_value
                        rgb = nt.nodes.new('ShaderNodeRGB')
                        rgb.outputs['Color'].default_value = color
                    else:
                        rgb = nt.nodes.new('ShaderNodeRGB')
                        rgb.outputs['Color'].default_value = (1.0, 1.0, 1.0, 1.0)
                    nt.links.new(rgb.outputs['Color'], emission.inputs['Color'])

                nt.links.new(emission.outputs['Emission'], output.inputs['Surface'])

                new_mats.append(new)

            # assign duplicated materials to the object (keeping slot order)
            for i, new_mat in enumerate(new_mats):
                if new_mat is None:
                    continue
                if i < len(obj.material_slots):
                    obj.material_slots[i].material = new_mat
                else:
                    obj.data.materials.append(new_mat)

            # compute bounding box and center (used to position camera XY)
            mins, maxs = world_bbox_minmax(obj)
            center = (mins + maxs) / 2.0
            size_x = maxs.x - mins.x
            size_y = maxs.y - mins.y
            # warn if object is bigger than camera view (will be cropped)
            max_dim = max(size_x, size_y)
            if max_dim > (cam_data.ortho_scale / PADDING):
                print(f"Warning: object '{obj.name}' max XY size ({max_dim:.4f}) is larger than ortho view ({cam_data.ortho_scale:.4f}). It will be cropped. Increase ORTHO_SCALE or reduce METERS_PER_PIXEL if you need it fully visible.")

            # place camera above object's center keeping fixed Z (constant height relative to object center)
            cam_obj.location = Vector((center.x, center.y, center.z + cam_height))

            # clip planes
            cam_data.clip_start = 0.01
            cam_data.clip_end = cam_height * 4.0

            # set scene camera to ours
            scene.camera = cam_obj

            # render to file
            safe_name = bpy.path.clean_name(obj.name)
            out_path = os.path.join(out_dir, f"{safe_name}_topdown.png")
            scene.render.filepath = out_path

            print(f"Rendering {obj.name} -> {out_path}")
            bpy.ops.render.render(write_still=True)

            # restore original materials for this object
            orig_mats = orig_materials.get(obj.name, [])
            obj.data.materials.clear()
            for m in orig_mats:
                if m is None:
                    obj.data.materials.append(None)
                else:
                    obj.data.materials.append(m)

        print("Top-down renders finished. Files saved to:", out_dir)

    finally:
        # cleanup: delete temporary materials created
        for m in temp_materials_created:
            try:
                if m.users == 0:
                    bpy.data.materials.remove(m, do_unlink=True)
                else:
                    bpy.data.materials.remove(m, do_unlink=True)
            except Exception:
                pass

        # remove camera object/data
        try:
            bpy.data.objects.remove(cam_obj, do_unlink=True)
            bpy.data.cameras.remove(cam_data, do_unlink=True)
        except Exception:
            pass

        # restore scene settings
        scene.render.engine = orig_engine
        scene.render.resolution_x = orig_resolution_x
        scene.render.resolution_y = orig_resolution_y
        if hasattr(scene.render, "film_transparent"):
            scene.render.film_transparent = orig_film_transparent
        scene.camera = orig_camera

if __name__ == "__main__":
    import sys, os, argparse
    import bpy
    from mathutils import Vector

    # ---------------- CLI parsing ----------------
    cli_argv = sys.argv
    if "--" in cli_argv:
        cli_argv = cli_argv[cli_argv.index("--") + 1:]
    else:
        cli_argv = []

    parser = argparse.ArgumentParser(
        description="Top-down bake CLI: specify many objects (name/index/gltf) and per-spec scales"
    )
    parser.add_argument(
        "--input", "-i", dest="objects", nargs="+", help="Object spec(s): name OR 0-based index OR path to .gltf/.glb. Can be repeated.", default=None
    )

    parser.add_argument(
        "--output-dir", "-o", help="Object spec(s): name OR 0-based index OR path to .gltf/.glb. Can be repeated.", default=None
    )

    parser.add_argument(
        "--scale", "-s", dest="scales", nargs="*", help="Scale(s) for specs. Single float for uniform or 'x,y,z' for non-uniform. Can repeat or provide comma-separated list.", default=None
    )
    parser.add_argument("--meters-per-pixel", type=float, help="meters per pixel for bake", default=0.025)
    args = parser.parse_args(cli_argv)

    # ---------------- helpers ----------------
    def parse_scale_token(tok):
        """Parse a single scale token like '1' or '1,0.5,2' -> Vector"""
        tok = str(tok).strip()
        if not tok:
            raise RuntimeError("Empty scale token")
        if "," in tok:
            parts = [p.strip() for p in tok.split(",")]
            if len(parts) != 3:
                raise RuntimeError("Non-uniform scale must have 3 comma-separated values, e.g. '1,0.5,2'")
            try:
                return Vector([float(p) for p in parts])
            except Exception as e:
                raise RuntimeError(f"Failed to parse non-uniform scale '{tok}': {e}")
        else:
            try:
                v = float(tok)
                return Vector((v, v, v))
            except Exception as e:
                raise RuntimeError(f"Failed to parse uniform scale '{tok}': {e}")

    def normalize_object_specs(raw_specs):
        """
        Accepts args.objects (list of strings possibly containing commas) and flattens into a list of specs.
        Example: ['A,B','C'] -> ['A','B','C']
        """
        if not raw_specs:
            return []
        out = []
        for item in raw_specs:
            # split comma-separated but allow filesystem paths containing commas rarely — this is best-effort
            if "," in item and not (item.lower().endswith(".gltf") or item.lower().endswith(".glb")):
                parts = [p.strip() for p in item.split(",") if p.strip()]
                out.extend(parts)
            else:
                out.append(item)
        return out

    def import_gltf_if_needed(spec):
        """Return list of newly imported objects (could be empty)."""
        if spec is None:
            return []
        _, ext = os.path.splitext(spec)
        ext = ext.lower()
        if ext in (".gltf", ".glb"):
            fp = bpy.path.abspath(spec) if spec.startswith("//") else os.path.abspath(spec)
            if not os.path.exists(fp):
                raise RuntimeError(f"glTF file not found: {spec} -> {fp}")
            before = set(bpy.data.objects)
            bpy.ops.import_scene.gltf(filepath=fp)
            after = set(bpy.data.objects)
            new_objs = list(after - before)
            if not new_objs:
                # fallback: try selection-based detection
                new_objs = [o for o in bpy.context.selected_objects if o not in before]
            return new_objs
        return []

    def resolve_spec_to_objs(spec):
        """
        Resolve a single spec into a list of bpy objects:
         - if spec is index string -> [mesh_obj_at_index]
         - if spec is object name -> [object]
         - if spec points to glTF -> list of imported objects
        """
        if spec is None:
            return []

        # try glTF import first by ext
        try:
            _, ext = os.path.splitext(spec)
            if ext.lower() in (".gltf", ".glb"):
                imported = import_gltf_if_needed(spec)
                if not imported:
                    raise RuntimeError(f"No objects imported from '{spec}'")
                return imported
        except RuntimeError:
            raise
        except Exception:
            # not a valid path or import failed silently -> continue
            pass

        # try index
        try:
            idx = int(spec)
            mesh_objs = [o for o in bpy.data.objects if o.type == 'MESH']
            if idx < 0 or idx >= len(mesh_objs):
                raise IndexError("index out of range")
            return [mesh_objs[idx]]
        except ValueError:
            # not an int, treat as name
            obj = bpy.data.objects.get(spec)
            if obj is None:
                raise RuntimeError(f"No object named '{spec}' found.")
            return [obj]
        except IndexError:
            raise RuntimeError(f"No mesh object at index {spec}")

    # ---------------- resolve input specs -> objects ----------------
    input_specs = normalize_object_specs(args.objects)  # flat list or []
    resolved_per_spec = []  # list[list[bpy.types.Object]]

    if input_specs:
        for spec in input_specs:
            objs = resolve_spec_to_objs(spec)
            if not objs:
                raise RuntimeError(f"Spec '{spec}' did not resolve to any objects.")
            resolved_per_spec.append(objs)
    else:
        # no specs supplied: use current selection; if empty, fallback to all mesh objects
        sel_meshes = [o for o in bpy.context.selected_objects if o.type == 'MESH']
        if sel_meshes:
            resolved_per_spec = [[o] for o in sel_meshes]
        else:
            all_meshes = [o for o in bpy.data.objects if o.type == 'MESH']
            if not all_meshes:
                raise RuntimeError("No mesh objects available in the file to bake.")
            # treat each mesh as its own spec
            resolved_per_spec = [[o] for o in all_meshes]

    # flatten list of all resolved objects (preserve order)
    all_resolved_objs = []
    for group in resolved_per_spec:
        for o in group:
            if o not in all_resolved_objs:
                all_resolved_objs.append(o)

    # ---------------- parse scales and map to specs ----------------
    # args.scales can be:
    #  - None -> default 1 for all specs
    #  - multiple tokens (repeatable) -> one per spec
    #  - single token containing commas -> split into tokens matching number of specs
    raw_scales = args.scales or []
    print(raw_scales)
    # flatten comma-separated single argument if provided as one string with commas
    if len(raw_scales) == 1 and "," in raw_scales[0] and len(input_specs) > 1:
        # if user passed something like --scale "1,0.5,2" AND there are multiple specs,
        # interpret that as per-spec scales (not per-component)
        raw_scales = [s.strip() for s in raw_scales[0].split(",") if s.strip()]

    # If user provided exactly one scale token and multiple specs, apply same to all specs.
    if not raw_scales:
        # default uniform 1 for all specs
        per_spec_scales = [Vector((1.0, 1.0, 1.0)) for _ in range(len(resolved_per_spec))]
    elif len(raw_scales) == 1:
        v = parse_scale_token(raw_scales[0])
        per_spec_scales = [v for _ in range(len(resolved_per_spec))]
    elif len(raw_scales) == len(resolved_per_spec):
        per_spec_scales = [parse_scale_token(tok) for tok in raw_scales]
    else:
        raise RuntimeError(f"Scale count ({len(raw_scales)}) does not match number of object specs ({len(resolved_per_spec)}). Provide 1 scale (applies to all) or one scale per spec.")

    # ---------------- apply scales ----------------
    for spec_idx, objs_for_spec in enumerate(resolved_per_spec):
        scale_vec = per_spec_scales[spec_idx]
        for o in objs_for_spec:
            o.scale = scale_vec
            loc = o.location
            print(loc)
            loc.z = 0
            print(loc)
            o.location = loc
            print(f"Applied {scale_vec} to {o.name} {o.scale}")

    # ---------------- selection & active object ----------------
    # clear selection
    try:
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
    except Exception:
        pass

    for o in all_resolved_objs:
        try:
            o.select_set(True)
        except Exception:
            pass

    if not all_resolved_objs:
        raise RuntimeError("No objects resolved to select/scale.")

    bpy.context.view_layer.objects.active = all_resolved_objs[0]

    # ---------------- report + bake ----------------
    def names_list(objs):
        return ", ".join([o.name for o in objs])

    print(f"Resolved and selected objects ({len(all_resolved_objs)}): {names_list(all_resolved_objs)}")
    for i, group in enumerate(resolved_per_spec):
        print(f" Spec {i}: {[o.name for o in group]} -> scale {tuple(per_spec_scales[i])}")

    # now call your existing bake routine which iterates over bpy.context.selected_objects
    bake_texture(meters_per_pixel=args.meters_per_pixel, output_dir=bpy.path.abspath(str(args.output_dir)))
