#!/usr/bin/env python3
"""
align_by_crops_fixed2.py

Robust alignment that:
 - crops to non-empty content in each texture,
 - uses FFT cross-correlation on the crops to get an initial integer shift,
 - optionally runs a small full-resolution local integer search around that shift (fast),
 - optionally runs subpixel phase-correlation refinement on the final overlap,
 - writes <out_prefix>_diff.png, <out_prefix>_overlap.png, <out_prefix>_absdiff.png

Usage:
    python align_by_crops_fixed2.py A.png B.png --out-prefix result [--local-radius 32] [--refine]
"""
import cv2
import numpy as np
import argparse
import json
from math import ceil

def read_image(path):
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(path)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img

def non_empty_bbox(img, thresh=8):
    mask = (img.max(axis=2) > thresh).astype(np.uint8)
    ys, xs = np.where(mask)
    if len(xs) == 0:
        h,w = img.shape[:2]
        return 0,0,w,h
    x0 = int(xs.min()); x1 = int(xs.max()) + 1
    y0 = int(ys.min()); y1 = int(ys.max()) + 1
    return x0, y0, x1, y1

def crop_to_bbox(img, bbox):
    x0,y0,x1,y1 = bbox
    return img[y0:y1, x0:x1].copy()

def fft_cross_correlation_shift(A, B, use_window=True, phase_only=True, eps=1e-9):
    """
    Robust FFT cross-correlation shift estimator.
    - A,B: 2D float grayscale arrays in [0,1] (crop images).
    - Returns (sx, sy, corr_map) meaning: MOVE B by (sx,sy) (positive right/down) to align to A (crop coords).
    Improvements over raw correlation:
      * mean-subtract A/B
      * optional Hanning window per-crop
      * optional phase-only normalization (cross-power spectrum)
    """
    Ha, Wa = A.shape
    Hb, Wb = B.shape
    # padding to avoid circular wrap: sum of sizes (safe)
    H = Ha + Hb
    W = Wa + Wb

    # copy & preprocess
    a = A.astype(np.float32).copy()
    b = B.astype(np.float32).copy()

    # mean-subtract (removes DC bias)
    a -= a.mean()
    b -= b.mean()

    # apply separable hanning window per-crop to reduce edge leakage
    if use_window:
        wy_a = np.hanning(Ha) if Ha > 1 else np.ones(Ha)
        wx_a = np.hanning(Wa) if Wa > 1 else np.ones(Wa)
        win_a = np.outer(wy_a, wx_a)
        a *= win_a

        wy_b = np.hanning(Hb) if Hb > 1 else np.ones(Hb)
        wx_b = np.hanning(Wb) if Wb > 1 else np.ones(Wb)
        win_b = np.outer(wy_b, wx_b)
        b *= win_b

    FA = np.fft.fft2(a, s=(H, W))
    FB = np.fft.fft2(b, s=(H, W))

    R = FA * np.conj(FB)   # cross-spectrum
    if phase_only:
        denom = np.abs(R)
        denom[denom < eps] = eps
        R = R / denom       # cross-power spectrum (phase-only)

    r = np.fft.ifft2(R)
    r = np.abs(r)

    # center zero-lag for easier indexing
    rshift = np.fft.fftshift(r)

    py, px = np.unravel_index(np.argmax(rshift), rshift.shape)
    cy = rshift.shape[0] // 2
    cx = rshift.shape[1] // 2

    sx = int(round(px - cx))
    sy = int(round(py - cy))

    return sx, sy, rshift


def compute_full_overlap_patch_coords_for_move(W, H, dx_move, dy_move):
    """
    Given full-image width W, height H, and a movement dx_move/dy_move that we will
    apply to B (move right/down), compute the overlap rectangle in A coords and corresponding B coords.
    Returns (ox0,oy0,ox1,oy1, bx0,b y0,bx1,by1) where ranges are [ox0:ox1) etc.
    """
    ox0 = max(0, dx_move)
    oy0 = max(0, dy_move)
    ox1 = min(W, dx_move + W)
    oy1 = min(H, dy_move + H)
    bx0 = max(0, -dx_move)
    by0 = max(0, -dy_move)
    bx1 = bx0 + (ox1 - ox0)
    by1 = by0 + (oy1 - oy0)
    return ox0, oy0, ox1, oy1, bx0, by0, bx1, by1

def local_integer_refine(fullA_gray, fullB_gray, dx_init, dy_init, radius=32):
    """
    Fast local integer search around (dx_init,dy_init). For each candidate integer shift
    (dx_init + dx, dy_init + dy) with dx,dy in [-radius..radius], compute overlap L1 score and pick min.
    Returns best_dx_move, best_dy_move, best_score.
    """
    H, W = fullA_gray.shape
    best_score = float('inf')
    best = (dx_init, dy_init)
    # Precompute valid bounding for search to reduce checks
    for dy in range(-radius, radius+1):
        cand_dy = dy_init + dy
        # compute y overlap quickly
        oy0 = max(0, cand_dy)
        oy1 = min(H, cand_dy + H)
        if oy1 <= oy0:
            continue
        for dx in range(-radius, radius+1):
            cand_dx = dx_init + dx
            ox0 = max(0, cand_dx)
            ox1 = min(W, cand_dx + W)
            if ox1 <= ox0:
                continue
            bx0 = max(0, -cand_dx)
            by0 = max(0, -cand_dy)
            bx1 = bx0 + (ox1 - ox0)
            by1 = by0 + (oy1 - oy0)
            a_patch = fullA_gray[oy0:oy1, ox0:ox1]
            b_patch = fullB_gray[by0:by1, bx0:bx1]
            # compute L1
            score = float(np.sum(np.abs(a_patch - b_patch)))
            if score < best_score:
                best_score = score
                best = (cand_dx, cand_dy)
    return best[0], best[1], best_score

def phase_correlate_subpixel_on_full_overlap(fullA_gray, fullB_gray, dx_int, dy_int, max_side=1024):
    """
    Given integer full-image movement dx_int,dy_int (move B by this to align to A),
    extract the full-resolution overlap patches, optionally downscale (float), then run phaseCorrelate to get
    subpixel residual (dx_res,dy_res). Returns dx_res, dy_res, response.
    """
    H, W = fullA_gray.shape
    ox0, oy0, ox1, oy1, bx0, by0, bx1, by1 = compute_full_overlap_patch_coords_for_move(W, H, dx_int, dy_int)
    if ox1 <= ox0 or oy1 <= oy0:
        return 0.0, 0.0, 0.0
    patchA = fullA_gray[oy0:oy1, ox0:ox1]
    patchB = fullB_gray[by0:by1, bx0:bx1]
    if patchA.size == 0 or patchA.shape != patchB.shape:
        return 0.0, 0.0, 0.0
    scale = 1.0
    if max(patchA.shape) > max_side:
        scale = max_side / float(max(patchA.shape))
        new_w = max(1, int(round(patchA.shape[1] * scale)))
        new_h = max(1, int(round(patchA.shape[0] * scale)))
        pa = cv2.resize(patchA, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        pb = cv2.resize(patchB, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    else:
        pa = patchA
        pb = patchB
    win = cv2.createHanningWindow((pa.shape[1], pa.shape[0]), cv2.CV_32F)
    (dx_small, dy_small), resp = cv2.phaseCorrelate(pa.astype(np.float32), pb.astype(np.float32), win)
    dx_res = float(dx_small) / (scale if scale != 0 else 1.0)
    dy_res = float(dy_small) / (scale if scale != 0 else 1.0)
    return dx_res, dy_res, resp

def make_canvas_and_place(img_a, img_b, shift_ab, base_canvas_size=4096):
    h, w = img_a.shape[:2]
    sx, sy = int(round(shift_ab[0])), int(round(shift_ab[1]))
    ax0 = (base_canvas_size - w)//2
    ay0 = (base_canvas_size - h)//2
    bx0 = ax0 + sx
    by0 = ay0 + sy
    min_x = min(ax0, bx0)
    min_y = min(ay0, by0)
    max_x = max(ax0 + w, bx0 + w)
    max_y = max(ay0 + h, by0 + h)
    if min_x < 0 or min_y < 0 or max_x > base_canvas_size or max_y > base_canvas_size:
        pad_left = -min_x if min_x < 0 else 0
        pad_top = -min_y if min_y < 0 else 0
        new_w = max_x + pad_left
        new_h = max_y + pad_top
        canvas_w = max(new_w, base_canvas_size)
        canvas_h = max(new_h, base_canvas_size)
        ax0 += pad_left
        ay0 += pad_top
        bx0 += pad_left
        by0 += pad_top
    else:
        canvas_w = base_canvas_size
        canvas_h = base_canvas_size
    canvasA = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    canvasB = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    canvasA[ay0:ay0+h, ax0:ax0+w] = img_a
    canvasB[by0:by0+h, bx0:bx0+w] = img_b
    return canvasA, canvasB, (ax0,ay0), (bx0,by0)

def compute_diff_and_overlap(canvas_a, canvas_b):
    diff = cv2.absdiff(canvas_a, canvas_b)
    diff_gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    if diff_gray.max() > 0:
        diff_vis = (255.0 * (diff_gray.astype(np.float32) / float(diff_gray.max()))).astype(np.uint8)
    else:
        diff_vis = diff_gray.copy()
    A = canvas_a.astype(np.float32) / 255.0
    B = canvas_b.astype(np.float32) / 255.0
    R = A[...,2]
    G = B[...,1]
    blue = ((A.mean(axis=2) + B.mean(axis=2)) * 0.5)
    comp = np.stack([blue, G, R], axis=2)
    comp = np.clip((comp*255.0).astype(np.uint8), 0, 255)
    return diff_vis, comp, diff

class Options():
    imgA = None
    imgB = None
    threshold = None
    local_radius = None
    out_prefix = None
    refine = None
    canvas = None
    output_json = None
    output_imgs = None

    def __init__(self, args):
        self.imgA = args.imgA
        self.imgB = args.imgB
        self.threshold = args.thresh
        self.local_radius = args.local_radius
        self.out_prefix = args.out_prefix
        self.refine = args.refine
        self.canvas = args.canvas
        self.output_json = args.out_json
        self.output_imgs = args.out_imgs

def process(opts : Options):
    A = read_image(opts.imgA)
    B = read_image(opts.imgB)
    if A.shape[:2] != B.shape[:2]:
        print("Warning: resizing B to A")
        B = cv2.resize(B, (A.shape[1], A.shape[0]), interpolation=cv2.INTER_LINEAR)

    H, W = A.shape[:2]
    bboxA = non_empty_bbox(A, thresh=opts.threshold)
    bboxB = non_empty_bbox(B, thresh=opts.threshold)
    ax0, ay0, ax1, ay1 = bboxA
    bx0, by0, bx1, by1 = bboxB
    cropA = crop_to_bbox(A, bboxA)
    cropB = crop_to_bbox(B, bboxB)
    print(f"A content bbox: {bboxA} size {(ax1-ax0, ay1-ay0)}")
    print(f"B content bbox: {bboxB} size {(bx1-bx0, by1-by0)}")

    gA = cv2.cvtColor(cropA, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    gB = cv2.cvtColor(cropB, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0

    sx_crop, sy_crop, corr_map = fft_cross_correlation_shift(gA, gB)
    print(f"Crop-relative (move B_crop by) sx_crop,sy_crop = ({sx_crop},{sy_crop})")

    # map crop-space move to full-image move
    dx_move = (ax0 - bx0) + sx_crop
    dy_move = (ay0 - by0) + sy_crop
    print(f"Initial FULL-RES MOVE for B (before local/subpixel refine): dx_move={dx_move}, dy_move={dy_move}")

    # local integer refine (full-res) (optional)
    if opts.local_radius and opts.local_radius > 0:
        fullA_gray = cv2.cvtColor(A, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        fullB_gray = cv2.cvtColor(B, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        best_dx, best_dy, best_score = local_integer_refine(fullA_gray, fullB_gray, int(round(dx_move)), int(round(dy_move)), radius=opts.local_radius)
        print(f"Local integer refine -> best dx={best_dx}, dy={best_dy}, score={best_score:.3f}")
        dx_move = best_dx
        dy_move = best_dy

    # subpixel refine on full-res overlap (optional)
    if opts.refine:
        fullA_gray = cv2.cvtColor(A, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        fullB_gray = cv2.cvtColor(B, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        dx_res, dy_res, resp = phase_correlate_subpixel_on_full_overlap(fullA_gray, fullB_gray, int(round(dx_move)), int(round(dy_move)))
        print(f"Subpixel residual: dx_res={dx_res:.4f}, dy_res={dy_res:.4f}, resp={resp:.6f}")
        dx_move = dx_move + dx_res
        dy_move = dy_move + dy_res

    # IMPORTANT: placement_arg should be exactly (dx_move, dy_move) — not negated.
    placement_arg = (dx_move, dy_move)
    print(f"Placement argument (pass to make_canvas_and_place): {placement_arg}")
    print(f"Final instruction: MOVE image B by dx={dx_move:.4f}, dy={dy_move:.4f}")

    # place using placement_arg so visualization matches the printed dx_move
    canvasA, canvasB, posA, posB = make_canvas_and_place(A, B, placement_arg, base_canvas_size=opts.canvas)
    diff_vis, overlap_vis, diff_raw = compute_diff_and_overlap(canvasA, canvasB)
    
    if opts.output_imgs:
        out_diff = f"{opts.out_prefix}_diff.png"
        out_overlap = f"{opts.out_prefix}_overlap.png"
        out_abs = f"{opts.out_prefix}_absdiff.png"
        cv2.imwrite(out_diff, diff_vis)
        cv2.imwrite(out_overlap, overlap_vis)
        cv2.imwrite(out_abs, diff_raw)
        print(f"Saved: {out_diff}, {out_overlap}, {out_abs}")
        print(f"A top-left on canvas: {posA}, B top-left on canvas: {posB}")

    # overlap metrics
    ax, ay = posA
    bx, by = posB
    overlap_x0 = max(ax, bx)
    overlap_y0 = max(ay, by)
    overlap_x1 = min(ax + W, bx + W)
    overlap_y1 = min(ay + H, by + H)
    if overlap_x1 > overlap_x0 and overlap_y1 > overlap_y0:
        A_ov = canvasA[overlap_y0:overlap_y1, overlap_x0:overlap_x1].astype(np.float32)/255.0
        B_ov = canvasB[overlap_y0:overlap_y1, overlap_x0:overlap_x1].astype(np.float32)/255.0
        l1 = float(np.sum(np.abs(A_ov - B_ov)))
        mse = float(np.mean((A_ov - B_ov)**2))
        print(f"Overlap area: {overlap_x1-overlap_x0} x {overlap_y1-overlap_y0}  L1={l1:.3f}  MSE={mse:.8f}")
    else:
        print("No overlap between placed images (unexpected).")

    if opts.output_json is not None:
        msg = {'dx' : dx_move, 'dy' : -dy_move}
        
        # 3. Open the file in write mode ('w') and use json.dump()
        try:
            with open(opts.output_json, 'w') as json_file:
                json.dump(msg, json_file, indent=4)
            print(f"Data successfully saved to {opts.output_json}")
        except IOError as e:
            print(f"An error occurred while writing the file: {e}")
    return dx_move, dy_move

def main():
    ap = argparse.ArgumentParser(description="Robust aligner (crop+FFT+local+subpixel).")
    ap.add_argument("imgA")
    ap.add_argument("imgB")
    ap.add_argument("--out-prefix", default="result")
    ap.add_argument("--thresh", type=int, default=8, help="non-empty threshold")
    ap.add_argument("--local-radius", type=int, default=16, help="local integer refine radius (0 to disable)")
    ap.add_argument("--refine", default = True, action="store_true", help="do subpixel refinement after integer steps")
    ap.add_argument("--canvas", type=int, default=1024)
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--out-imgs", action="store_true", default=False)
    args = ap.parse_args()

    opts = Options(args)
    process(opts)
    
if __name__ == "__main__":
    main()