import bpy
from mathutils import Vector, Matrix
from mathutils.kdtree import KDTree

# ----------------- Config -----------------
tol_xy = 1e-4         # XY match tolerance (world units)
min_points = 4        # leaf if fewer points than this in either set
max_depth = 12        # max recursion depth
min_pairs_required = 6  # require at least this many pairs to act
eps_z = 1e-6          # treat z difference <= eps_z as 'no difference'
# ------------------------------------------

def world_verts(obj):
    """Return list of (index, Vector(world_xyz)) for object mesh vertices."""
    mesh = obj.data
    wm = obj.matrix_world
    verts = []
    # Ensure up-to-date evaluated mesh if modifiers exist
    deps = bpy.context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(deps)
    mesh_eval = obj_eval.to_mesh()
    for i, v in enumerate(mesh_eval.vertices):
        verts.append((i, wm @ v.co))
    obj_eval.to_mesh_clear()
    return verts

def bbox_xy(verts):
    """Return (minx, miny, maxx, maxy)."""
    xs = [v.x for (_, v) in verts]
    ys = [v.y for (_, v) in verts]
    return min(xs), min(ys), max(xs), max(ys)

def in_cell(v, bounds):
    minx, miny, maxx, maxy = bounds
    return (minx <= v.x <= maxx) and (miny <= v.y <= maxy)

def subdivide_bounds(bounds):
    minx, miny, maxx, maxy = bounds
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    return [
        (minx, miny, cx, cy),  # lower-left
        (cx, miny, maxx, cy),  # lower-right
        (minx, cy, cx, maxy),  # upper-left
        (cx, cy, maxx, maxy),  # upper-right
    ]

def process_leaf(static_pts, moving_pts, tol_xy, pairs):
    """Attempt to match moving_pts to static_pts by XY using KDTree."""
    if not static_pts or not moving_pts:
        return
    kd = KDTree(len(static_pts))
    idx_to_static = []
    for i, (orig_idx, co) in enumerate(static_pts):
        kd.insert((co.x, co.y, 0.0), i)
        idx_to_static.append((orig_idx, co))
    kd.balance()

    used_static = set()
    for m_idx, m_co in moving_pts:
        # Query nearest static in XY
        co_found, si, dist = kd.find((m_co.x, m_co.y, 0.0))
        # dist is Euclidean in 3D (z=0), so it's XY distance
        if dist <= tol_xy and si is not None:
            s_orig_idx, s_co = idx_to_static[si]
            if si in used_static:
                # allow reuse only if necessary; skip for now to avoid duplicates
                continue
            used_static.add(si)
            dz = s_co.z - m_co.z
            if abs(dz) > eps_z:
                pairs.append(dz)

def quadtree_match(bounds, static_pts, moving_pts, depth, tol_xy, pairs):
    """Recursive divide-and-conquer: subdivide until small or depth reached."""
    if not static_pts or not moving_pts:
        return
    # leaf condition
    if depth >= max_depth or (len(static_pts) <= min_points and len(moving_pts) <= min_points):
        process_leaf(static_pts, moving_pts, tol_xy, pairs)
        return

    # subdivide and distribute points
    children = subdivide_bounds(bounds)
    for child in children:
        s_child = [(i, co) for (i, co) in static_pts if in_cell(co, child)]
        m_child = [(i, co) for (i, co) in moving_pts if in_cell(co, child)]
        if s_child and m_child:
            quadtree_match(child, s_child, m_child, depth + 1, tol_xy, pairs)

def median(values):
    if not values:
        return None
    vals = sorted(values)
    n = len(vals)
    mid = n // 2
    if n % 2 == 1:
        return vals[mid]
    else:
        return 0.5 * (vals[mid-1] + vals[mid])

def align_by_xy_vertices(static_obj, moving_obj, tol_xy=1e-4):
    static_verts = world_verts(static_obj)
    moving_verts = world_verts(moving_obj)
    if not static_verts or not moving_verts:
        print("One of the objects has no vertices (or failed to evaluate).")
        return False

    # overall bounds containing both sets
    sminx, sminy, smaxx, smaxy = bbox_xy(static_verts)
    mminx, mminy, mmaxx, mmaxy = bbox_xy(moving_verts)
    minx = min(sminx, mminx) - tol_xy
    miny = min(sminy, mminy) - tol_xy
    maxx = max(smaxx, mmaxx) + tol_xy
    maxy = max(smaxy, mmaxy) + tol_xy
    bounds = (minx, miny, maxx, maxy)

    # convert lists to (idx, Vector) lists
    s_list = [(i, co) for (i, co) in static_verts]
    m_list = [(i, co) for (i, co) in moving_verts]

    pairs = []
    quadtree_match(bounds, s_list, m_list, 0, tol_xy, pairs)

    if not pairs:
        print("No overlapping XY vertex pairs found (increase tol_xy?).")
        return False

    z_med = median(pairs)
    if z_med is None:
        print("Found pairs but couldn't compute median.")
        return False

    # Safety: require a minimum number of pairs to be confident
    if len(pairs) < min_pairs_required:
        print(f"Only {len(pairs)} matching pairs found (need >= {min_pairs_required}).")
        print("Median Z difference computed but action not taken. You can lower min_pairs_required or check tolerance.")
        # still offer to apply — here we won't apply automatically
        return False

    # Apply translation in world space to moving_obj
    delta = Vector((0.0, 0.0, z_med))
    new_mat = moving_obj.matrix_world.copy()
    new_mat.translation = new_mat.translation + delta
    moving_obj.matrix_world = new_mat

    print(f"Matched pairs: {len(pairs)}. Applied Z translation of {z_med:.6f} to '{moving_obj.name}'.")
    return True

# ----------------- Auto-detect selection -----------------
def align_z_selected():
    sel = [o for o in bpy.context.selected_objects if o.type == 'MESH']
    if len(sel) < 2:
        print("Select two mesh objects (static and moving). Make the MOVING mesh the active object (last selected).")
    else:
        moving = bpy.context.view_layer.objects.active
        if moving is None or moving.type != 'MESH' or moving not in sel:
            print("Make the moving mesh the active object (last selected).")
        else:
            # choose the other selected mesh as static (if more than 2 are selected, we take the first other)
            static = next(o for o in sel if o != moving)
            success = align_by_xy_vertices(static, moving, tol_xy=tol_xy)
            if not success:
                print("Alignment not applied. Inspect printed messages and tweak tol_xy/min_pairs_required.")

if __name__ == "__main__":
    align_z_selected()