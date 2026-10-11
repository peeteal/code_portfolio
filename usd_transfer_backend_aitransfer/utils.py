import os
import OpenImageIO as oiio
import re
import shutil
from pathlib import Path
from pxr import Sdf

# --------------------------
# EXR to HDR
# --------------------------
def exr_to_hdr_inplace(file_path: str):
    if not file_path.lower().endswith(".exr"):
        raise ValueError("File must be a .exr")
    img_input = oiio.ImageInput.open(file_path)
    if not img_input:
        raise FileNotFoundError(f"Cannot open file: {file_path}")
    spec = img_input.spec()
    pixels = img_input.read_image(format=oiio.FLOAT)
    img_input.close()
    temp_path = file_path + ".tmp.hdr"
    img_output = oiio.ImageOutput.create(temp_path)
    if not img_output:
        raise RuntimeError(f"Cannot create temporary file: {temp_path}")
    spec.format = oiio.FLOAT
    img_output.open(temp_path, spec)
    img_output.write_image(pixels)
    img_output.close()
    os.replace(temp_path, os.path.splitext(file_path)[0] + ".hdr")
    print(f"Converted {file_path} -> {os.path.splitext(file_path)[0]}.hdr")
    os.remove(file_path)
    return f"{os.path.splitext(file_path)[0]}.hdr"


# --------------------------
# Path helpers (refactor of repeated code)
# --------------------------
def is_usd_like(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext.lower().lstrip('.') in ("usd", "usda", "usdc")

def is_path_absolute(asset_path: str):
        is_absolute = False
        if os.path.isabs(asset_path):
            is_absolute = True
        check = asset_path[1:3] if len(asset_path) > 2 else ''
        if check == ':/' or check == ':\\':
            is_absolute = True
        return is_absolute

def resolve_asset_absolute_path(source_root: str, asset_str: str, src_layer: Sdf.Layer):
    """
    Robustly resolve an authored asset path string to an absolute path using
    Sdf.ComputeAssetPathRelativeToLayer when possible, then fall back to
    directory-based heuristics and source_root.
    """
    if not asset_str:
        return None

    # 1) Ask pxr to resolve while honoring the layer
    try:
        resolved = None
        try:
            resolved = Sdf.ComputeAssetPathRelativeToLayer(src_layer, asset_str)
        except Exception:
            resolved = None

        if resolved:
            # If pxr returns an absolute path that exists, accept it immediately
            if os.path.isabs(resolved) and os.path.exists(resolved):
                return os.path.abspath(resolved)

            # If resolved is relative, it's usually relative to the layer's directory.
            layer_dir = os.path.dirname(src_layer.realPath)

            if layer_dir:
                cand = os.path.normpath(os.path.join(layer_dir, resolved))
                if os.path.exists(cand):
                    return os.path.abspath(cand)

            # As a last attempt with what pxr returned, try abspath() (cwd-based)
            cand2 = os.path.abspath(resolved)
            if os.path.exists(cand2):
                return cand2
    except Exception:
        # fall through to other heuristics below
        pass

    # 2) Fallback: treat asset_str as relative to the layer file's directory
    try:
        layer_dir = os.path.dirname(src_layer.realPath)
        candidate = os.path.normpath(os.path.join(layer_dir, asset_str))
        if os.path.exists(candidate):
            return candidate
    except Exception:
        pass

    # 3) Fallbacks using source_root
    if source_root:
        cand2 = os.path.normpath(os.path.join(source_root, asset_str))
        if os.path.exists(cand2):
            return cand2
        cand3 = os.path.normpath(os.path.join(source_root, "./" + asset_str))
        if os.path.exists(cand3):
            return cand3

    # final try: as given
    if os.path.exists(asset_str):
        return os.path.abspath(asset_str)

    # not found
    # debug helpful message:
    print(f"DEBUG: Could not resolve asset path '{asset_str}' relative to layer '{getattr(src_layer, 'realPath', None)}'")
    return None


def resolve_reference_items(prim_spec: Sdf.PrimSpec):
    refs = []
    try:
        rlist = getattr(prim_spec, "referenceList", None)
        if rlist is None:
            maybe_refs = getattr(prim_spec, "references", None)
            if maybe_refs:
                r: Sdf.Reference
                for r in maybe_refs:
                    refs.append((r.assetPath, r.primPath))
                return refs
        for attr in ("prependedItems", "appendedItems", "explicitItems", "references"):
            items = getattr(rlist, attr, None)
            if items:
                for r in items:
                    asset = getattr(r, "assetPath", None) or getattr(r, "assetPath", "")
                    primp = getattr(r, "primPath", None) or getattr(r, "primPath", "")
                    refs.append((str(asset), str(primp)))
    except Exception:
        pass
    return refs



def change_path_extensions(paths, file_format):
    """
    Change the file extension of each path in `paths` to `.<file_format>`.
    Preserves leading './' and absolute paths; normalizes separators to '/'.
    """
    updated_paths = []
    if not file_format:
        return list(paths)
    for orig in paths:
        if not orig:
            updated_paths.append(orig)
            continue

        # Preserve leading './' explicitly since Path(...).with_suffix removes it.
        has_dot_slash = orig.startswith("./") or orig.startswith(".\\")
        try:
            p = Path(orig)
            newp = p.with_suffix(f".{file_format}")
            new_str = str(newp)
            # If original had leading './' but new_str lost it, re-add.
            if has_dot_slash and not new_str.startswith("./") and not new_str.startswith(".\\"):
                new_str = "./" + new_str
            # Normalize separators to forward slash for USD asset paths
            new_str = new_str.replace("\\", "/")
            updated_paths.append(new_str)
        except Exception:
            # Fallback conservative replacement using splitext
            base, _ = os.path.splitext(orig)
            new = base + "." + file_format
            new = new.replace("\\", "/")
            if has_dot_slash and not new.startswith("./"):
                new = "./" + new.lstrip("./")
            updated_paths.append(new)
    return updated_paths






import tempfile

def _atomic_write_text(path, text):
    """Write text atomically into path (same dir)."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_usd_", dir=d, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise

def replace_asset_token_exact(layer_path, old_relpath, new_relpath):
    """
    Replace occurrences of @old_relpath@ with @new_relpath@ inside an ASCII USD layer.
    Returns True if a change was made.
    - layer_path: absolute path to the USD layer file (.usda/.usd)
    - old_relpath/new_relpath: the exact path string inside the @..@ token (preserve ./ or ../ if present)
    """
    if not layer_path or not os.path.exists(layer_path):
        return False

    if Path(layer_path).suffix.lower() not in (".usda", ".usd"):
        return False

    # normalize separators to forward slash for matching
    old_tok = "@" + old_relpath.replace("\\", "/") + "@"
    new_tok = "@" + new_relpath.replace("\\", "/") + "@"

    with open(layer_path, "r", encoding="utf-8") as fh:
        text = fh.read()

    if old_tok not in text:
        return False

    new_text = text.replace(old_tok, new_tok)
    if new_text == text:
        return False

    _atomic_write_text(layer_path, new_text)
    return True


def rewrite_asset_token_extensions_in_file(layer_path, from_ext, to_ext):
    """
    Replace any @...from_ext@ token with @...to_ext@ inside an ASCII USD layer.
    - from_ext and to_ext should include the leading dot: '.exr' -> '.hdr'
    Returns True if anything changed.
    """
    if not layer_path or not os.path.exists(layer_path):
        return False

    if Path(layer_path).suffix.lower() not in (".usda", ".usd"):
        return False

    # case-insensitive match for tokens that end with the given extension
    pat = re.compile(r'@([^@]+{})(?=@)'.format(re.escape(from_ext)), flags=re.IGNORECASE)

    with open(layer_path, "r", encoding="utf-8") as fh:
        text = fh.read()

    if not pat.search(text):
        return False

    def repl(m):
        orig = m.group(1)
        # try to preserve directories / leading ./ and normalize separators
        new = str(Path(orig).with_suffix(to_ext)).replace("\\", "/")
        if orig.startswith("./") and not new.startswith("./"):
            new = "./" + new.lstrip("./")
        return new

    new_text = pat.sub(lambda m: repl(m), text)
    if new_text == text:
        return False

    _atomic_write_text(layer_path, new_text)
    return True

    
def delete_empty_folders_upwards(start_path):
    """Delete the folder if it contains no files (only empty subfolders),
    then go upward recursively deleting empty parents too."""
    path = os.path.abspath(start_path)

    while True:
        if not os.path.isdir(path):
            break

        # Count all files (recursively) inside this folder
        has_files = any(
            os.path.isfile(os.path.join(root, f))
            for root, _, files in os.walk(path)
            for f in files
        )

        if has_files:
            # Stop if this folder (or subfolders) has files
            break

        # No files found → delete the folder entirely
        shutil.rmtree(path)
        print(f"Deleted empty folder: {path}")

        # Move one level up
        parent = os.path.dirname(path)
        if parent == path or not os.path.isdir(parent):
            break
        path = parent