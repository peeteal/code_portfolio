import os
import shutil
from pathlib import Path
from pxr import Usd, Sdf
from metadata import copy_root_metadata, copy_root_metadata_animated
import utils
from typing import Optional, List, Set, Dict


from logger import log_debug, log_error, log_stdout, log_warning

COMMON_FILTERS = [
    "Camera",
    # "Xform", 
    # "Scope", 
    # "Material",
    # "variantSet",
    # "rel"
]

LIGHT_FILTERS = [
    "DomeLight", "RectLight", "SphereLight", "DiskLight",
    "CylinderLight", "Light", "LightFilter", "DistantLight"
]

GEOM_FILTERS = [
    "Mesh",
    "BasisCurves",
    "Points",
    "Cube", "Sphere", "Cone", "Cylinder", "Capsule",
    "NurbsCurves", "NurbsPatch", "Curves"
]
DEFAULT_FILTER_TYPENAMES = []
# DEFAULT_FILTER_TYPENAMES.extend(COMMON_FILTERS)
DEFAULT_FILTER_TYPENAMES.extend(LIGHT_FILTERS)
# DEFAULT_FILTER_TYPENAMES.extend(GEOM_FILTERS)


class Exporter:
    FILTER_TYPENAMES = DEFAULT_FILTER_TYPENAMES
    DEFINED_OBJECTS = set()

    def __init__(self):
        self.active_filter_typenames = list(self.FILTER_TYPENAMES)
        self.src_usd_dir: str = None
        self.dst_usd_dir: str = None
        self.src_stage = None
        self.src_sdf_layer = None
        self.dst_stage: Optional[Usd.Stage] = None
        self.source_root = None
        self.format = None
        self.formatted = None
        self.exr_to_hdr = True
        self.include_geometries = False
        self.include_references = False
        self._copied_layers = {}  # map src_abs -> dst_abs

    # def _resolve_asset_absolute_path(self, asset_str: str, src_layer: Sdf.Layer):
    #     """
    #     Robustly resolve an authored asset path string to an absolute path using
    #     Sdf.ComputeAssetPathRelativeToLayer when possible, then fall back to
    #     directory-based heuristics and source_root.
    #     """
    #     if not asset_str:
    #         return None
    #     # Prefer pxr's resolution relative to the provided layer
    #     try:
    #         resolved = Sdf.ComputeAssetPathRelativeToLayer(src_layer, asset_str)
    #         if resolved:
    #             # ComputeAssetPathRelativeToLayer may return a relative path; make absolute
    #             abs_candidate = os.path.abspath(resolved)
    #             log_debug(f"Resolved {resolved} => {abs_candidate}")
    #             if os.path.exists(abs_candidate):
    #                 return abs_candidate
    #             # also accept resolved as-is if absolute
    #             if os.path.isabs(resolved) and os.path.exists(resolved):
    #                 return os.path.abspath(resolved)
    #     except Exception as e:
    #         log_debug(f"Suppressed exception in traversal: {e}")

    #     # Fallback: treat asset_str as relative to the layer file's directory
    #     try:
    #         layer_dir = os.path.dirname(src_layer.realPath)
    #         candidate = os.path.normpath(os.path.join(layer_dir, asset_str))
    #         if os.path.exists(candidate):
    #             return candidate
    #     except Exception:
    #         log_debug(f"Suppressed exception in traversal: {e}")

    #     # Fallbacks: try source_root variants
    #     if self.source_root:
    #         cand2 = os.path.normpath(os.path.join(self.source_root, asset_str))
    #         if os.path.exists(cand2):
    #             return cand2
    #         cand3 = os.path.normpath(os.path.join(self.source_root, "./" + asset_str))
    #         if os.path.exists(cand3):
    #             return cand3

    #     # final try: as given
    #     if os.path.exists(asset_str):
    #         return os.path.abspath(asset_str)
    #     return None

    def _collect_list_items(self, list_obj, attr_names):
        """
        Normalize the various possible list styles (prependedItems/appendedItems/explicitItems/references)
        into a simple python list of entries.
        """
        items = []
        if not list_obj:
            return items
        for attr in attr_names:
            maybe = getattr(list_obj, attr, None)
            if maybe:
                for entry in maybe:
                    items.append(entry)
        return items

    def _copy_non_usd_asset(self, src_asset_abs: str, dst_usd_dir: str):
        """Copy a non-USD asset into dst_usd_dir preserving layout and convert exr→hdr if requested."""
        dst_asset_abs = os.path.normpath(
            os.path.join(dst_usd_dir, os.path.relpath(src_asset_abs, self.src_usd_dir))
        )
        os.makedirs(os.path.dirname(dst_asset_abs), exist_ok=True)

        ext = os.path.splitext(dst_asset_abs)[1].lower()

        # Case 1: EXR → HDR conversion
        if self.exr_to_hdr and ext == ".exr":
            try:
                # let utils.exr_to_hdr produce an .hdr file alongside
                hdr_dst_abs = utils.exr_to_hdr_inplace(dst_asset_abs)
                if hdr_dst_abs and os.path.exists(hdr_dst_abs):
                    log_debug(f"Converted EXR to HDR: {src_asset_abs} -> {hdr_dst_abs}")
                    rel_dst_asset_path = os.path.relpath(hdr_dst_abs, dst_usd_dir).replace(os.sep, "/")
                    return Sdf.AssetPath(rel_dst_asset_path)
                else:
                    log_warning(f"EXR->HDR conversion returned no file for {src_asset_abs}, copying .exr instead")
            except Exception as e:
                log_warning(f"EXR->HDR conversion failed for {src_asset_abs}: {e}")
                # fall through to normal copy of .exr

        # Case 2: everything else (including .exr fallback)
        try:
            shutil.copy2(src_asset_abs, dst_asset_abs)
            log_debug(f"Copying asset {src_asset_abs} -> {dst_asset_abs}")
        except Exception as e:
            log_warning(f": copying asset {src_asset_abs} -> {dst_asset_abs}: {e}")
            return None

        rel_dst_asset_path = os.path.relpath(dst_asset_abs, dst_usd_dir).replace(os.sep, "/")
        utils.delete_empty_folders_upwards(os.path.dirname(dst_asset_abs))
        return Sdf.AssetPath(rel_dst_asset_path)

    # def _copy_non_usd_asset(self, src_asset_abs: str, dst_usd_dir: str):
    #     """Copy a non-USD asset into dst_usd_dir preserving layout and convert exr if requested."""
    #     dst_asset_abs = os.path.normpath(os.path.join(dst_usd_dir, os.path.relpath(src_asset_abs, self.src_usd_dir)))
    #     os.makedirs(os.path.dirname(dst_asset_abs), exist_ok=True)
    #     try:
    #         shutil.copy2(src_asset_abs, dst_asset_abs)
    #         log_debug(f"Copying asset {src_asset_abs} -> {dst_asset_abs}")
    #     except Exception as e:
    #         log_warning(f": copying asset {src_asset_abs} -> {dst_asset_abs}: {e}")
    #         return None
    #     if self.exr_to_hdr:
    #         try:
    #             if dst_asset_abs.lower().endswith('.exr'):
    #                 utils.exr_to_hdr_inplace(dst_asset_abs)
    #         except Exception as e:
    #             log_warning(f" while converting EXR: {e}")
    #     rel_dst_asset_path = os.path.relpath(dst_asset_abs, dst_usd_dir).replace(os.sep, "/")
    #     return Sdf.AssetPath(rel_dst_asset_path)

    def _copy_or_recurse_asset(self, asset_abs: str, visited: set):
        """
        If asset_abs is USD-like, recurse via copy_layer_and_deps; else copy file.
        Returns absolute path to copied asset (for USD-like) or Sdf.AssetPath (for non-usd).
        """
        if not asset_abs or not os.path.exists(asset_abs):
            log_debug(f"{asset_abs} not found")
            return None

        if utils.is_usd_like(asset_abs):
            # return copied absolute path to the new USD layer
            log_debug(f"Traversed to USD asset {asset_abs}")
            return self.copy_layer_and_deps(asset_abs, visited)
        else:
            # copy and return Sdf.AssetPath
            log_debug(f"Traversed to asset {asset_abs}")
            return self._copy_non_usd_asset(asset_abs, self.dst_usd_dir)

    def _add_reference_to_dst_prim(self, dst_prim_spec: Sdf.PrimSpec, rel_path, prim_path):
        """Add a reference to the dst prim/spec with backward-compatible fallbacks."""
        try:
            dst_prim_spec.referenceList.Add(Sdf.Reference(rel_path, prim_path))
            return True
        except Exception:
            try:
                # older builds
                dst_prim_spec.referenceList.Add(rel_path, prim_path)
                return True
            except Exception as e:
                log_warning(f"Writing reference {rel_path} {prim_path}: {e}")
        return False

    def _add_payload_to_dst_prim(self, dst_prim_spec: Sdf.PrimSpec, rel_path, prim_path):
        """Add a payload to the dst prim/spec with backward-compatible fallbacks."""
        try:
            dst_prim_spec.payloadList.Add(Sdf.Payload(rel_path, prim_path))
            return True
        except Exception:
            try:
                dst_prim_spec.payloadList.Add(rel_path, prim_path)
                return True
            except Exception as e:
                log_warning(f" writing payload {rel_path} {prim_path}: {e}")
        return False

    def _process_reference_or_payload_entries(self, src_layer: Sdf.Layer, prim_spec: Sdf.PrimSpec, dst_prim_spec: Sdf.PrimSpec, list_kind: str, visited: set):
        """
        Shared logic for processing referenceList or payloadList entries in a prim spec.
        list_kind: "reference" or "payload"
        Uses src_layer (Sdf.Layer) to compute relative asset paths via Sdf.ComputeAssetPathRelativeToLayer.
        """
        if list_kind == "reference":
            list_obj = getattr(prim_spec, "referenceList", None) or getattr(prim_spec, "references", None)
            attrs = ("prependedItems", "appendedItems", "explicitItems", "references")
        else:
            list_obj = getattr(prim_spec, "payloadList", None) or getattr(prim_spec, "payloads", None)
            attrs = ("prependedItems", "appendedItems", "explicitItems", "payloads")

        items: List[object] = self._collect_list_items(list_obj, attrs)
        if not items or not dst_prim_spec:
            log_debug("items not found or dst prim spec not found.")
            return

        # # Clear existing entries conservatively
        # try:
        #     if list_kind == "reference":
        #         dst_prim_spec.referenceList = Sdf.ReferenceList()
        #     else:
        #         dst_prim_spec.payloadList = Sdf.PayloadList()
        # except Exception:
        #     pass

        for entry in items:
            asset = getattr(entry, "assetPath", None) or getattr(entry, "assetPath", "")
            prim_p = getattr(entry, "primPath", None) or getattr(entry, "primPath", "") or ""
            if not asset:
                continue

            # Resolve absolute asset path robustly using src_layer
            # Use the proper helper that returns an absolute filename (or None)
            try:
                asset_abs = utils.resolve_asset_absolute_path(self.source_root, str(asset), src_layer)
            except Exception:
                asset_abs = None
            log_debug(str(asset_abs))

            new_asset_result = None
            if asset_abs and os.path.exists(asset_abs):
                try:
                    new_asset_result = self._copy_or_recurse_asset(asset_abs, visited)
                except Exception:
                    new_asset_result = None
            else:
                # last resort: try copy_asset_if_needed using src_layer as base
                try:
                    new_asset = self.copy_asset(Sdf.AssetPath(str(asset).strip("@")), self.src_usd_dir, self.dst_usd_dir, base_layer=src_layer)
                    if new_asset:
                        new_asset_result = new_asset
                except Exception:
                    new_asset_result = None

            if not new_asset_result:
                # couldn't copy — keep original authored path as a best-effort fallback
                continue

            # If new_asset_result is absolute path (USD-like copies) => compute relative from dst layer
            relpath = None
            if isinstance(new_asset_result, str):
                # USD-like path (absolute path to copied layer)
                new_asset_abs = new_asset_result
                try:
                    dst_layer_dir = os.path.dirname(dst_prim_spec.layer.realPath) if dst_prim_spec else self.dst_usd_dir
                except Exception:
                    dst_layer_dir = self.dst_usd_dir
                relpath = os.path.relpath(new_asset_abs, dst_layer_dir).replace(os.sep, "/")

                # If we're reformatting to a different file format, ensure the relpath uses the converted suffix
                try:
                    if self.formatted and self.format and utils.is_usd_like(relpath):
                        # use pathlib to change suffix safely and normalize separators
                        relpath = utils.change_path_extensions([relpath], self.format)[0]
                except Exception:
                    pass

            # if isinstance(new_asset_result, str):
            #     # USD-like path
            #     new_asset_abs = new_asset_result
            #     try:
            #         dst_layer_dir = os.path.dirname(dst_prim_spec.layer.realPath) if dst_prim_spec else self.dst_usd_dir
            #     except Exception:
            #         dst_layer_dir = self.dst_usd_dir
            #     relpath = os.path.relpath(new_asset_abs, dst_layer_dir).replace(os.sep, "/")
            # else:
            #     # copy_asset_if_needed returned a dict or other metadata -> best-effort: keep original authored string
            #     relpath = str(asset)

            # Only add the reference/payload to dst_prim_spec if it exists. We still wanted the asset copied
            # so that composition/instantiation later can find it.
            if dst_prim_spec:
                if list_kind == "reference":
                    self._add_reference_to_dst_prim(dst_prim_spec, relpath, prim_p)
                else:
                    self._add_payload_to_dst_prim(dst_prim_spec, relpath, prim_p)

    # --------------------------
    # Existing (but simplified) core: copy layer and deps (uses helpers)
    # --------------------------
    def copy_layer_and_deps(self, src_layer_abs: str, visited: set):
        """
        Copy a USD layer file (src_layer_abs) into the dst tree, recursively copy its
        sublayers, references and payloads, update the copied layer to point to the copied deps,
        optionally convert to self.format (self.formatted True), save and return the copied dst abs path.
        visited is a set of src abs paths to avoid cycles.
        """
        if not src_layer_abs:
            return None

        src_layer_abs = os.path.abspath(src_layer_abs)
        if src_layer_abs in self._copied_layers:
            return self._copied_layers[src_layer_abs]

        if visited is None:
            visited = set()
        if src_layer_abs in visited:
            return None
        visited.add(src_layer_abs)

        # compute destination path preserving relative layout
        try:
            rel = os.path.relpath(src_layer_abs, self.src_usd_dir)
        except Exception:
            rel = os.path.basename(src_layer_abs)
        dst_layer_abs = os.path.normpath(os.path.join(self.dst_usd_dir, rel))
        os.makedirs(os.path.dirname(dst_layer_abs), exist_ok=True)

        # copy file first so we can edit the copy safely
        try:
            shutil.copy2(src_layer_abs, dst_layer_abs)
        except Exception as e:
            log_warning(f": failed to copy layer {src_layer_abs} -> {dst_layer_abs}: {e}")
            return None

        # register immediately to avoid cycles
        self._copied_layers[src_layer_abs] = dst_layer_abs

        src_layer = Sdf.Layer.FindOrOpen(src_layer_abs)
        dst_layer = Sdf.Layer.FindOrOpen(dst_layer_abs)

        if not src_layer or not dst_layer:
            # If it's a .usdc that can't be read, just copy as-is
            if src_layer_abs.endswith(".usdc"):
                log_warning(f"⚠️ Could not open {src_layer_abs}, copying .usdc directly.")
                return dst_layer_abs
            return dst_layer_abs

        # 1) process sublayers: copy each referenced sublayer and rewrite subLayerPaths
        new_sublayer_paths = self._copy_sublayers(src_layer, src_layer_abs, dst_layer_abs, dst_layer, visited)

        # 2) process ALL prim specs (depth-first including variant children) for references/payloads
        self._process_prims_for_layer(src_layer, dst_layer, dst_layer_abs, visited)

        #    and process variant sets so authored payloads inside variant bodies (e.g. .usdc) are handled.
        try:
            # open destination stage (if possible) so copy_variant_sets_if_any can author variant bodies
            try:
                dst_stage = None
                # prefer opening the stage, but fall back gracefully
                try:
                    dst_stage = Usd.Stage.Open(dst_layer_abs)
                except Exception:
                    # if Stage.Open fails, try opening the layer via Stage with anonymous root
                    try:
                        dst_stage = Usd.Stage.Open(dst_layer.resolvedPath) if getattr(dst_layer, "resolvedPath", None) else None
                    except Exception:
                        dst_stage = None
            except Exception:
                dst_stage = None

            # Depth-first traverse every prim spec (this visits variant child primSpecs too)
            stack: List[Sdf.PrimSpec] = list(src_layer.rootPrims)
            while stack:
                prim_spec: Sdf.PrimSpec = stack.pop()
                # push children for traversal (nameChildren includes variant children)
                stack.extend(reversed(prim_spec.nameChildren))

                try:
                    dst_prim_spec = dst_layer.GetPrimAtPath(prim_spec.path)
                except Exception:
                    dst_prim_spec: Optional[Sdf.PrimSpec] = None

                # Process references authored on this prim spec
                try:
                    
                    try:
                        if dst_prim_spec is None:
                            try:
                                self.define_prim_if_missing(dst_stage, prim_spec, src_layer)
                            except Exception as e:
                                log_error(e)
                            try:
                                dst_prim_spec = dst_layer.GetPrimAtPath(prim_spec.path)
                            except Exception as e:
                                log_error(e)
                                dst_prim_spec = None
                    except Exception as e:
                        log_error(e)

                    self._process_reference_or_payload_entries(src_layer, prim_spec, dst_prim_spec, "reference", visited)
                except Exception:
                    pass

                # Process payloads authored on this prim spec (this is the important bit for your .usdc payloads)
                try:
                    
                    try:
                        if dst_prim_spec is None:
                            try:
                                self.define_prim_if_missing(dst_stage, prim_spec, src_layer)
                            except Exception:
                                pass
                            try:
                                dst_prim_spec = dst_layer.GetPrimAtPath(prim_spec.path)
                            except Exception:
                                dst_prim_spec = None
                    except Exception:
                        pass

                    self._process_reference_or_payload_entries(src_layer, prim_spec, dst_prim_spec, "payload", visited)
                except Exception:
                    pass

                # If this primSpec contains variant sets, copy/mirror them into the dst stage so
                # variant bodies are created and then make sure their contents (payloads inside variant bodies)
                # were handled. copy_variant_sets_if_any will also call helpers to handle payloads inside variants.
                try:
                    if dst_stage is not None:
                        # pass the dst_stage so variant edit contexts write to the dst layer
                        self.copy_variant_sets_if_any(prim_spec, dst_stage, dst_layer)
                    else:
                        # as a fallback, still attempt to copy variant payloads by introspecting the variant
                        # members directly (this is less reliable but avoids loss)
                        self.copy_variant_sets_if_any(prim_spec, None, dst_layer)
                except Exception:
                    pass
        except Exception as e:
            log_warning(f" processing prims/payloads in {src_layer_abs}: {e}")


        # Save edited dst layer + optional conversion
        try:
            if self.formatted and self.format:
                desired_suffix = f".{self.format}"
                current_suffix = Path(dst_layer_abs).suffix.lower()
                # Only convert if suffix differs from desired
                if current_suffix != desired_suffix:
                    new_dst_abs = str(Path(dst_layer_abs).with_suffix(desired_suffix))
                    # Try using Sdf.Layer.Export where possible
                    try:
                        layer_obj = Sdf.Layer.FindOrOpen(dst_layer_abs)
                        if layer_obj:
                            # Export to the new filename (this may convert ascii/binary)
                            layer_obj.Export(new_dst_abs, "", {})
                            # Remove original if different
                            if os.path.exists(dst_layer_abs) and os.path.abspath(dst_layer_abs) != os.path.abspath(new_dst_abs):
                                try:
                                    os.remove(dst_layer_abs)
                                except Exception:
                                    pass
                            dst_layer_abs = new_dst_abs
                            self._copied_layers[src_layer_abs] = dst_layer_abs
                    except Exception:
                        # Fallback: try using stage.Export
                        try:
                            stage = Usd.Stage.Open(dst_layer_abs)
                            if stage:
                                stage.Export(new_dst_abs)
                                if os.path.exists(dst_layer_abs) and os.path.abspath(dst_layer_abs) != os.path.abspath(new_dst_abs):
                                    try:
                                        os.remove(dst_layer_abs)
                                    except Exception:
                                        pass
                                dst_layer_abs = new_dst_abs
                                self._copied_layers[src_layer_abs] = dst_layer_abs
                        except Exception:
                            log_warning(f": failed to convert {dst_layer_abs} to .{self.format}")

                # somehow WORKED, but not sure
                # export to desired extension (.usda/.usdc)
                # if Path(dst_layer_abs).suffix != ".usdc":
                    # new_dst_abs = str(Path(dst_layer_abs))#.with_suffix(f".{self.format}"))
                    # try:
                    #     layer_obj = Sdf.Layer.FindOrOpen(dst_layer_abs)
                    #     if layer_obj:
                    #         layer_obj.Export(new_dst_abs, "", {})
                    #         if os.path.exists(dst_layer_abs) and os.path.abspath(dst_layer_abs) != os.path.abspath(new_dst_abs):
                    #             try:
                    #                 os.remove(dst_layer_abs)
                    #             except Exception:
                    #                 pass
                    #         dst_layer_abs = new_dst_abs
                    #         self._copied_layers[src_layer_abs] = dst_layer_abs
                    # except Exception:
                    #     try:
                    #         stage = Usd.Stage.Open(dst_layer_abs)
                    #         if stage:
                    #             stage.Export(new_dst_abs)
                    #             if os.path.exists(dst_layer_abs) and os.path.abspath(dst_layer_abs) != os.path.abspath(new_dst_abs):
                    #                 try:
                    #                     os.remove(dst_layer_abs)
                    #                 except Exception as e:
                    #                     log_error(e)
                    #             dst_layer_abs = new_dst_abs
                    #             self._copied_layers[src_layer_abs] = dst_layer_abs
                    #     except Exception:
                    #         log_warning(f": failed to convert {dst_layer_abs} to .{self.format}")

            # finally save dst_layer if possible
            try:
                dst_layer.Save()
            except Exception:
                pass
            
        except Exception as e:
            log_warning(f"Exception while saving layer {dst_layer_abs}: {e}")
        
        utils.delete_empty_folders_upwards(os.path.dirname(dst_layer_abs))
        return dst_layer_abs

    # --------------------------
    # Other refactored helpers from original
    # --------------------------
    def _copy_sublayers(self, src_layer: Sdf.Layer, src_layer_abs: str, dst_layer_abs: str, dst_layer: Sdf.Layer, visited: Set[str]) -> List[str]:
        """Extracted helper: copy sublayers of src_layer into dst and return new subLayerPaths list."""
        new_sublayer_paths: List[str] = []
        for sub in src_layer.subLayerPaths:
            sub_path: str = str(sub)
            sub_abs: Optional[str] = None
            try:
                resolved = Sdf.ComputeAssetPathRelativeToLayer(src_layer, sub_path)
                if resolved:
                    sub_abs = os.path.abspath(resolved)
                else:
                    sub_abs = os.path.normpath(os.path.join(os.path.dirname(src_layer_abs), sub_path))
            except Exception:
                sub_abs = os.path.normpath(os.path.join(os.path.dirname(src_layer_abs), sub_path))

            copied: Optional[str] = None
            if os.path.exists(sub_abs):
                copied = self.copy_layer_and_deps(sub_abs, visited)
            else:
                alt_candidates: List[str] = [
                    os.path.normpath(os.path.join(os.path.dirname(src_layer_abs), "./" + sub_path)),
                ]
                if self.source_root:
                    alt_candidates.extend([
                        os.path.normpath(os.path.join(self.source_root, sub_path)),
                        os.path.normpath(os.path.join(self.source_root, "./" + sub_path)),
                    ])
                for cand in alt_candidates:
                    if os.path.exists(cand):
                        copied = self.copy_layer_and_deps(cand, visited)
                        break

            if copied:
                rel_to_dst_layer = os.path.relpath(copied, os.path.dirname(dst_layer_abs)).replace(os.sep, "/")
                new_sublayer_paths.append(rel_to_dst_layer)
            else:
                new_sublayer_paths.append(sub_path)

        if new_sublayer_paths:
            try:
                dst_layer.subLayerPaths = new_sublayer_paths
            except Exception:
                pass
        return new_sublayer_paths

    def _process_prims_for_layer(self, src_layer: Sdf.Layer, dst_layer: Sdf.Layer, dst_layer_abs: str, visited: Set[str]) -> None:
        """Extracted helper: process prims in src_layer, handle references/payloads and variants."""
        dst_stage = None
        try:
            dst_stage = Usd.Stage.Open(dst_layer_abs)
        except Exception:
            try:
                dst_stage = Usd.Stage.Open(dst_layer.resolvedPath) if getattr(dst_layer, "resolvedPath", None) else None
            except Exception:
                dst_stage = None
        stack = list(src_layer.rootPrims)
        while stack:
            prim_spec = stack.pop()
            stack.extend(reversed(prim_spec.nameChildren))

            try:
                dst_prim_spec = dst_layer.GetPrimAtPath(prim_spec.path)
            except Exception:
                dst_prim_spec = None
            # Delegate processing of references/payloads to existing helper
            try:
                self.process_reference_or_payload_entries(src_layer, prim_spec, dst_prim_spec, "reference", self, visited)
            except Exception:
                pass
            try:
                self.process_reference_or_payload_entries(src_layer, prim_spec, dst_prim_spec, "payload", self, visited)
            except Exception:
                pass
            try:
                if dst_stage is not None:
                    self.copy_variant_sets_if_any(prim_spec, dst_stage, dst_layer)
                else:
                    self.copy_variant_sets_if_any(prim_spec, None, dst_layer)
            except Exception:
                pass
        return

    def copy_asset(self, asset_path: Sdf.AssetPath, src_usd_dir: str, dst_usd_dir: str, base_layer: Sdf.Layer = None):
        """
        Keep robust path handling and return new Sdf.AssetPath that points into dst_usd_dir.
        If base_layer is provided, prefer to resolve the authored path relative to that layer using
        Sdf.ComputeAssetPathRelativeToLayer.
        """
        # Try to use base_layer resolution first
        src_asset_abs = None
        try:
            if base_layer is not None:
                cand = Sdf.ComputeAssetPathRelativeToLayer(base_layer, asset_path.path)
                if cand == asset_path.path:
                    log_debug(f"Resolved {cand} == {asset_path.path}")
                if cand:
                    cand_abs = os.path.abspath(cand)
                    if os.path.exists(cand_abs):
                        src_asset_abs = cand_abs
        except Exception:
            src_asset_abs = None

        if not src_asset_abs:
            # Fallback: resolve from src_usd_dir using asset_path.resolvedPath
            src_asset_rel = asset_path.resolvedPath.replace("/", os.sep)
            src_asset_abs = os.path.normpath(os.path.join(src_usd_dir, src_asset_rel))

        if not os.path.exists(src_asset_abs) or not os.path.isfile(src_asset_abs):
            # Try variants: with ./ prefix or in source_root
            alt_candidates = []
            alt_candidates.append(os.path.normpath(os.path.join(src_usd_dir, '.' + os.sep + src_asset_rel)))
            if self.source_root:
                alt_candidates.append(os.path.normpath(os.path.join(self.source_root, src_asset_rel)))
                alt_candidates.append(os.path.normpath(os.path.join(self.source_root, '.' + os.sep + src_asset_rel)))

            found = False
            for cand in alt_candidates:
                if os.path.exists(cand) and os.path.isfile(cand):
                    log_stdout(f"Resolved asset by alternative path: {cand}")
                    src_asset_abs = cand
                    found = True
                    break
            if not found:
                log_warning(f": Asset not found at expected location: {src_asset_abs}")
                return None

        # If it's a USD-like file, recursively copy its layer and dependencies
        if utils.is_usd_like(src_asset_abs):
            new_dst_abs = self.copy_layer_and_deps(src_asset_abs, set())
            if not new_dst_abs:
                return None
            rel_dst_asset_path = os.path.relpath(new_dst_abs, dst_usd_dir).replace(os.sep, "/")
            return Sdf.AssetPath(rel_dst_asset_path)

        # Non-USD assets: simple copy (as before) - EXR etc
        return self._copy_non_usd_asset(src_asset_abs, dst_usd_dir)

    def copy_attribute_with_time_samples(self, attr_spec: Sdf.AttributeSpec, dst_attr: Usd.Attribute, layer: Sdf.Layer):
        try:
            if attr_spec.default is not None:
                dst_attr.Set(attr_spec.default)
        except Exception:
            pass
        attr_path = attr_spec.path
        time_samples = layer.ListTimeSamplesForPath(attr_path)
        values = [layer.QueryTimeSample(attr_path, t) for t in time_samples]
        if values:
            for i in range(len(values)):
                try:
                    dst_attr.Set(values[i], time_samples[i])
                except Exception:
                    pass
        # Copy authored asset path values (textures, references inside attributes)
        try:
            if attr_spec.default is not None and isinstance(attr_spec.default, Sdf.AssetPath) and self.src_usd_dir and self.dst_usd_dir:
                
                self.copy_asset(attr_spec.default, self.src_usd_dir, self.dst_usd_dir, base_layer=layer)
        except Exception:
            pass

    def copy_variant_sets_if_any(self, prim_spec: Sdf.PrimSpec, dst_stage: Usd.Stage, dst_layer: Sdf.Layer):
        """
        Copy authored variant sets and their variants from prim_spec into the dst_stage.
        Recursively process nested variant sets so payloads deep in the tree (e.g. .usdc inside variantSets)
        get discovered and copied.

        This will:
        - materialize variant bodies into dst_stage (if dst_stage is provided),
        - traverse every primSpec inside each variant body (depth-first),
        - run _process_reference_or_payload_entries(...) for references and payloads (layer-relative),
        - recurse into referenced/payload layers found,
        - recurse into nested variantSets found inside variant bodies.
        """
        try:
            src_layer = prim_spec.layer

            # If the source layer is a binary .usdc that can't be introspected, try to skip safely.
            if src_layer and getattr(src_layer, "realPath", None) and src_layer.realPath.endswith(".usdc"):
                try:
                    _ = src_layer.ExportToString()
                except Exception:
                    # Can't introspect binary layer — no variant info we can read here
                    return

            # Get variant set names
            variant_sets: dict = getattr(prim_spec, 'variantSetNames', None) or getattr(prim_spec, 'variantSets', None)
            if not variant_sets:
                return

            if hasattr(variant_sets, 'keys'):
                variant_set_names = list(variant_sets.keys())
            elif hasattr(variant_sets, '__iter__'):
                variant_set_names = list(variant_sets)
            else:
                return
            # Ensure we have a dst_prim if a dst_stage was given.
            dst_prim = None
            if dst_stage is not None:
                try:
                    dst_prim = dst_stage.GetPrimAtPath(prim_spec.path)
                except Exception:
                    dst_prim = None
                if not dst_prim:
                    # Nothing to write into; safe to return early
                    return
            else:
                # If no dst_stage, we will still attempt to process payloads by inspecting primSpecs
                dst_prim = None

            # visited keeps track of processed (layer, primPath) tuples to avoid reprocessing
            local_visited = set()

            # Helper to process a variant body primSpec tree depth-first
            def _process_variant_body_tree(root_prim_spec: Sdf.PrimSpec):
                stack = [root_prim_spec]
                while stack:
                    ps = stack.pop()
                    # push children (nameChildren includes variant-members too)
                    stack.extend(reversed(ps.nameChildren))

                    # traverse into variant sets and their variant primSpecs
                    try:
                        for vs in getattr(ps, 'variantSets', {}).values():
                            for variant in getattr(vs, 'variants', {}).values():
                                vps = getattr(variant, 'primSpec', None)
                                if vps:
                                    stack.append(vps)
                    except Exception as e:
                            print('DEBUG: suppressed exception in traversal:', e)


                    # Avoid reprocessing same prim in same layer
                    key = (getattr(ps.layer, "identifier", None), str(ps.path))
                    if key in local_visited:
                        continue
                    local_visited.add(key)

                    # If possible, define the prim in dst_stage/dst_layer so payload rewriting can happen
                    if dst_stage is not None:
                        try:
                            # define_prim_if_missing expects a stage; this will define Def or Over as authored
                            self.define_prim_if_missing(dst_stage, ps, dst_layer)
                        except Exception:
                            pass

                    # Grab dst prim spec if available in dst_layer so we can author rewritten ref/payload entries
                    try:
                        dst_child_spec = dst_layer.GetPrimAtPath(ps.path) if dst_layer is not None else None
                    except Exception:
                        dst_child_spec = None

                    # Process references and payloads authored on this primSpec
                    try:
                        
                        try:
                            if dst_prim_spec is None:
                                try:
                                    self.define_prim_if_missing(dst_stage, prim_spec, src_layer)
                                except Exception:
                                    pass
                                try:
                                    dst_prim_spec = dst_layer.GetPrimAtPath(prim_spec.path)
                                except Exception:
                                    dst_prim_spec = None
                        except Exception:
                            pass

                        self._process_reference_or_payload_entries(src_layer, ps, dst_child_spec, "reference", local_visited)
                    except Exception:
                        pass
                    try:
                        
                        try:
                            if dst_prim_spec is None:
                                try:
                                    self.define_prim_if_missing(dst_stage, prim_spec, src_layer)
                                except Exception:
                                    pass
                                try:
                                    dst_prim_spec = dst_layer.GetPrimAtPath(prim_spec.path)
                                except Exception:
                                    dst_prim_spec = None
                        except Exception:
                            pass

                        self._process_reference_or_payload_entries(src_layer, ps, dst_child_spec, "payload", local_visited)
                    except Exception:
                        pass

                    # After processing entries, recurse into referenced/payloaded layers found here
                    try:
                        self.recurse_into_referenced_layers(ps.layer, local_visited)
                    except Exception:
                        pass

                    # If this primSpec contains nested variantSets, recurse into them
                    try:
                        nested_vs = getattr(ps, 'variantSetNames', None) or getattr(ps, 'variantSets', None)
                        log_debug(nested_vs)
                        if nested_vs:
                            # call copy_variant_sets_if_any recursively — it will process nested variant sets
                            try:
                                self.copy_variant_sets_if_any(ps, dst_stage, dst_layer)
                            except Exception:
                                pass
                    except Exception:
                        pass

            # For each top-level variant set on prim_spec
            for vs_name in variant_set_names:
                try:
                    src_vset: Sdf.VariantSetSpec = prim_spec.variantSets.get(vs_name) if hasattr(prim_spec, "variantSets") else None
                    # Create the variant set in destination prim (dst_prim already validated above)
                    dst_vset = None
                    if dst_prim is not None:
                        try:
                            dst_vset = dst_prim.GetVariantSets().AddVariantSet(vs_name)
                        except Exception:
                            # fallback older API
                            try:
                                dst_vset = dst_prim.GetVariantSets().AddVariantSet(vs_name)
                            except Exception:
                                dst_vset = None

                    # Collect variant names
                    if src_vset and hasattr(src_vset, "variantList"):
                        variant_names = [v.name for v in src_vset.variantList]
                    else:
                        variant_names = []
                        child: Sdf.PrimSpec
                        for child in prim_spec.nameChildren:
                            if child.name.startswith(vs_name + '='):
                                vname = child.name.split('=')[-1]
                                if vname not in variant_names:
                                    variant_names.append(vname)

                    # Iterate variants
                    for vname in variant_names:
                        # Add variant and select it on the dst vset (if we have one)
                        if dst_vset is not None:
                            try:
                                dst_vset.AddVariant(vname)
                                dst_vset.SetVariantSelection(vname)
                            except Exception:
                                try:
                                    dst_vset.SetVariantSelection(vname)
                                except Exception:
                                    pass

                        # Enter variant edit context if we have a dst vset; otherwise operate directly on primSpecs
                        if dst_vset is not None:
                            ctx = dst_vset.GetVariantEditContext()
                        else:
                            ctx = None

                        if ctx is not None:
                            with ctx:
                                variant_prim = next((c for c in prim_spec.nameChildren if c.name == f"{vs_name}={vname}"), None)
                                if not variant_prim:
                                    continue
                                # Process entire variant body tree (depth-first)
                                _process_variant_body_tree(variant_prim)
                        else:
                            # No dst stage/variant context — still traverse variant primSpec tree and process payloads
                            variant_prim = next((c for c in prim_spec.nameChildren if c.name == f"{vs_name}={vname}"), None)
                            if not variant_prim:
                                continue
                            _process_variant_body_tree(variant_prim)

                except Exception as e:
                    log_warning(f"⚠️ Failed copying variant set {vs_name} on {prim_spec.path}: {e}")

        except Exception as e:
            log_warning(f"⚠️ Skipping variant sets on {prim_spec.path}: {e}")

    def copy_prim_attributes(self, prim_spec: Sdf.PrimSpec, dst_prim: Usd.Prim, layer: Sdf.Layer):
        # Copy attributes
        for attr_name, attr_spec in prim_spec.attributes.items():
            try:
                attr_spec: Sdf.AttributeSpec
                dst_attr = dst_prim.CreateAttribute(attr_spec.name, attr_spec.typeName)
                self.copy_attribute_with_time_samples(attr_spec, dst_attr, layer)
            except Exception as e:
                log_warning(f" copying attribute {attr_name} on {prim_spec.path}: {e}")

        # Copy relationships (collections, material bindings, etc.)
        try:
            for rel_name, rel_spec in prim_spec.relationships.items():
                try:
                    rel_spec: Sdf.RelationshipSpec
                    dst_rel = dst_prim.CreateRelationship(rel_spec.name)
                    for t in getattr(rel_spec, "targetPaths", []):
                        dst_rel.AddTarget(t)
                except Exception:
                    pass
        except Exception:
            pass

        # Copy references and payloads using the shared helper (but note this is for authored prim -> runtime prim)
        try:
            if hasattr(prim_spec, "referenceList"):
                refs = []
                refs.extend(prim_spec.referenceList.prependedItems)
                refs.extend(prim_spec.referenceList.addedItems)
                refs.extend(prim_spec.referenceList.orderedItems)
                refs.extend(prim_spec.referenceList.appendedItems)
                refs.extend(prim_spec.referenceList.explicitItems)
                for r in refs:
                    r: Sdf.Reference
                    new_asset_path = self.copy_asset(Sdf.AssetPath(r.assetPath), self.src_usd_dir, self.dst_usd_dir, base_layer=layer)
                    if new_asset_path:
                        dst_refs = dst_prim.GetReferences()
                        dst_refs.AddReference(Sdf.Reference(new_asset_path.path, r.primPath))
        except Exception as e:
            log_warning(f" copying references on {prim_spec.path}: {e}")

        try:
            if hasattr(prim_spec, "payloadList"):
                payloads = []
                payloads.extend(prim_spec.payloadList.prependedItems)
                payloads.extend(prim_spec.payloadList.addedItems)
                payloads.extend(prim_spec.payloadList.orderedItems)
                payloads.extend(prim_spec.payloadList.appendedItems)
                payloads.extend(prim_spec.payloadList.explicitItems)

                for p in payloads:
                    p: Sdf.Payload
                    new_asset_path = self.copy_asset(Sdf.AssetPath(p.assetPath), self.src_usd_dir, self.dst_usd_dir, base_layer=layer)
                    if new_asset_path:
                        dst_payloads = dst_prim.GetPayloads()
                        dst_payloads.AddPayload(Sdf.Payload(new_asset_path.path, p.primPath))
        except Exception as e:
            log_warning(f" copying payloads on {prim_spec.path}: {e}")

    def define_prim_if_missing(self, stage: Usd.Stage, prim_spec: Sdf.PrimSpec, layer: Sdf.Layer):
        dst_prim = None
        try:
            if prim_spec.specifier == Sdf.SpecifierDef:
                typename = prim_spec.typeName or ''
                dst_prim = stage.DefinePrim(prim_spec.path, typename)
                self.DEFINED_OBJECTS.add(prim_spec.name)
            elif prim_spec.specifier == Sdf.SpecifierOver:
                dst_prim = stage.OverridePrim(prim_spec.path)
            if dst_prim is not None:
                self.copy_prim_attributes(prim_spec, dst_prim, layer)
                # Attempt to copy variantSets if present
                try:
                    #self.copy_variant_sets_if_any(prim_spec, stage, stage.GetRootLayer())
                    self.copy_variant_sets_if_any(prim_spec, stage, layer)
                except Exception:
                    pass
                return True
        except Exception as e:
            log_error(f" defining prim {prim_spec.path}: {e}")
        return False

    def _resolve_reference_layer(self, base_layer: Sdf.Layer, asset_path: str):
        # Try relative to layer, then try as-is, then try with ./, then try under source_root
        try:
            cand = Sdf.Layer.FindOrOpenRelativeToLayer(base_layer, asset_path)
            if cand:
                return cand
        except Exception:
            pass
        try:
            cand = Sdf.Layer.FindOrOpen(asset_path)
            if cand:
                return cand
        except Exception:
            pass
        # Try small heuristic changes
        variants = [asset_path, './' + asset_path, asset_path.lstrip('./')]
        for v in variants:
            try:
                cand = Sdf.Layer.FindOrOpenRelativeToLayer(base_layer, v)
                if cand:
                    return cand
            except Exception:
                pass
            try:
                cand = Sdf.Layer.FindOrOpen(v)
                if cand:
                    return cand
            except Exception:
                pass
        # Try searching under source_root
        if self.source_root:
            try_paths = [os.path.join(self.source_root, asset_path), os.path.join(self.source_root, './' + asset_path)]
            for p in try_paths:
                try:
                    cand = Sdf.Layer.FindOrOpen(os.path.normpath(p))
                    if cand:
                        return cand
                except Exception:
                    pass
        return None

    def filter_prims(self, dst_stage, layer: Sdf.Layer, visited: set) -> bool:
        # Returns True if something was defined in dst_stage
        stack = list(layer.rootPrims)
        any_defined = False
        while stack:
            prim_spec: Sdf.PrimSpec = stack.pop()
            stack.extend(reversed(prim_spec.nameChildren))
            matched = False
            if prim_spec.typeName in self.active_filter_typenames:
                matched = True
            if (not matched) and self.include_geometries:
                if prim_spec.typeName and (prim_spec.typeName in GEOM_FILTERS):
                    matched = True
            if (not matched) and self.include_references:
                # First: quickly check whether any authored reference points to a prim type we want

                #TODO: Check this block and find how to copy files in nontype references
                try:
                    if self._referenced_prim_matches_filters(prim_spec, layer):
                        matched = True
                    else:
                        # Fallback: existing behaviour — recurse into referenced layers and create outputs.
                        #TODO: CHECK!
                        #refs = utils.resolve_asset_absolute_path(prim_spec.nameRoot, assetPath, layer)
                        refs = utils.resolve_reference_items(prim_spec)
                        #print(refs)
                        if refs:
                            for assetPath, primPath in refs:
                                ref_layer = self._resolve_reference_layer(layer, assetPath)
                                if ref_layer:
                                    tmp_visited = set(visited)
                                    created = self._process_layer_recursively(ref_layer, tmp_visited)
                                    if created:
                                        matched = True
                                        break
                except Exception:
                    pass

            if matched or prim_spec.name in self.DEFINED_OBJECTS:
                # Define this prim and its ancestors
                ancestor = prim_spec
                while ancestor:
                    defined = self.define_prim_if_missing(dst_stage, ancestor, layer)
                    if defined:
                        any_defined = True
                    ancestor = ancestor.nameParent

                # If we included references and matched by references, ensure the reference remains but
                # if the referenced layer produced no created output, remove the reference from the dst prim
                if self.include_references:
                    #refs = utils.resolve_asset_absolute_path(prim_spec.nameRoot, prim_spec.assetInfo, layer)
                    refs = utils.resolve_reference_items(prim_spec)
                    for assetPath, primPath in refs:
                        ref_layer = self._resolve_reference_layer(layer, assetPath)
                        if ref_layer:
                            try:
                                rel_ref = os.path.relpath(ref_layer.realPath, self.src_usd_dir).replace('\\', '/')
                                dst_ref_abs = os.path.normpath(os.path.join(self.dst_usd_dir, rel_ref))
                                if not os.path.exists(dst_ref_abs):
                                    # referenced output not created -> do not keep
                                    pass
                            except Exception:
                                pass
        return any_defined

    def fix_sublayer_paths_relative_to_layer(self, layer: Sdf.Layer):
        changed = False
        new_paths = []

        for sub_path in layer.subLayerPaths:
            resolved_path = Sdf.ComputeAssetPathRelativeToLayer(layer, sub_path)
            abs_path = os.path.abspath(resolved_path).replace("\\", "/")
            layer_dir = os.path.dirname(os.path.abspath(layer.realPath))
            rel_path = os.path.relpath(abs_path, layer_dir).replace("\\", "/")
            
            mnt = sub_path.find(":/") # find on different disc mount
            if mnt != -1:
                rel_path = sub_path[mnt-1:]
            elif sub_path[:2] == "./":
                # keep relative if in layer root folder
                rel_path = f"./{rel_path}"

            if rel_path != sub_path:
                log_stdout(f"Updating subLayerPath in {layer.identifier}:\n  {sub_path} → {rel_path}")
                changed = True
            new_paths.append(rel_path)

            sublayer = Sdf.Layer.FindOrOpen(abs_path)
            if sublayer:
                self.fix_sublayer_paths_relative_to_layer(sublayer)

        if changed:
            layer.subLayerPaths = new_paths
            return new_paths


    # --------------------------
    # Layer traversal & export (kept logic, but integrated with helpers)
    # --------------------------
    def _process_layer_recursively(self, input_layer: Sdf.Layer, visited: set) -> bool:
        if not input_layer:
            log_warning(f"⚠️ Skipping unreadable layer.")
            return False
        if input_layer.identifier in visited:
            return False
        visited.add(input_layer.identifier)

        log_stdout(f"📂 Processing layer: {input_layer.identifier}")

        # Recurse into sublayers first (depth-first, reverse order)
        self.recurse_into_sublayer(input_layer, visited)
        # also recurse into referenced/payloaded layers (leaf-most USDs)
        self.recurse_into_referenced_layers(input_layer, visited)

        # Determine relative output path for this layer
        rel_layer_path = os.path.relpath(input_layer.realPath, self.src_usd_dir)
        output_layer_path = os.path.normpath(os.path.join(self.dst_usd_dir, rel_layer_path)).replace("\\", "/")

        try:
            os.makedirs(os.path.dirname(output_layer_path), exist_ok=True)

            dst_stage = Usd.Stage.CreateNew(output_layer_path)
            dst_layer = dst_stage.GetRootLayer()

            src_stage = Usd.Stage.Open(input_layer.identifier)
            default_prim = src_stage.GetDefaultPrim()
            if default_prim:
                dst_stage.SetDefaultPrim(dst_stage.DefinePrim(default_prim.GetPath(), default_prim.GetTypeName()))
                
            original_path = dst_layer.realPath
            self.fix_sublayer_paths_relative_to_layer(input_layer)
            dst_layer.subLayerPaths = input_layer.subLayerPaths[:]  # retain structure
            if self.formatted:# and input_layer.fileExtension.lower() != "usdc":
                dst_layer.subLayerPaths = utils.change_path_extensions(dst_layer.subLayerPaths, self.format)

            dst_layer.startTimeCode = input_layer.startTimeCode
            dst_layer.endTimeCode = input_layer.endTimeCode

            copy_root_metadata(src_stage, dst_stage)
            copy_root_metadata_animated(src_stage, dst_stage)

            # Filter and copy prims
            created_any = self.filter_prims(dst_stage, input_layer, visited)

            if created_any:
                if self.formatted:# and dst_layer.fileExtension.lower() != "usdc":
                    new_path = str(Path(str(dst_layer.resolvedPath)).with_suffix(f".{self.format}"))
                    dst_layer.Export(new_path, "USD-Transfer", {})
                    log_stdout(f"Exported layer to path {new_path}")
                    if os.path.exists(original_path) and original_path != new_path:
                        os.remove(original_path)
                        log_stdout(f"Deleted original layer at {original_path}")
                else:
                    dst_layer.Save(True)
                    new_path = str(Path(str(dst_layer.resolvedPath)))
                    log_stdout(f"Exported layer to path {new_path}")
                return True
            else:
                # No prims matched for this layer: cleanup created file
                try:
                    if os.path.exists(output_layer_path):
                        os.remove(output_layer_path)
                        log_stdout(f"Removed empty exported layer: {output_layer_path}")
                        utils.delete_empty_folders_upwards(os.path.dirname(output_layer_path))
                except Exception:
                    pass
                return False

        except PermissionError:
            log_error(f"Permission denied.")
            return False
        except FileNotFoundError:
            log_error(f"File not found.")
            return False
        except Exception as e:
            log_error(f"An unexpected error occurred: {e.with_traceback()}")
            return False

    def recurse_into_sublayer(self, input_layer: Sdf.Layer, visited: set):
        for sublayer_entry in reversed(input_layer.subLayerPaths):
            sublayer_asset_path = str(sublayer_entry)
            # Prefer ComputeAssetPathRelativeToLayer to find the actual file
            resolved = Sdf.ComputeAssetPathRelativeToLayer(input_layer, sublayer_asset_path)
            if resolved:
                sublayer = Sdf.Layer.FindOrOpen(resolved)
            else:
                sublayer = Sdf.Layer.FindOrOpenRelativeToLayer(input_layer, sublayer_asset_path)

            if (sublayer):
                log_stdout(f"✅ Found sublayer: {str(sublayer.resolvedPath)}")
                self._process_layer_recursively(sublayer, visited)
            else:
                sublayer = Sdf.Layer.FindOrOpen(sublayer_asset_path)
                if sublayer:
                    log_stdout(f"✅ Alt-Found sublayer: {str(sublayer.resolvedPath)}")
                    self._process_layer_recursively(sublayer, visited)
                else:
                    log_error(f"❌ Could not resolve sublayer: {sublayer_asset_path}")

    def _resolve_reference_items(self, prim_spec):
        refs = []
        try:
            rlist = getattr(prim_spec, "referenceList", None)
            if rlist is None:
                maybe_refs = getattr(prim_spec, "references", None)
                if maybe_refs:
                    for r in maybe_refs:
                        r: Sdf.Reference
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

    # # --------------------------
    # # New: reexport_layouts (keeps behavior)
    # # --------------------------
    # def reexport_layouts(self, input_usd_path: str, output_dir: str,
    #                      source_root: str = None, format: str = None) -> list:
    #     """
    #     Re-export (copy) the given USD layer and all of its sublayers / references / payloads
    #     recursively into output_dir. Returns a list of destination absolute paths that were created/copied.

    #     This uses the same internal copy_layer_and_deps recursion, which copies USD-like files
    #     using Sdf/Stage-aware editing, and non-USD assets as simple files.
    #     """
    #     self.source_root = source_root or self.source_root
    #     self.src_usd_dir = os.path.dirname(os.path.abspath(input_usd_path))
    #     self.dst_usd_dir = os.path.abspath(output_dir)
    #     self.format = format
    #     self.formatted = (self.format is not None)
    #     os.makedirs(self.dst_usd_dir, exist_ok=True)

    #     visited = set()
    #     self._copied_layers.clear()
    #     result = self.copy_layer_and_deps(os.path.abspath(input_usd_path), visited)
    #     if not result:
    #         log_error("❌ Re-export failed (couldn't copy root layer).")
    #         return []

    #     # return list of copied absolute paths
    #     copied_list = list(self._copied_layers.values())
    #     log_stdout(f"✅ Re-exported {len(copied_list)} files into {self.dst_usd_dir}")
    #     return copied_list

    def _referenced_prim_matches_filters(self, prim_spec: Sdf.PrimSpec, src_layer: Sdf.Layer) -> bool:
        """
        Inspect the prim_spec's authored references and payloads and return True if any
        referenced prim's authored typeName matches active_filter_typenames.
        Uses src_layer to resolve authored asset paths (via ComputeAssetPathRelativeToLayer).
        """
        try:
            refs = utils.resolve_reference_items(prim_spec)
            for assetPath, primPath in refs:
                # Resolve authored asset path relative to the source layer (preferred)
                asset_abs = utils.resolve_asset_absolute_path(self.source_root, assetPath, src_layer)
                log_debug(f"resolve_asset_absolute_path output: {asset_abs}")
                #log_debug(f"resolve_reference_items output: {asset_abs}")
                # Try opening layers using resolved absolute path or the authored path relative to layer
                layer = None
                if asset_abs and os.path.exists(asset_abs):
                    layer = Sdf.Layer.FindOrOpen(asset_abs)
                if layer is None:
                    # try opening relative to src_layer
                    try:
                        cand = Sdf.ComputeAssetPathRelativeToLayer(src_layer, assetPath)
                        if cand:
                            layer = Sdf.Layer.FindOrOpen(cand)
                    except Exception:
                        pass
                if layer is None:
                    # last resorts
                    layer = Sdf.Layer.FindOrOpenRelativeToLayer(src_layer, assetPath) or Sdf.Layer.FindOrOpen(assetPath)
                if not layer:
                    continue

                # If primPath is empty, try defaultPrim
                auth_prim_path = primPath or layer.defaultPrim or ""
                if not auth_prim_path:
                    continue

                try:
                    # Get the authored prim spec from the referenced layer
                    ref_prim_spec = layer.GetPrimAtPath(auth_prim_path)
                    if ref_prim_spec and getattr(ref_prim_spec, "typeName", None):
                        if ref_prim_spec.typeName in self.active_filter_typenames:
                            return True
                except Exception:
                    # fall back to opening a stage and querying prim type
                    try:
                        st = Usd.Stage.Open(layer.realPath)
                        if st:
                            p = st.GetPrimAtPath(auth_prim_path)
                            if p and p.GetTypeName() and p.GetTypeName() in self.active_filter_typenames:
                                return True
                    except Exception:
                        pass
        except Exception:
            pass
        return False

    def recurse_into_referenced_layers(self, input_layer: Sdf.Layer, visited: set):
        """
        Find all authored reference/payload assets in `input_layer` (using layer-relative
        resolution), and recursively process each referenced layer (depth-first).
        This ensures we reach bottom-most layers that actually define lights/cameras/etc.
        """
        found = []  # store tuples (resolved_layer_path, authored_asset_path)
        try:
            # Iterate all prims and collect references/payloads
            for prim_spec in input_layer.rootPrims:
                stack = [prim_spec]
                while stack:
                    ps = stack.pop()
                    ps: Sdf.PrimSpec
                    stack.extend(reversed(ps.nameChildren))

                    # traverse into variant sets and their variant primSpecs
                    try:
                        for vs in getattr(ps, 'variantSets', {}).values():
                            for variant in getattr(vs, 'variants', {}).values():
                                vps = getattr(variant, 'primSpec', None)
                                if vps:
                                    stack.append(vps)
                    except Exception as e:
                            print('DEBUG: suppressed exception in traversal:', e)


                    # resolve reference-like entries
                    for list_attr in ("referenceList", "references", "payloadList", "payloads"):
                        lst = getattr(ps, list_attr, None)
                        if not lst:
                            continue
                        # Try common container names
                        for attr in ("prependedItems", "appendedItems", "explicitItems", "references", "payloads"):
                            items = getattr(lst, attr, None)
                            if not items:
                                continue
                            for e in items:
                                asset = getattr(e, "assetPath", None) or getattr(e, "assetPath", "")
                                if not asset:
                                    continue
                                # Prefer ComputeAssetPathRelativeToLayer
                                try:
                                    resolved = Sdf.ComputeAssetPathRelativeToLayer(input_layer, str(asset))
                                except Exception:
                                    resolved = None
                                if resolved and os.path.exists(resolved):
                                    found.append((os.path.abspath(resolved), str(asset)))
                                else:
                                    # fallback heuristics: relative to layer dir, source_root
                                    layer_dir = os.path.dirname(input_layer.realPath) if input_layer.realPath else None
                                    if layer_dir:
                                        cand = os.path.normpath(os.path.join(layer_dir, str(asset)))
                                        if os.path.exists(cand):
                                            found.append((os.path.abspath(cand), str(asset)))
                                            continue
                                    if self.source_root:
                                        cand2 = os.path.normpath(os.path.join(self.source_root, str(asset)))
                                        if os.path.exists(cand2):
                                            found.append((os.path.abspath(cand2), str(asset)))
        except Exception:
            # be robust — do not raise here
            pass

        # Deduplicate by absolute path and recurse depth-first
        seen = set()
        # process in reverse to keep last-first behavior if ordering matters
        for abs_path, authored in reversed(found):
            if not abs_path or abs_path in seen:
                continue
            seen.add(abs_path)
            try:
                sublayer = Sdf.Layer.FindOrOpen(abs_path)
            except Exception:
                sublayer = None
            if sublayer:
                self._process_layer_recursively(sublayer, visited)
            else:
                # As additional fallback try FindOrOpenRelativeToLayer with authored path
                try:
                    sublayer = Sdf.Layer.FindOrOpenRelativeToLayer(input_layer, authored)
                except Exception:
                    sublayer = None
                if sublayer:
                    self._process_layer_recursively(sublayer, visited)

    # --------------------------
    # Public high-level interface (keeps existing signature)
    # --------------------------
    def preflight_copy_assets_in_layer(self, input_layer: Sdf.Layer, visited: set) -> None:
        """
        Walk the layer (including variant bodies) and ensure all authored reference/payload assets
        are copied (or scheduled) into the destination layout **before** any Usd.Stage.Open that would
        trigger composition/instantiation. This prevents USD from warning about missing payloads
        when stages are opened.
        """
        try:
            layer_dir = os.path.dirname(input_layer.realPath) if getattr(input_layer, 'realPath', None) else None
        except Exception:
            layer_dir = None

        def _process_prim_for_assets(ps: Sdf.PrimSpec):
            # Handle reference and payload lists on this primSpec
            for list_attr in ("referenceList", "references", "payloadList", "payloads"):
                lst = getattr(ps, list_attr, None)
                if not lst:
                    continue
                for attr in ("prependedItems", "appendedItems", "explicitItems", "references", "payloads"):
                    items = getattr(lst, attr, None)
                    if not items:
                        continue
                    for e in items:
                        asset = getattr(e, 'assetPath', None) or getattr(e, 'asset', None) or None
                        if not asset:
                            continue
                        asset_str = str(asset)
                        # Prefer ComputeAssetPathRelativeToLayer when possible
                        asset_abs = None
                        try:
                            try:
                                cand = Sdf.ComputeAssetPathRelativeToLayer(input_layer, asset_str)
                                if cand:
                                    cand_abs = os.path.abspath(cand)
                                    if os.path.exists(cand_abs):
                                        asset_abs = cand_abs
                            except Exception:
                                asset_abs = None
                        except Exception:
                            asset_abs = None
                        if not asset_abs:
                            # try layer directory heuristic
                            if layer_dir:
                                cand = os.path.normpath(os.path.join(layer_dir, asset_str))
                                if os.path.exists(cand):
                                    asset_abs = os.path.abspath(cand)
                        if not asset_abs and self.source_root:
                            cand2 = os.path.normpath(os.path.join(self.source_root, asset_str))
                            if os.path.exists(cand2):
                                asset_abs = os.path.abspath(cand2)
                        if not asset_abs:
                            # last: maybe the authored string is already absolute or relative to cwd
                            if os.path.exists(asset_str):
                                asset_abs = os.path.abspath(asset_str)
                        if not asset_abs:
                            # nothing we can do now
                            continue
                        # Copy the asset now (USD-like -> recurse, else copy file)
                        try:
                            if utils.is_usd_like(asset_abs):
                                # use existing copy_layer_and_deps to recursively copy USD layers
                                _ = self.copy_layer_and_deps(asset_abs, visited)
                            else:
                                # non-USD file copy - preserve layout relative to src_usd_dir
                                try:
                                    rel_dst = os.path.normpath(os.path.join(self.dst_usd_dir, os.path.relpath(asset_abs, self.src_usd_dir)))
                                    os.makedirs(os.path.dirname(rel_dst), exist_ok=True)
                                    shutil.copy2(asset_abs, rel_dst)
                                except Exception:
                                    utils.delete_empty_folders_upwards(os.path.dirname(rel_dst))
                                    pass
                        except Exception:
                            pass

        # Traverse root prims (depth-first) and variant bodies similarly to other walkers
        try:
            stack = list(input_layer.rootPrims)
            while stack:
                ps: Sdf.PrimSpec
                ps = stack.pop()
                stack.extend(reversed(ps.nameChildren))
                try:
                    _process_prim_for_assets(ps)
                except Exception:
                    pass
                # Also handle variant body children inside ps similar to copy_variant_sets_if_any
                try:
                    nested_vs: dict = getattr(ps, 'variantSetNames', None) or getattr(ps, 'variantSets', None)
                    if nested_vs:
                        # collect variant set names
                        if hasattr(nested_vs, 'keys'):
                            vs_names = list(nested_vs.keys())
                        elif hasattr(nested_vs, '__iter__'):
                            vs_names = list(nested_vs)
                        else:
                            vs_names = []
                        for vs in vs_names:
                            try:
                                src_vset = ps.variantSets.get(vs) if hasattr(ps, 'variantSets') else None
                                if src_vset and hasattr(src_vset, 'variantList'):
                                    variant_names = [v.name for v in src_vset.variantList]
                                else:
                                    variant_names = []
                                    for child in ps.nameChildren:
                                        if child.name.startswith(vs + '='):
                                            vname = child.name.split('=')[-1]
                                            if vname not in variant_names:
                                                variant_names.append(vname)
                                for vname in variant_names:
                                    variant_prim = next((c for c in ps.nameChildren if c.name == f"{vs}={vname}"), None)
                                    if variant_prim:
                                        # walk variant body tree
                                        vstack = [variant_prim]
                                        while vstack:
                                            vps = vstack.pop()
                                            vstack.extend(reversed(vps.nameChildren))
                                            try:
                                                _process_prim_for_assets(vps)
                                            except Exception:
                                                pass
                    
                            except Exception:
                                pass
                except Exception:
                    pass
        except Exception:
            pass


    def extract_filtered_from_render_manifest(self, input_usd_path: str, output_path: str,
                                              source_root: str = "/upp/servers/vfxstore2/VFXstore/PROJECTS/",
                                              format: str = None,
                                              include_geometries: bool = False,
                                              include_references: bool = True) -> None:
        self.source_root = source_root
        self.src_usd_dir = os.path.dirname(os.path.abspath(input_usd_path))
        self.dst_usd_dir = os.path.dirname(os.path.abspath(output_path))
        self.format = format
        self.include_geometries = include_geometries
        self.include_references = include_references

        self.active_filter_typenames = list(DEFAULT_FILTER_TYPENAMES)
        if self.include_geometries:
            self.active_filter_typenames.extend(GEOM_FILTERS)
        
        log_debug(f"Active Typenames: {self.active_filter_typenames}")
        self.formatted = (self.format is not None)

        os.makedirs(self.dst_usd_dir, exist_ok=True)

        visited = set()
        input_layer = Sdf.Layer.FindOrOpen(input_usd_path)
        if input_layer:
            # Take the layer and go into its sublayers and references
            self._process_layer_recursively(input_layer=input_layer, visited=visited)
            log_stdout("✅ Done executing function: extract_lights_from_render_manifest (recursive mode)")
        else:
            log_error("❌Invalid input layer!")