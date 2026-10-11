import unreal
import re
import traceback
import json
import crowds.utils as utils
import gc

EAS = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
ES = unreal.get_editor_subsystem(unreal.EditorSubsystem)


@unreal.uclass()
class UppCrowdsConvertionBlueprintLibrary(unreal.BlueprintFunctionLibrary):

    class AnimInfo:
        animation: unreal.AnimSequence = None
        animation_length_seconds: float | None = None
        animation_length_frames: int | None   # rounded to sequence display rate = None
        animation_first_offset_frames: int | None
        sequence_display_rate_num: int | None = None
        sequence_display_rate_den: int | None = None
        section_start_frame: int | None        # in sequence display-rate frames = None
        section_end_frame: int | None = None
        section_start_time_seconds: float | None = None
        section_end_time_seconds: float | None = None
        section: unreal.MovieSceneSection = None  # the MovieSceneSection object

        def to_string(self, prefix: str = "") -> str:
            anim_name = self.animation.get_name() if self.animation else "None"
            section_name = self.section.get_name() if self.section else "None"

            return (
                f"{prefix}AnimInfo\n"
                f"{prefix}  Animation: {anim_name}\n"
                f"{prefix}  Animation Length:\n"
                f"{prefix}    Seconds: {self.animation_length_seconds}\n"
                f"{prefix}    Frames:  {self.animation_length_frames}\n"
                f"{prefix}  Sequence Display Rate: "
                f"{self.sequence_display_rate_num}/{self.sequence_display_rate_den}\n"
                f"{prefix}  Section Frames:\n"
                f"{prefix}    Start: {self.section_start_frame}\n"
                f"{prefix}    End:   {self.section_end_frame}\n"
                f"{prefix}  Section Time (seconds):\n"
                f"{prefix}    Start: {self.section_start_time_seconds}\n"
                f"{prefix}    End:   {self.section_end_time_seconds}\n"
                f"{prefix}  Section Object: {section_name}"
            )
    # ------------------------
    # Helper methods (pure-Python static helpers that preserve original logic)
    # ------------------------

    @staticmethod
    def _anim_subfolder_safe_name(anim_info):
        """
        Return a safe subfolder name for an AnimInfo instance.
        If anim_info is None, return 'noanim'.
        """
        try:
            if not anim_info or not getattr(anim_info, 'animation', None):
                return "noanim"
            anim = anim_info.animation
            # prefer outermost package name (short)
            try:
                pkg = anim.get_outermost().get_name()
                name = anim.get_name()
            except Exception:
                name = getattr(anim, 'get_name', lambda: str(anim))()
            # sanitize
            safe = re.sub(r'[^0-9A-Za-z_]+', '_', str(name)).strip('_')
            if not safe:
                safe = "anim"
            return safe
        except Exception:
            return "anim"

    @staticmethod
    def _duplicate_static_mesh_into_folder(src_static: unreal.StaticMesh, dest_folder: str, asset_tools):
        """
        Duplicate a StaticMesh into dest_folder. Reuse if same-name asset exists in dest_folder.
        Returns the duplicated (or existing) StaticMesh object or None.
        """
        try:
            if not src_static or not dest_folder or not asset_tools:
                return None
            src_name = src_static.get_name()
            target_full = dest_folder.rstrip('/') + '/' + src_name
            # Reuse if present
            if unreal.EditorAssetLibrary.does_asset_exist(target_full):
                existing = unreal.EditorAssetLibrary.load_asset(target_full)
                if existing and isinstance(existing, unreal.StaticMesh):
                    unreal.log(f'Using existing StaticMesh in target folder: {target_full}')
                    return existing

            dup = None
            try:
                dup = asset_tools.duplicate_asset(src_name, dest_folder, src_static)
            except Exception:
                dup = None
            if not dup:
                try:
                    dst_path = target_full
                    dup = unreal.EditorAssetLibrary.duplicate_asset(src_static.get_path_name(), dst_path)
                except Exception:
                    dup = None
            if not dup:
                unreal.log_warning(f'Failed to duplicate static mesh "{src_static.get_path_name()}" into {dest_folder}.')
                return None
            try:
                unreal.EditorAssetLibrary.save_loaded_asset(dup)
            except Exception:
                pass
            unreal.log(f'Duplicated StaticMesh "{src_name}" -> {target_full}')
            return dup
        except Exception as e:
            unreal.log_warning(f'_duplicate_static_mesh_into_folder: unexpected exception: {e}')
            return None

    @staticmethod
    def _ensure_root_static_mesh_for_skeletal(skeletal_mesh_obj: unreal.SkeletalMesh,
                                              version_folder: str,
                                              created_static_obj: unreal.StaticMesh,
                                              asset_tools,
                                              root_static_cache: dict):
        """
        Ensure a single root static mesh exists in version_folder for the given skeletal_mesh_obj.
        Returns the root static mesh (existing or created) or None.
        - root_static_cache: dict mapping skeletal_mesh_path -> root_static_obj to avoid repeated lookups.
        - created_static_obj: the latest static produced by conversion (may be already in folder)
        """
        try:
            if not skeletal_mesh_obj or not version_folder:
                return None

            key = None
            try:
                key = skeletal_mesh_obj.get_path_name()
            except Exception:
                key = getattr(skeletal_mesh_obj, 'get_name', lambda: None)()

            if key and key in root_static_cache:
                return root_static_cache[key]

            # Determine desired root name: use created_static_obj name if provided, else derive from skeletal mesh name
            root_name = None
            if created_static_obj:
                try:
                    root_name = created_static_obj.get_name()
                except Exception:
                    root_name = None
            if not root_name:
                try:
                    root_name = f"SM_{skeletal_mesh_obj.get_name()}"
                except Exception:
                    root_name = "SM_Root"

            # target path in version folder
            target_pkg = version_folder.rstrip('/') + '/' + root_name

            # If asset exists at target, load and accept as root
            if unreal.EditorAssetLibrary.does_asset_exist(target_pkg):
                candidate = unreal.EditorAssetLibrary.load_asset(target_pkg)
                if candidate and isinstance(candidate, unreal.StaticMesh):
                    root_static_cache[key] = candidate
                    unreal.log(f'Using existing root StaticMesh at {target_pkg}')
                    return candidate

            # If created_static_obj already matches desired_pkg, use it (rename if necessary)
            if created_static_obj:
                try:
                    created_path = created_static_obj.get_path_name()
                    created_pkg = created_path.split('.')[0]
                except Exception:
                    created_pkg = None

                # If created asset is not in version folder (or wrong name), try to move/rename it into version_folder with root_name
                if created_pkg:
                    desired_pkg = target_pkg
                    if created_pkg != desired_pkg:
                        # If desired exists, keep desired; otherwise try to rename created into desired
                        if not unreal.EditorAssetLibrary.does_asset_exist(desired_pkg):
                            try:
                                ok = unreal.EditorAssetLibrary.rename_asset(created_pkg, desired_pkg)
                                if ok:
                                    created_static_obj = unreal.EditorAssetLibrary.load_asset(desired_pkg)
                                    unreal.log(f'Renamed converted static mesh to root name: {created_pkg} -> {desired_pkg}')
                                else:
                                    unreal.log_warning(f'Could not rename {created_pkg} -> {desired_pkg}')
                            except Exception:
                                pass

                # If still not at desired pkg, duplicate to target folder and keep created one in place
                if not unreal.EditorAssetLibrary.does_asset_exist(target_pkg):
                    dup = UppCrowdsConvertionBlueprintLibrary._duplicate_static_mesh_into_folder(created_static_obj, version_folder, asset_tools)
                    if dup:
                        root_static_cache[key] = dup
                        return dup

            # As last resort, try to find any static mesh with same base name in version folder
            assets = unreal.EditorAssetLibrary.list_assets(version_folder, recursive=False)
            for a in assets:
                name = a.split('/')[-1]
                try:
                    candidate = unreal.EditorAssetLibrary.load_asset(a)
                    if isinstance(candidate, unreal.StaticMesh):
                        root_static_cache[key] = candidate
                        return candidate
                except Exception:
                    pass

            unreal.log_warning(f'Could not ensure root static mesh for skeletal {getattr(skeletal_mesh_obj, "get_name", lambda: "(unknown)")()} in {version_folder}')
            return None
        except Exception as e:
            unreal.log_warning(f'_ensure_root_static_mesh_for_skeletal: unexpected exception: {e}')
            return None

    @staticmethod
    def _next_version_folder(parent_folder):
        """Find next v### folder name inside parent_folder by scanning subfolders."""
        try:
            registry = unreal.AssetRegistryHelpers.get_asset_registry()
            subs = registry.get_sub_paths(parent_folder, False) or []
            max_v = 0
            for p in subs:
                tail = p.split('/')[-1]
                m = re.match(r'^v0*([0-9]+)$', tail, re.IGNORECASE)
                if m:
                    try:
                        val = int(m.group(1))
                        if val > max_v:
                            max_v = val
                    except:
                        pass
            next_idx = max_v + 1
            return f"v{next_idx:03d}"
        except Exception as e:
            unreal.log_warning(f'Failed to compute next version folder in "{parent_folder}": {e}')
            return "v001"

    @staticmethod
    def _resolve_mi_parent_if_mid(src_mat):
        """
        Robust resolution of MI_ parent for a src_mat that is usually a MaterialInstance.
        - Uses src_mat.get_editor_property('parent') (or .parent) and walks the parent chain.
        - Handles parent as object or as a path string.
        - Falls back to same-folder sibling lookup (fixes MID_MI_* -> MI_* logic).
        Returns a parent object (Material or MaterialInstance) whose name starts with "MI_", or None.
        """
        try:
            if not src_mat:
                return None

            # get name safely
            try:
                src_name = src_mat.get_name()
            except Exception:
                src_name = None

            if not src_name or not isinstance(src_name, str):
                return None

            # only handle MID_ prefixed names here
            if not src_name.startswith("MID_"):
                return None

            # Helper: attempt to normalize/load a parent reference that may be:
            #  - an object reference (has get_name)
            #  - a soft/path string (loadable by EditorAssetLibrary.load_asset)
            def _load_parent_object(parent_ref):
                if not parent_ref:
                    return None
                # already an object
                if hasattr(parent_ref, "get_name"):
                    return parent_ref
                # string path -> load
                if isinstance(parent_ref, str):
                    try:
                        loaded = unreal.EditorAssetLibrary.load_asset(parent_ref)
                        return loaded
                    except Exception:
                        return None
                return None

            # Walk parent chain via editor property 'parent' (or attribute .parent if available)
            try:
                parent_ref = None
                # prefer get_editor_property to be robust across API variations
                try:
                    parent_ref = src_mat.get_editor_property('parent')
                except Exception:
                    # try direct attribute access as fallback
                    try:
                        parent_ref = getattr(src_mat, 'parent', None)
                    except Exception:
                        parent_ref = None

                # attempt to resolve/load to an object
                parent_obj = _load_parent_object(parent_ref)
                depth = 0
                max_depth = 8
                while parent_obj and depth < max_depth:
                    # get candidate name
                    try:
                        pname = parent_obj.get_name()
                    except Exception:
                        pname = None

                    if pname and isinstance(pname, str) and pname.startswith("MI_"):
                        unreal.log(f'Found MI_ parent via parent-chain for {src_name} -> {pname}')
                        return parent_obj

                    # otherwise step up: parent_obj may itself be an instance that has a 'parent'
                    try:
                        next_parent_ref = None
                        try:
                            next_parent_ref = parent_obj.get_editor_property('parent')
                        except Exception:
                            try:
                                next_parent_ref = getattr(parent_obj, 'parent', None)
                            except Exception:
                                next_parent_ref = None

                        if not next_parent_ref:
                            break

                        parent_obj = _load_parent_object(next_parent_ref)
                        depth += 1
                    except Exception:
                        break
            except Exception:
                # swallow and continue to sibling lookup fallback
                pass

            # Fallback: same-folder candidate lookup (handle MID_MI_... properly)
            try:
                path = None
                try:
                    path = src_mat.get_path_name()
                except Exception:
                    path = None

                if path:
                    pkg = path.split('.')[0]  # strip possible trailing .Name
                    if '/' in pkg:
                        folder = pkg.rsplit('/', 1)[0]
                        remainder = src_name[4:]  # strip leading 'MID_'
                        # if remainder already starts with 'MI_' do not add another MI_
                        if remainder.startswith('MI_'):
                            candidate_name = remainder
                        else:
                            candidate_name = 'MI_' + remainder
                        candidate_pkg = folder + '/' + candidate_name
                        try:
                            candidate_obj = unreal.EditorAssetLibrary.load_asset(candidate_pkg)
                            if candidate_obj and hasattr(candidate_obj, 'get_name'):
                                try:
                                    if candidate_obj.get_name().startswith('MI_'):
                                        unreal.log(f'Found MI_ candidate by same-folder lookup for {src_name} -> {candidate_obj.get_name()} at {candidate_pkg}')
                                        return candidate_obj
                                except Exception:
                                    pass
                        except Exception:
                            pass
            except Exception:
                pass

            unreal.log(f'No MI_ parent found for MID_ material {src_name}; will fall back to duplicating the MID_ itself.')
            return None

        except Exception:
            return None

    @staticmethod
    def _get_component_materials(comp, skeletal_mesh):
        """Return list of materials for a component respecting overrides; fallback to skeletal mesh slots."""
        try:
            num_slots = 0
            try:
                num_slots = comp.get_num_materials()
            except Exception:
                # fallback to number of skeletal mesh slots
                try:
                    ske_mats = skeletal_mesh.get_editor_property("materials") or []
                    num_slots = len(ske_mats)
                except Exception:
                    num_slots = 0

            comp_materials = []
            for mat_idx in range(num_slots):
                try:
                    mat_on_comp = comp.get_material(mat_idx)  # returns override or default
                    comp_materials.append(mat_on_comp)
                except Exception:
                    comp_materials.append(None)
            return comp_materials
        except Exception:
            return []

    @staticmethod
    def _duplicate_material_into_folder(src_mat, version_folder, asset_tools):
        """
        Duplicate single material (src_mat) into version_folder.
        If an asset with the same name already exists in version_folder, return that existing asset
        instead of creating a duplicate (prevents MI_X_dupN proliferation).
        Returns duplicated (or existing) object, or None on failure.
        """
        try:
            src_path = src_mat.get_path_name()
            src_name = src_mat.get_name()
        except Exception:
            return None

        # Desired target full package path
        target_name = src_name
        target_full = version_folder.rstrip('/') + '/' + target_name

        # If the exact target already exists in the version folder, reuse it (no _dupN naming).
        try:
            if unreal.EditorAssetLibrary.does_asset_exist(target_full):
                existing = unreal.EditorAssetLibrary.load_asset(target_full)
                if existing:
                    unreal.log(f'Using existing material in target folder: {target_full} (no duplicate created).')
                    return existing
                # If path exists but load failed, continue to attempt duplication below.
        except Exception:
            # fallthrough to duplication attempt
            pass

        dup_obj = None
        # Try AssetTools.duplicate_asset (preferred)
        try:
            dup_obj = asset_tools.duplicate_asset(target_name, version_folder, src_mat)
        except Exception:
            dup_obj = None

        # Fallback to EditorAssetLibrary.duplicate_asset using full paths
        if not dup_obj:
            try:
                dst_path = version_folder.rstrip('/') + '/' + target_name
                dup_obj = unreal.EditorAssetLibrary.duplicate_asset(src_path, dst_path)
            except Exception:
                dup_obj = None

        if not dup_obj:
            unreal.log_warning(f'Failed to duplicate material "{src_path}" into {version_folder}.')
            return None

        # Save duplicated material
        try:
            unreal.EditorAssetLibrary.save_loaded_asset(dup_obj)
        except Exception:
            pass

        return dup_obj

    @staticmethod
    def _set_mic_max_wpo_displacement(mat_instance, displacement_value):
        """
        Ensure MaxWorldPositionOffsetDisplacement is applied and the material instance is updated/compiled.
        If mat_instance is a base Material, create a MaterialInstanceConstant in the same folder and use that.
        Returns the material instance object that was changed (or None on failure).
        """
        try:
            if not mat_instance:
                unreal.log_error("No material provided to _set_mic_max_wpo_displacement.")
                return None

            # If we got a base Material (not an instance), create a MI constant with that parent in the same folder
            if isinstance(mat_instance, unreal.Material):
                try:
                    parent_path = mat_instance.get_path_name().split('.')[0]
                    folder = parent_path.rsplit('/', 1)[0]
                    base_name = mat_instance.get_name()
                    mi_name = base_name + "_MIC"
                    target_pkg = folder + '/' + mi_name

                    # If an MI already exists at that path, reuse it
                    if unreal.EditorAssetLibrary.does_asset_exist(target_pkg):
                        created_mi = unreal.EditorAssetLibrary.load_asset(target_pkg)
                    else:
                        factory = unreal.MaterialInstanceConstantFactoryNew()
                        created_mi = unreal.AssetToolsHelpers.get_asset_tools().create_asset(mi_name, folder, unreal.MaterialInstanceConstant, factory)
                        if created_mi:
                            created_mi.set_editor_property('parent', mat_instance)
                            unreal.EditorAssetLibrary.save_loaded_asset(created_mi)
                            unreal.log(f'Created MaterialInstanceConstant {target_pkg} from base material {mat_instance.get_name()}')
                        else:
                            unreal.log_warning(f'Failed to create MI for material {mat_instance.get_name()}')
                            return None

                    mat_instance = created_mi
                except Exception as e:
                    unreal.log_warning(f'_set_mic_max_wpo_displacement: failed to create MI from Material: {e}')
                    return None

            # Now we should have a MaterialInstanceConstant (or compatible)
            if not isinstance(mat_instance, (unreal.MaterialInstanceConstant, unreal.MaterialInstance)):
                unreal.log_warning(f'_set_mic_max_wpo_displacement: object is not a MaterialInstance: {getattr(mat_instance, "get_name", lambda: "(unknown)")()}')
                return None

            # Get or create the base_property_overrides struct
            try:
                overrides = mat_instance.get_editor_property('base_property_overrides')
            except Exception:
                overrides = None

            if overrides is None:
                try:
                    overrides = unreal.MaterialInstanceBasePropertyOverrides()
                except Exception:
                    overrides = None

            if overrides is None:
                unreal.log_warning('_set_mic_max_wpo_displacement: Could not create base_property_overrides struct.')
            else:
                try:
                    overrides.set_editor_property('override_max_world_position_offset_displacement', True)
                    overrides.set_editor_property('max_world_position_offset_displacement', float(displacement_value))
                    mat_instance.set_editor_property('base_property_overrides', overrides)
                except Exception as e:
                    unreal.log_warning(f'_set_mic_max_wpo_displacement: failed to set overrides: {e}')

            # Force an update/compile of the instance so the editor uses the new displacement immediately.
            try:
                # Update/compile the instance via MaterialEditingLibrary (if available)
                try:
                    unreal.MaterialEditingLibrary.update_material_instance(mat_instance)
                except Exception:
                    pass
                # Some builds respond better to post_edit_change/mark_package_dirty
                try:
                    mat_instance.post_edit_change()
                except Exception:
                    pass

                try:
                    unreal.EditorAssetLibrary.save_loaded_asset(mat_instance)
                except Exception:
                    pass

                unreal.log(f"Set MaxWorldPositionOffsetDisplacement={displacement_value} on {mat_instance.get_path_name()}")
                return mat_instance
            except Exception as e:
                unreal.log_warning(f'_set_mic_max_wpo_displacement: exception during update/save: {e}')
                return mat_instance

        except Exception as e:
            unreal.log_warning(f'_set_mic_max_wpo_displacement: unexpected exception: {e}')
            return None

    @staticmethod
    def _enable_vats_textures_param(dup_obj):
        """
        Enable the single parameter 'VATs textures' on dup_obj.
        - Prefers static switch if present, otherwise tries scalar parameter.
        - Works for MaterialInstanceConstant (instance) and base Material objects.
        - Updates the material instance and saves the asset.
        Returns True if the parameter was found and set; False otherwise.
        """
        try:
            if not dup_obj:
                return False

            target_name = "VATs textures"
            target_lc = target_name.lower()

            # --- Try static switch parameter first ---
            try:
                switch_names = unreal.MaterialEditingLibrary.get_static_switch_parameter_names(dup_obj) or []
            except Exception:
                switch_names = []

            for sname in switch_names:
                try:
                    if str(sname).lower() == target_lc:
                        try:
                            if isinstance(dup_obj, unreal.MaterialInstanceConstant):
                                # set instance static switch, update (last arg update handled by update_material_instance below)
                                unreal.MaterialEditingLibrary.set_material_instance_static_switch_parameter_value(dup_obj, sname, True)
                                unreal.log(f'Set static switch "{sname}" = True on duplicated material instance {dup_obj.get_name()}')
                            else:
                                # base material default static switch
                                unreal.MaterialEditingLibrary.set_material_default_static_switch_parameter_value(dup_obj, sname, True)
                                unreal.log(f'Set default static switch "{sname}" = True on duplicated material {dup_obj.get_name()}')
                        except Exception as e:
                            unreal.log_warning(f'Failed to set static switch "{sname}" on {getattr(dup_obj, "get_name", lambda: "(unknown)")()}: {e}')
                        # ensure update + save
                        try:
                            unreal.MaterialEditingLibrary.update_material_instance(dup_obj)
                        except Exception:
                            pass
                        try:
                            unreal.EditorAssetLibrary.save_loaded_asset(dup_obj)
                        except Exception:
                            pass
                        return True
                except Exception:
                    continue
        except Exception as e:
            unreal.log(e)

    @staticmethod
    def _assign_materials_to_static_mesh(static_mesh_obj, duplicated_materials):
        """Assign duplicated materials onto the static mesh slots using the original logic/fallbacks."""
        try:
            # prefer set_material(index, material) method
            for mi_idx, dup_mat in enumerate(duplicated_materials):
                if not dup_mat:
                    continue
                try:
                    static_mesh_obj.set_material(mi_idx, dup_mat)
                except Exception:
                    # fallback: set_editor_property on 'static_materials' list
                    try:
                        current_slots = static_mesh_obj.get_editor_property('static_materials') or []
                        sm = unreal.StaticMaterial()
                        sm.material_interface = dup_mat
                        if mi_idx < len(current_slots):
                            current_slots[mi_idx] = sm
                        else:
                            current_slots.append(sm)
                        static_mesh_obj.set_editor_property('static_materials', current_slots)
                    except Exception:
                        pass
            # Save after assignment
            try:
                unreal.EditorAssetLibrary.save_loaded_asset(static_mesh_obj)
            except Exception:
                pass
        except Exception as assign_e:
            unreal.log_warning(f'Failed to assign duplicated materials to StaticMesh "{getattr(static_mesh_obj, "get_name", lambda: "(unknown)")()}": {assign_e}')

    @staticmethod
    def _enable_nanite(static_mesh_obj, static_mesh_editor_subsystem):
        """Enable Nanite and set fallback_relative_error to 0 on the given static mesh (retaining original logic)."""
        try:
            static_mesh_obj.set_editor_property('bEvaluateWorldPositionOffsetInRayTracing', True)
            nanite_settings: unreal.NaniteSettings = static_mesh_editor_subsystem.get_nanite_settings(static_mesh_obj)
            if nanite_settings:
                nanite_settings.enabled = True
                nanite_settings.lerp_u_vs = False
                try:
                    # setattr(nanite_settings, 'bLerpUVs', False)
                    setattr(nanite_settings, 'fallback_relative_error', 0.0)
                except Exception:
                    pass
                static_mesh_editor_subsystem.set_nanite_settings(static_mesh_obj, nanite_settings, True)
                unreal.log(f'Enabled Nanite on {static_mesh_obj.get_name()} and set fallback_relative_error=0')
                try:
                    unreal.EditorAssetLibrary.save_loaded_asset(static_mesh_obj)
                except Exception:
                    pass
            else:
                unreal.log_warning(f'Could not get nanite settings for {static_mesh_obj.get_name()}')
        except Exception as n_e:
            unreal.log_warning(f'Exception while enabling Nanite on "{static_mesh_obj.get_name()}": {n_e}\n{traceback.format_exc()}')

    @staticmethod
    def _get_or_create_anim_to_texture_data_asset(version_folder, static_mesh_obj, asset_tools, asset_name=None):
        """
        Return an existing AnimToTextureDataAsset at version_folder/<AT_name> or create one.
        Uses AT_ prefix for the asset name (will add if missing).
        - version_folder: package folder, e.g. '/Game/whatever/v001'
        - static_mesh_obj: used to derive default name if asset_name is None
        - asset_tools: unreal.AssetToolsHelpers.get_asset_tools()
        Returns: AnimToTextureDataAsset object or None.
        """
        try:
            if not version_folder or not asset_tools or not static_mesh_obj:
                unreal.log_warning('_get_or_create_anim_to_texture_data_asset: missing required args')
                return None

            try:
                # Determine base name and ensure AT_ prefix
                raw_name = asset_name if asset_name else static_mesh_obj.get_name()
                if not raw_name:
                    raw_name = "DataAsset"
                if not str(raw_name).startswith("AT_"):
                    base_name = "AT_" + str(raw_name)
                else:
                    base_name = str(raw_name)
            except Exception:
                base_name = "AT_DataAsset"

            target_pkg = version_folder.rstrip('/') + '/' + base_name

            # If asset exists, return loaded asset
            try:
                if unreal.EditorAssetLibrary.does_asset_exist(target_pkg):
                    asset = unreal.EditorAssetLibrary.load_asset(target_pkg)
                    if asset:
                        unreal.log(f'Using existing AnimToTextureDataAsset: {target_pkg}')
                        return asset
            except Exception as e:
                unreal.log_warning(f'Could not check/load existing data asset at {target_pkg}: {e}')

            # Create new DataAsset using DataAssetFactory
            try:
                factory = unreal.DataAssetFactory()
                data_asset = asset_tools.create_asset(base_name, version_folder, unreal.AnimToTextureDataAsset, factory)
                if not data_asset:
                    unreal.log_warning(f'Failed to create AnimToTextureDataAsset at {target_pkg}')
                    return None
                unreal.log(f'Created AnimToTextureDataAsset: {target_pkg}')
                # Save after creation
                try:
                    unreal.EditorAssetLibrary.save_loaded_asset(data_asset)
                except Exception:
                    pass
                return data_asset
            except Exception as e:
                unreal.log_warning(f'Exception while creating AnimToTextureDataAsset "{target_pkg}": {e}')
                return None

        except Exception as e:
            unreal.log_warning(f'_get_or_create_anim_to_texture_data_asset: unexpected exception: {e}')
            return None

    @staticmethod
    def _make_anim_sequence_structs(anim_sequences):
        """
        Build a list of AnimToTextureAnimSequenceInfo (or compatible) struct instances
        from an iterable of animation assets.

        Returns a list (possibly empty) of struct objects that can be assigned to the data-asset.
        This function attempts multiple likely struct types for compatibility across engine/plugin versions.
        """
        seq_structs = []
        try:
            if not anim_sequences:
                return seq_structs

            for anim in anim_sequences:
                try:
                    if not anim:
                        continue

                    info_obj = None

                    # Preferred struct name used by the plugin
                    try:
                        info_obj = unreal.AnimToTextureAnimSequenceInfo(enabled=True, anim_sequence=anim)
                    except Exception:
                        info_obj = None

                    # Fallback: common alternate struct name
                    if info_obj is None:
                        try:
                            info_obj = unreal.AnimSequenceInfo(enabled=True, anim_sequence=anim)
                        except Exception:
                            info_obj = None

                    # If we still have no struct, attempt a final best-effort: try to construct
                    # an empty generic struct and set attributes if that is supported.
                    if info_obj is None:
                        try:
                            # Some builds expose a struct factory via unreal.StructName(...) — try a generic approach
                            # NOTE: This will usually fail unless the exact struct is exposed to Python.
                            generic = type("TempAnimInfo", (), {})()
                            # Set attributes that other code expects
                            setattr(generic, "enabled", True)
                            try:
                                setattr(generic, "anim_sequence", anim)
                            except Exception:
                                pass
                            info_obj = generic
                        except Exception:
                            info_obj = None

                    if info_obj:
                        seq_structs.append(info_obj)

                except Exception as inner_e:
                    unreal.log_warning(f'_make_anim_sequence_structs: failed to build struct for anim {getattr(anim, "get_name", lambda: str(anim))()}: {inner_e}')
                    continue

        except Exception as e:
            unreal.log_warning(f'_make_anim_sequence_structs: unexpected error: {e}')
        return seq_structs

    # --- Updated portion of _populate_anim_to_texture_data_asset ---
    # Replace the original inline "Build Array[AnimToTextureAnimSequenceInfo] ..." block with this:

    @staticmethod
    def _populate_anim_to_texture_data_asset(data_asset, static_mesh_obj, skeletal_mesh_obj=None,
                                             anim_sequences=None,
                                             mode=unreal.AnimToTextureMode.BONE,
                                             uv_channel=1,
                                             sample_rate=30.0,
                                             num_bone_influences=None):
        """
        Populate properties on an AnimToTextureDataAsset (best-effort).
        - data_asset: AnimToTextureDataAsset returned by _get_or_create... (required)
        - static_mesh_obj: UStaticMesh used for the DA.StaticMesh property
        - skeletal_mesh_obj: optional USkeletalMesh used for DA.SkeletalMesh property
        - anim_sequences: optional iterable of AnimSequence assets to add (will be converted to
                          an Array[AnimToTextureAnimSequenceInfo])
        - mode, uv_channel, sample_rate: basic settings
        - num_bone_influences: pass engine enum if desired (optional)
        Returns: True on success, False otherwise.
        """
        try:
            if not data_asset or not static_mesh_obj:
                unreal.log_warning('_populate_anim_to_texture_data_asset: missing required args')
                return False

            # Basic references
            try:
                data_asset.set_editor_property('StaticMesh', static_mesh_obj)
            except Exception as e:
                unreal.log_warning(f'Failed to set StaticMesh on data asset {getattr(data_asset, "get_name", lambda: "(unknown)")()}: {e}')

            if skeletal_mesh_obj:
                try:
                    data_asset.set_editor_property('SkeletalMesh', skeletal_mesh_obj)
                except Exception as e:
                    unreal.log_warning(f'Failed to set SkeletalMesh on data asset {getattr(data_asset, "get_name", lambda: "(unknown)")()}: {e}')

            # Mode / UV / sample rate
            try:
                data_asset.set_editor_property('Mode', mode)
            except Exception:
                try:
                    setattr(data_asset, 'Mode', mode)
                except Exception:
                    pass
            try:
                data_asset.set_editor_property('UVChannel', int(uv_channel))
            except Exception:
                pass
            try:
                data_asset.set_editor_property('SampleRate', float(sample_rate))
            except Exception:
                pass
            if num_bone_influences is not None:
                try:
                    data_asset.set_editor_property('NumBoneInfluences', num_bone_influences)
                except Exception:
                    pass
            data_asset.set_editor_property('bAutoPlay', False)

            # unreal.log(f"[CROWD CONVERSION]: bits {data_asset.get_editor_property('Precision')}")
            data_asset.set_editor_property('Precision', unreal.AnimToTexturePrecision.SIXTEEN_BITS)
            unreal.log(f"[CROWD CONVERSION]: bits {data_asset.get_editor_property('Precision')}")

            # Build Array[AnimToTextureAnimSequenceInfo] using helper and assign directly to data_asset.anim_sequences
            seq_structs = UppCrowdsConvertionBlueprintLibrary._make_anim_sequence_structs(anim_sequences)

            # Assign the array directly if possible; fall back to set_editor_property with common name variations
            try:
                # direct attribute assignment (preferred)
                try:
                    setattr(data_asset, 'anim_sequences', seq_structs)
                except Exception:
                    # Some builds expose the property with different casing; try common alternatives
                    try:
                        setattr(data_asset, 'AnimSequences', seq_structs)
                    except Exception:
                        # final fallback to set_editor_property
                        data_asset.set_editor_property('AnimSequences', seq_structs)
            except Exception as e:
                # Last resort: try set_editor_property with lowercase name
                try:
                    data_asset.set_editor_property('anim_sequences', seq_structs)
                except Exception:
                    unreal.log_warning(f'Failed to assign AnimSequences array on DA {getattr(data_asset, "get_name", lambda: "?")()}: {e}')

            # Save the data asset
            try:
                unreal.EditorAssetLibrary.save_loaded_asset(data_asset)
            except Exception:
                pass

            return True

        except Exception as e:
            unreal.log_warning(f'_populate_anim_to_texture_data_asset: unexpected exception: {e}')
            return False

    @staticmethod
    def _update_material_instances_from_data_asset(data_asset, static_mesh_obj, anim_to_texture_lib):
        """
        Update MaterialInstanceConstant objects on static_mesh_obj from the provided data_asset
        using AnimToTexture library helpers (best-effort).
        - data_asset: AnimToTextureDataAsset
        - static_mesh_obj: UStaticMesh to inspect for static_materials
        - anim_to_texture_lib: unreal.AnimToTextureBPLibrary (must be provided)
        Returns: number of material instances updated.
        """
        try:
            if not data_asset or not static_mesh_obj or not anim_to_texture_lib:
                unreal.log_warning('_update_material_instances_from_data_asset: missing required args')
                return 0

            updated_count = 0
            # static_materials is array of StaticMaterial structs containing material_interface
            try:
                slots = static_mesh_obj.get_editor_property('static_materials') or []
            except Exception:
                slots = []

            for sm in slots:
                try:
                    mat_ifc = getattr(sm, 'material_interface', None)
                    if not mat_ifc:
                        continue
                    # Only update instance assets (MaterialInstanceConstant)
                    if isinstance(mat_ifc, unreal.MaterialInstanceConstant):
                        try:
                            # Some versions of the plugin require an enum association – use LAYER_PARAMETER as in previous versions
                            association = unreal.MaterialParameterAssociation.LAYER_PARAMETER if hasattr(unreal, 'MaterialParameterAssociation') else None
                            if association:
                                anim_to_texture_lib.update_material_instance_from_data_asset(data_asset, mat_ifc, association)
                            else:
                                # plugin may provide an overload without association parameter
                                try:
                                    anim_to_texture_lib.update_material_instance_from_data_asset(data_asset, mat_ifc)
                                except Exception:
                                    # last resort: call with explicit None
                                    anim_to_texture_lib.update_material_instance_from_data_asset(data_asset, mat_ifc, None)
                            unreal.log(f'Updated material instance "{mat_ifc.get_name()}" from data asset "{getattr(data_asset, "get_name", lambda: "(unknown)")()}"')
                            try:
                                unreal.EditorAssetLibrary.save_loaded_asset(mat_ifc)
                            except Exception:
                                pass
                            updated_count += 1
                        except Exception as e:
                            unreal.log_warning(f'Failed to update material instance "{getattr(mat_ifc, "get_name", lambda: "?")()}": {e}')
                except Exception:
                    continue

            return updated_count

        except Exception as e:
            unreal.log_warning(f'_update_material_instances_from_data_asset: unexpected exception: {e}')
            return 0

    @staticmethod
    def _create_anim_to_texture_job(static_mesh_obj, skeletal_mesh_obj, version_folder, asset_tools, anim_to_texture_lib,
                                    asset_name=None, anim_sequences=None,
                                    mode=unreal.AnimToTextureMode.BONE, uv_channel=0, sample_rate=30.0, num_bone_influences=None):
        """
        High-level helper: create (or reuse) a DataAsset for AnimToTexture, populate it and update materials.
        Returns the data asset on success or None.
        """
        try:
            # create or get data asset
            data_asset = UppCrowdsConvertionBlueprintLibrary._get_or_create_anim_to_texture_data_asset(
                version_folder, static_mesh_obj, asset_tools, asset_name=asset_name
            )
            if not data_asset:
                unreal.log_warning('_create_anim_to_texture_job: failed to obtain data asset')
                return None

            # populate properties
            ok = UppCrowdsConvertionBlueprintLibrary._populate_anim_to_texture_data_asset(
                data_asset,
                static_mesh_obj,
                skeletal_mesh_obj,
                anim_sequences=anim_sequences,
                mode=mode,
                uv_channel=uv_channel,
                sample_rate=sample_rate,
                num_bone_influences=num_bone_influences
            )
            if not ok:
                unreal.log_warning(f'_create_anim_to_texture_job: failed to populate data asset {getattr(data_asset, "get_name", lambda: "?")()}')

            # update material instances on the static mesh from the DA
            updated = 0
            try:
                if anim_to_texture_lib:
                    updated = UppCrowdsConvertionBlueprintLibrary._update_material_instances_from_data_asset(
                        data_asset, static_mesh_obj, anim_to_texture_lib
                    )
            except Exception:
                updated = 0

            unreal.log(f'AnimToTexture job ready: data asset={getattr(data_asset, "get_name", lambda: "?")()}, material_instances_updated={updated}')
            return data_asset

        except Exception as e:
            unreal.log_warning(f'_create_anim_to_texture_job: unexpected exception: {e}')
            return None

    @staticmethod
    def _create_render_target_asset(version_folder: str, name: str, asset_tools,
                                    width: int = 1024, height: int = 1024,
                                    auto_generate_mips: bool = False):
        """
        Create (or reuse) a TextureRenderTarget2D asset named <name> in version_folder.
        Returns the TextureRenderTarget2D object or None.
        """
        try:
            if not version_folder or not name or not asset_tools:
                unreal.log_warning('_create_render_target_asset: missing required args')
                return None

            target_pkg = version_folder.rstrip('/') + '/' + name
            try:
                if unreal.EditorAssetLibrary.does_asset_exist(target_pkg):
                    rt = unreal.EditorAssetLibrary.load_asset(target_pkg)
                    if rt:
                        unreal.log(f'Using existing render target asset: {target_pkg}')
                        return rt
            except Exception:
                pass

            try:
                factory = unreal.TextureRenderTargetFactoryNew()
                rt_obj: unreal.TextureRenderTarget2D = asset_tools.create_asset(name, version_folder, unreal.TextureRenderTarget2D, factory)

                if not rt_obj:
                    unreal.log_warning(f'Failed to create TextureRenderTarget2D asset at {target_pkg}')
                    return None
                # ensure size (best-effort); RenderingLibrary.resize_render_target2d is available
                try:
                    unreal.RenderingLibrary.resize_render_target2d(rt_obj, width, height)
                except Exception:
                    # fallback: set editor properties (some engine builds allow this)
                    try:
                        rt_obj.set_editor_property('size_x', int(width))
                        rt_obj.set_editor_property('size_y', int(height))
                    except Exception:
                        pass
                # set mip generation flag if available
                try:
                    rt_obj.set_editor_property('b_auto_generate_mips', bool(auto_generate_mips))
                except Exception:
                    # property name may differ, ignore if not present
                    pass

                try:
                    unreal.EditorAssetLibrary.save_loaded_asset(rt_obj)
                except Exception:
                    pass

                unreal.log(f'Created render target asset: {target_pkg}')
                return rt_obj
            except Exception as e:
                unreal.log_warning(f'_create_render_target_asset: exception creating {target_pkg}: {e}')
                return None

        except Exception as e:
            unreal.log_warning(f'_create_render_target_asset: unexpected exception: {e}')
            return None

    @staticmethod
    def _create_static_texture_from_render_target(render_target_obj, texture_name: str,
                                                  compression_setting=None, mip_setting=None, filter=unreal.TextureFilter.TF_NEAREST):
        """
        Convert a TextureRenderTarget2D into a Texture2D asset (editor-only).
        Uses RenderingLibrary.render_target_create_static_texture2d_editor_only.
        - render_target_obj: UTextureRenderTarget2D (required)
        - texture_name: desired name for the resulting Texture2D (string)
        - compression_setting / mip_setting: optional enums (fallbacks used if None)
        - world_context: optional WorldContextObject required by some RenderingLibrary calls; pass None to let the API resolve
        Returns created Texture2D object or None.
        """
        try:
            if not render_target_obj or not texture_name:
                unreal.log_warning('_create_static_texture_from_render_target: missing required args')
                return None

            # choose defaults
            try:
                comp = compression_setting or unreal.TextureCompressionSettings.TC_DEFAULT
            except Exception:
                comp = None
            try:
                mip = mip_setting or unreal.TextureMipGenSettings.TMGS_FROM_TEXTURE_GROUP
            except Exception:
                mip = None

            # call the editor-only helper (RenderingLibrary)
            try:
                tx: unreal.Texture2D = unreal.RenderingLibrary.render_target_create_static_texture2d_editor_only(
                    render_target_obj,
                    texture_name,
                    comp,
                    mip
                )

                if tx:
                    unreal.EditorAssetLibrary.save_loaded_asset(tx, False)
                    unreal.log(f'Created static Texture2D "{texture_name}" from render target "{getattr(render_target_obj, "get_name", lambda: "(rt)")()}"')
                    return tx
                else:
                    unreal.log_warning(f'render_target_create_static_texture2d_editor_only returned None for {texture_name}')
                    return None
            except Exception as e:
                unreal.log_warning(f'_create_static_texture_from_render_target: exception: {e}')
                return None

        except Exception as e:
            unreal.log_warning(f'_create_static_texture_from_render_target: unexpected exception: {e}')
            return None

    @staticmethod
    def _create_and_assign_bone_textures_for_data_asset(data_asset, version_folder, base_name, asset_tools,
                                                        rt_width=1024, rt_height=1024,
                                                        compression_setting=None, mip_setting=None):
        """
        High-level: create RenderTarget assets, create static Texture2D from them,
        assign to data_asset.bone_position_texture / bone_rotation_texture / bone_weight_texture,
        rename textures to TX_BonePosition_<base_name>, TX_BoneRotation_<base_name>, TX_BoneWeight_<base_name>.
        After successful creation and assignment of the TX_ textures, delete the temporary RT_ assets.
        Returns a dict with the created Texture2D objects or None on failure.
        """
        try:
            if not data_asset or not version_folder or not base_name or not asset_tools:
                unreal.log_warning('_create_and_assign_bone_textures_for_data_asset: missing required args')
                return None

            # prepare asset names
            pos_rt_name = f"RT_BonePosition_{base_name}"
            rot_rt_name = f"RT_BoneRotation_{base_name}"
            wgt_rt_name = f"RT_BoneWeight_{base_name}"

            pos_tx_name = f"TX_BonePosition_{base_name}"
            rot_tx_name = f"TX_BoneRotation_{base_name}"
            wgt_tx_name = f"TX_BoneWeight_{base_name}"

            # TODO: Skip if textures in that folder already exist - animation specific
            # create or reuse render targets
            pos_rt = UppCrowdsConvertionBlueprintLibrary._create_render_target_asset(version_folder, pos_rt_name, asset_tools, width=rt_width, height=rt_height)
            rot_rt = UppCrowdsConvertionBlueprintLibrary._create_render_target_asset(version_folder, rot_rt_name, asset_tools, width=rt_width, height=rt_height)
            wgt_rt = UppCrowdsConvertionBlueprintLibrary._create_render_target_asset(version_folder, wgt_rt_name, asset_tools, width=rt_width, height=rt_height)

            if not (pos_rt or rot_rt or wgt_rt):
                unreal.log_warning('_create_and_assign_bone_textures_for_data_asset: failed to create any render targets')
                return None

            # convert RT -> Texture2D (editor-only)
            pos_tx = None
            rot_tx = None
            wgt_tx = None
            try:
                if pos_rt:
                    pos_tx = UppCrowdsConvertionBlueprintLibrary._create_static_texture_from_render_target(pos_rt, pos_tx_name, compression_setting, mip_setting, unreal.TextureFilter.TF_DEFAULT)
                if rot_rt:
                    rot_tx = UppCrowdsConvertionBlueprintLibrary._create_static_texture_from_render_target(rot_rt, rot_tx_name, compression_setting, mip_setting, unreal.TextureFilter.TF_DEFAULT)
                if wgt_rt:
                    wgt_tx = UppCrowdsConvertionBlueprintLibrary._create_static_texture_from_render_target(wgt_rt, wgt_tx_name, compression_setting, mip_setting, unreal.TextureFilter.TF_NEAREST)
            except Exception:
                pass

            # TODO: Position and rotation have to be filter = default!

            # Assign to data asset properties (AnimToTextureDataAsset expects Texture2D on these properties)
            assigned = False
            try:
                if pos_tx:
                    data_asset.set_editor_property('bone_position_texture', pos_tx)
                    assigned = True
            except Exception:
                unreal.log_warning(f'Failed to assign bone_position_texture on DA {getattr(data_asset, "get_name", lambda: "?")()}')
            try:
                if rot_tx:
                    data_asset.set_editor_property('bone_rotation_texture', rot_tx)
                    assigned = True
            except Exception:
                unreal.log_warning(f'Failed to assign bone_rotation_texture on DA {getattr(data_asset, "get_name", lambda: "?")()}')
            try:
                if wgt_tx:
                    data_asset.set_editor_property('bone_weight_texture', wgt_tx)
                    assigned = True
            except Exception:
                unreal.log_warning(f'Failed to assign bone_weight_texture on DA {getattr(data_asset, "get_name", lambda: "?")()}')

            # Save the data asset if we assigned anything
            if assigned:
                try:
                    data_asset.set_editor_property('max_height', 8192)
                    data_asset.set_editor_property('max_width', 8192)
                    unreal.EditorAssetLibrary.save_loaded_asset(data_asset)
                except Exception:
                    pass

            # Attempt to delete the temporary render target assets (best-effort).
            # Use package path: version_folder + '/' + RT_name
            try:
                def _delete_asset_by_name(folder, name):
                    pkg = folder.rstrip('/') + '/' + name
                    try:
                        # if asset exists, try to delete it
                        if unreal.EditorAssetLibrary.does_asset_exist(pkg):
                            success = unreal.EditorAssetLibrary.delete_asset(pkg)
                            if success:
                                unreal.log(f'Deleted temporary render target asset: {pkg}')
                            else:
                                unreal.log_warning(f'Failed to delete temporary render target asset: {pkg}')
                    except Exception as de:
                        unreal.log_warning(f'Exception deleting asset {pkg}: {de}')

                # delete each RT if it exists
                if pos_rt:
                    _delete_asset_by_name(version_folder, pos_rt_name)
                if rot_rt:
                    _delete_asset_by_name(version_folder, rot_rt_name)
                if wgt_rt:
                    _delete_asset_by_name(version_folder, wgt_rt_name)
            except Exception as e:
                unreal.log_warning(f'Failed to delete temporary render targets: {e}')

            unreal.log(
                f'Bone textures processed for DA {getattr(data_asset, "get_name", lambda: "?")()}: pos={getattr(pos_tx, "get_name", lambda: "None")()}, rot={getattr(rot_tx, "get_name", lambda: "None")()}, wgt={getattr(wgt_tx, "get_name", lambda: "None")()}')
            return {
                'pos': pos_tx,
                'rot': rot_tx,
                'wgt': wgt_tx
            }

        except Exception as e:
            unreal.log_warning(f'_create_and_assign_bone_textures_for_data_asset: unexpected exception: {e}\n{traceback.format_exc()}')
            return None

    # ------------------------
    # Main exposed function (keeps the same overall control flow)
    # ------------------------

    @staticmethod
    def _get_selected_spawnable_bindings_from_current_sequence():
        """
        Return spawnable bindings owning the currently-selected Sequencer bindings.

        IMPORTANT:
        - Sequencer selection usually targets child possessables (components)
        - We must walk UP to the owning spawnable
        """
        # 2. Get the currently active level sequence in the editor
        active_sequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()

        if active_sequence:
            # 3. Get the GUIDs of the currently selected tracks/bindings
            selected_bindings = unreal.LevelSequenceEditorBlueprintLibrary.get_selected_bindings()

            spawnable_objects = []

            for binding_proxy in selected_bindings:
                # 4. Check if the binding is a spawnable
                # A binding is a 'spawnable' if it is not a 'possessable'
                if isinstance(binding_proxy, unreal.MovieSceneSpawnableBindingBase):
                    unreal.log("Binding is a spawnable")
                    spawnable_objects.append(binding_proxy)
        else:
            unreal.log_warning("No Level Sequence is currently open in the editor.")

        return spawnable_objects

    @staticmethod
    def _get_spawnable_bindings_from_current_sequence():
        """
        Return a list of spawnable binding proxies (top-level spawnables) from the currently-open
        Level Sequence. Returns [] if no sequence is open or on error.
        """
        try:
            seq = None
            try:
                seq = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
            except Exception:
                seq = None

            if not seq:
                unreal.log('No Level Sequence open in Sequencer; _get_spawnable_bindings_from_current_sequence returning []')
                return []

            try:
                spawnables = sequence_spawnables = seq.get_spawnables() or []
            except Exception:
                # fallback: some engine builds may expose get_spawnables differently
                try:
                    spawnables = seq.get_bindings() or []
                except Exception:
                    spawnables = []

            # Convert to list and return
            try:
                return list(spawnables)
            except Exception:
                return spawnables or []

        except Exception as e:
            unreal.log_warning(f'_get_spawnable_bindings_from_current_sequence: unexpected exception: {e}')
            return []

    @staticmethod
    def _resolve_actors_from_binding(binding_proxy, sequence=None):
        """
        Resolve runtime Actor instances targeted by the provided binding_proxy.
        - sequence: optional LevelSequence to use; if None, current open sequence is used
        Returns: list of Actor instances (deduplicated), may be empty.
        """
        try:
            if not binding_proxy:
                return []

            # obtain sequence if not provided
            if sequence is None:
                try:
                    sequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
                except Exception:
                    sequence = None

            # Helper: try to get bound objects using direct API or SequencerTools fallback
            def _get_bound_objects(binding):
                objs = []
                if sequence:
                    objs = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(binding)
                return objs
            binding_id = sequence.get_binding_id(binding_proxy)
            bound_objects = _get_bound_objects(binding_id)
            unreal.log(f"[CROWD CONVERSION]: Found {len(bound_objects)} bound objects.")

            actors = []
            for bo in bound_objects:
                # unreal.log(f"[CROWD CONVERSION]: {bo.get_name()}")
                actors.append(bo)
            return actors

        except Exception as e:
            unreal.log_warning(f'_resolve_actors_from_binding: unexpected exception: {e}')
            return []

    @staticmethod
    def _find_child_possessable_binding_for_component(top_binding: unreal.MovieSceneBindingProxy, skel_comp: unreal.SkeletalMeshComponent, sequence=None):
        """
        Given a top-level spawnable/possessable binding (top_binding) and a SkeletalMeshComponent (skel_comp),
        return the child possessable binding proxy that targets the given component, or None if not found.

        - top_binding: movie scene binding proxy (spawnable)
        - skel_comp: unreal.SkeletalMeshComponent instance to match against
        - sequence: optional LevelSequence; if None, current open sequence is used
        """
        try:
            if not top_binding or not skel_comp:
                return None

            if sequence is None:
                try:
                    sequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
                except Exception:
                    sequence = None

            # Get child possessables for the top-level binding
            try:
                children = unreal.MovieSceneBindingExtensions.get_child_possessables(top_binding) or []
            except Exception:
                children = []
            unreal.log(f"[CROWD CONVERSION]: find child possessable binding for component : {len(children)}")

            for child in children:
                child: unreal.MovieSceneBindingProxy
                unreal.log(f"[CROWD CONVERSION]: {child.get_name()} | {skel_comp.get_name()}")
                if (child.get_name() == skel_comp.get_name()):
                    unreal.log(f"[CROWD CONVERSION]: same")
                    return child

        except Exception as e:
            unreal.log_warning(f'_find_child_possessable_binding_for_component: unexpected exception: {e}')
            return None

        return None

    @staticmethod
    def _get_anim_sequences_from_child_binding(
        child_binding: unreal.MovieSceneBindingProxy,
        sequence: unreal.LevelSequence | None = None
    ) -> list:

        if not child_binding:
            return []

        if sequence is None and hasattr(unreal.LevelSequenceEditorBlueprintLibrary, 'get_current_level_sequence'):
            sequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()

        # ------------------------------------------------------------
        # Sequence display rate helpers
        # ------------------------------------------------------------
        def _get_sequence_display_rate(seq):
            num = None
            den = None
            rate_float = None

            movie_scene = seq.get_movie_scene() if hasattr(seq, 'get_movie_scene') else None

            if movie_scene and hasattr(movie_scene, 'get_display_rate'):
                fr = movie_scene.get_display_rate()
                num = getattr(fr, 'numerator', getattr(fr, 'Numerator', None))
                den = getattr(fr, 'denominator', getattr(fr, 'Denominator', None))

            if (num is None or den is None) and hasattr(seq, 'get_display_rate'):
                fr = seq.get_display_rate()
                num = num or getattr(fr, 'numerator', getattr(fr, 'Numerator', None))
                den = den or getattr(fr, 'denominator', getattr(fr, 'Denominator', None))

            if num is not None and den is not None and den != 0:
                rate_float = float(num) / float(den)

            return num, den, rate_float

        # ------------------------------------------------------------
        # Section helpers (DOCUMENTED API)
        # ------------------------------------------------------------
        def _has_start(section):
            if hasattr(section, 'has_start_frame'):
                return section.has_start_frame()
            return unreal.MovieSceneSectionExtensions.has_start_frame(section)

        def _has_end(section):
            if hasattr(section, 'has_end_frame'):
                return section.has_end_frame()
            return unreal.MovieSceneSectionExtensions.has_end_frame(section)

        def _get_start_frame(section):
            if hasattr(section, 'get_start_frame'):
                return section.get_start_frame()
            return unreal.MovieSceneSectionExtensions.get_start_frame(section)

        def _get_end_frame(section):
            if hasattr(section, 'get_end_frame'):
                return section.get_end_frame()
            return unreal.MovieSceneSectionExtensions.get_end_frame(section)

        def _get_start_seconds(section):
            if hasattr(section, 'get_start_frame_seconds'):
                return section.get_start_frame_seconds()
            return unreal.MovieSceneSectionExtensions.get_start_frame_seconds(section)

        def _get_end_seconds(section):
            if hasattr(section, 'get_end_frame_seconds'):
                return section.get_end_frame_seconds()
            return unreal.MovieSceneSectionExtensions.get_end_frame_seconds(section)

        def _extract_section_range(section, seq_fps):
            start_frame = None
            end_frame = None
            start_seconds = None
            end_seconds = None

            if _has_start(section):
                sf = _get_start_frame(section)
                start_frame = int(getattr(sf, 'value', sf))
                start_seconds = _get_start_seconds(section)

            if _has_end(section):
                ef = _get_end_frame(section)
                end_frame = int(getattr(ef, 'value', ef))
                end_seconds = _get_end_seconds(section)

            if seq_fps:
                if start_seconds is None and start_frame is not None:
                    start_seconds = float(start_frame) / float(seq_fps)
                if end_seconds is None and end_frame is not None:
                    end_seconds = float(end_frame) / float(seq_fps)

            return start_frame, end_frame, start_seconds, end_seconds

        # ------------------------------------------------------------
        # Anim length helper
        # ------------------------------------------------------------
        def _get_anim_length_seconds(anim: unreal.AnimSequence) -> float | None:
            if hasattr(anim, 'get_play_length'):
                return float(anim.get_play_length())
            if hasattr(anim, 'get_editor_property'):
                length = anim.get_editor_property('sequence_length')
                if length is not None:
                    return float(length)
            return None

        # ------------------------------------------------------------
        # Collect tracks
        # ------------------------------------------------------------
        if hasattr(child_binding, 'find_tracks_by_type'):
            tracks = child_binding.find_tracks_by_type(unreal.MovieSceneSkeletalAnimationTrack) or []
        else:
            tracks = child_binding.get_tracks() or []

        seq_num, seq_den, seq_fps = (None, None, None)
        if sequence:
            seq_num, seq_den, seq_fps = _get_sequence_display_rate(sequence)

        results: list[UppCrowdsConvertionBlueprintLibrary.AnimInfo] = []

        for track in tracks:
            if not isinstance(track, unreal.MovieSceneSkeletalAnimationTrack):
                continue

            sections = track.get_sections() if hasattr(track, 'get_sections') else []
            for section in sections:
                anim = None
                animation_first_offset_frames = None
                if hasattr(section, 'get_editor_property'):
                    params: unreal.MovieSceneSectionParameters = section.get_editor_property('params')
                    if params and hasattr(params, 'animation'):
                        anim = params.animation
                    if params and hasattr(params, 'animation'):
                        animation_first_offset_frames = params.first_loop_start_frame_offset.value / 1000

                if not isinstance(anim, unreal.AnimSequence):
                    continue

                anim_length_seconds = _get_anim_length_seconds(anim)
                anim_length_frames = None
                if anim_length_seconds is not None and seq_fps:
                    anim_length_frames = int(round(anim_length_seconds * seq_fps))

                (
                    section_start_frame,
                    section_end_frame,
                    section_start_seconds,
                    section_end_seconds
                ) = _extract_section_range(section, seq_fps)

                info = UppCrowdsConvertionBlueprintLibrary.AnimInfo()
                info.animation = anim
                info.animation_length_seconds = anim_length_seconds
                info.animation_length_frames = anim_length_frames
                info.animation_first_offset_frames = animation_first_offset_frames
                info.sequence_display_rate_num = seq_num
                info.sequence_display_rate_den = seq_den
                info.section_start_frame = section_start_frame
                info.section_end_frame = section_end_frame
                info.section_start_time_seconds = section_start_seconds
                info.section_end_time_seconds = section_end_seconds
                info.section = section

                results.append(info)

        return results

    @staticmethod
    def get_static_mesh_from_data_asset(data_asset):
        # try common property names first
        candidates = ['static_mesh', 'StaticMesh', 'mesh', 'Mesh', 'mesh_asset', 'StaticMeshAsset']
        for name in candidates:
            if hasattr(data_asset, name):
                try:
                    val = getattr(data_asset, name)
                    if isinstance(val, unreal.StaticMesh):
                        return val
                except Exception:
                    pass
            # sometimes it's stored as an editable property (SoftObjectPath etc.)
            try:
                val = data_asset.get_editor_property(name)
                if isinstance(val, unreal.StaticMesh):
                    return val
            except Exception:
                pass

        # fallback: scan all attributes and return the first StaticMesh we find
        for name in dir(data_asset):
            try:
                val = getattr(data_asset, name)
                if isinstance(val, unreal.StaticMesh):
                    return val
            except Exception:
                pass

        return None

    @staticmethod
    def get_all_materials_from_static_mesh(static_mesh, lod=0):
        if static_mesh is None:
            return []
        # number of material slots / sections on LOD 0
        # num = static_mesh.get_num_sections(lod)
        # unreal.log(f"[CROWD CONVERSION]: Found num sections {num}")
        materials = []
        for m in static_mesh.static_materials:
            materials.append(m.material_interface)
        # materials.extend(static_mesh.static_materials)
        unreal.log(f"[CROWD CONVERSION]: num mats {len(materials)}")
        # for i in range(num):
        # mat = static_mesh.get_material(i).get_base_material()
        # materials.append(mat)
        return materials

    @staticmethod
    def _resolve_loaded_static_mesh(value):
        """
        Helper: accept a value that might be a UStaticMesh, a SoftObjectPath (string), or a path,
        and return a loaded UStaticMesh or None.
        """
        try:
            if value is None:
                return None
            # Already a live UStaticMesh
            if isinstance(value, unreal.StaticMesh):
                return value

            # If this is a SoftObjectPath-like or string path, try to load it
            # Many engine builds expose SoftObjectPath, TAssetPtr, or return path strings from BP
            try:
                # If it's already a string path
                if isinstance(value, str):
                    maybe = unreal.load_asset(value)
                    if isinstance(maybe, unreal.StaticMesh):
                        return maybe
            except Exception:
                pass

            # Some wrappers expose get_path_name / get_name etc. Try to treat it as an asset ref
            try:
                path = None
                if hasattr(value, "get_path_name"):
                    path = value.get_path_name()
                elif hasattr(value, "get_editor_property"):
                    # If it's an asset pointer struct (e.g. AssetData-like), get its path/editor reference
                    try:
                        path = value.get_editor_property("asset_path_name")
                    except Exception:
                        pass
                if path:
                    maybe = unreal.load_asset(path)
                    if isinstance(maybe, unreal.StaticMesh):
                        return maybe
            except Exception:
                pass

        except Exception:
            pass
        return None

    @staticmethod
    def get_static_mesh_from_data_asset(data_asset):
        """
        Robust: find and return a loaded UStaticMesh from a data asset.
        Tries:
        - common property names via get_editor_property
        - bp_get_static_mesh() accessor if present (and loads if necessary)
        - scan static_materials array (and load entries)
        Returns a live unreal.StaticMesh instance or None.
        """
        if not data_asset:
            return None

        candidates = ['StaticMesh', 'static_mesh', 'mesh', 'Mesh', 'mesh_asset', 'StaticMeshAsset']
        # 1) try common editor properties
        for name in candidates:
            try:
                # Prefer get_editor_property for UE-exposed properties
                try:
                    val = data_asset.get_editor_property(name)
                except Exception:
                    # fallback to plain getattr (some blueprints expose direct attributes)
                    val = getattr(data_asset, name, None)

                sm = UppCrowdsConvertionBlueprintLibrary._resolve_loaded_static_mesh(val)
                if sm:
                    return sm
            except Exception:
                pass

        # 2) try common accessor function(s) like bp_get_static_mesh
        try:
            if hasattr(data_asset, "bp_get_static_mesh") and callable(getattr(data_asset, "bp_get_static_mesh")):
                try:
                    val = data_asset.bp_get_static_mesh()
                    sm = UppCrowdsConvertionBlueprintLibrary._resolve_loaded_static_mesh(val)
                    if sm:
                        return sm
                except Exception:
                    pass
        except Exception:
            pass

        # 3) fallback: scan static_materials array to see if it contains a material that references a static mesh (rare),
        # or just return None because we couldn't resolve a mesh
        try:
            mats = None
            try:
                mats = data_asset.get_editor_property("static_materials")
            except Exception:
                mats = None
            if mats:
                # Some entries may be structs that contain material_interface property
                for entry in mats:
                    try:
                        # nothing to resolve to static mesh here, but keep for debugging
                        pass
                    except Exception:
                        pass
        except Exception:
            pass

        return None

    @staticmethod
    def _duplicate_skeletal_mesh_into_folder(src_smesh: unreal.SkeletalMesh, version_folder: str, asset_tools):
        """
        Duplicate a SkeletalMesh into version_folder and return the duplicated object.
        If an asset with the same name already exists in the folder, return that existing asset.
        """
        try:
            if not src_smesh:
                return None

            # source name and path
            try:
                src_name = src_smesh.get_name()
                src_path = src_smesh.get_path_name()
            except Exception:
                return None

            target_name = src_name
            target_full = version_folder.rstrip('/') + '/' + target_name

            # If asset already exists in target folder, reuse it
            if unreal.EditorAssetLibrary.does_asset_exist(target_full):
                try:
                    existing = unreal.EditorAssetLibrary.load_asset(target_full)
                    if existing and isinstance(existing, unreal.SkeletalMesh):
                        unreal.log(f'Reusing existing skeletal mesh at {target_full}')
                        return existing
                except Exception:
                    pass

            # Attempt to duplicate with AssetTools (preferred)
            dup_obj = None
            try:
                dup_obj = asset_tools.duplicate_asset(target_name, version_folder, src_smesh)
            except Exception:
                dup_obj = None

            # Fallback to EditorAssetLibrary.duplicate_asset using full paths
            if not dup_obj:
                try:
                    dst_path = target_full
                    dup_obj = unreal.EditorAssetLibrary.duplicate_asset(src_path, dst_path)
                except Exception:
                    dup_obj = None

            if not dup_obj:
                unreal.log_warning(f'Failed to duplicate skeletal mesh "{src_path}" into {version_folder}.')
                return None

            # Save duplicated asset
            try:
                unreal.EditorAssetLibrary.save_loaded_asset(dup_obj)
            except Exception:
                pass

            unreal.log(f'Duplicated SkeletalMesh "{src_name}" -> {target_full}')
            return dup_obj

        except Exception as e:
            unreal.log_warning(f'_duplicate_skeletal_mesh_into_folder: unexpected exception: {e}')
            return None

    @staticmethod
    def _create_blueprint_with_static_meshes(version_folder: str,
                                             blueprint_name: str,
                                             static_mesh_objs: list,
                                             asset_tools) -> unreal.Blueprint | None:
        """
        Create an Actor Blueprint in `version_folder` named `blueprint_name`
        and add one StaticMeshComponent per entry in `static_mesh_objs`.
        Returns the created Blueprint asset (unreal.Blueprint) or None on failure.

        - version_folder: "/Game/Some/Folder"
        - blueprint_name: "BP_MyCombinedMeshes"
        - static_mesh_objs: iterable of unreal.StaticMesh objects
        - asset_tools: unreal.AssetToolsHelpers.get_asset_tools() instance
        """
        try:
            if not version_folder or not blueprint_name:
                unreal.log_warning('_create_blueprint_with_static_meshes: missing folder/name.')
                return None

            # Create Blueprint asset (Actor-derived)
            factory = unreal.BlueprintFactory()
            factory.set_editor_property('parent_class', unreal.Actor)

            # Prevent collisions: if already exists, reuse it (do not create duplicates)
            target_full = version_folder.rstrip('/') + '/' + blueprint_name
            if unreal.EditorAssetLibrary.does_asset_exist(target_full):
                bp_asset = unreal.EditorAssetLibrary.load_asset(target_full)
                if not isinstance(bp_asset, unreal.Blueprint):
                    unreal.log_warning(f'Existing asset at {target_full} is not a Blueprint. Aborting.')
                    return None
                unreal.log(f'Reusing existing Blueprint asset: {target_full}')
            else:
                bp_asset = asset_tools.create_asset(blueprint_name, version_folder, unreal.Blueprint, factory)
                if not bp_asset or not isinstance(bp_asset, unreal.Blueprint):
                    unreal.log_warning(f'Failed to create Blueprint asset {version_folder}/{blueprint_name}.')
                    return None
                unreal.log(f'Created Blueprint: {version_folder}/{blueprint_name}')

            # Get the SubobjectDataSubsystem (editor-side helper for blueprint subobjects)
            subsystem = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
            if not subsystem:
                unreal.log_warning('Could not get SubobjectDataSubsystem.')
                return bp_asset

            # Gather blueprint root handle (we'll attach new subobjects to this)
            root_handles = subsystem.k2_gather_subobject_data_for_blueprint(bp_asset) or []
            if not root_handles:
                unreal.log_warning('No root handles returned for blueprint; cannot add components.')
                return bp_asset
            blueprint_handle = root_handles[0]

            # helper function: create+attach subobject and return the created object (or None)
            BFL = unreal.SubobjectDataBlueprintFunctionLibrary

            created_components = []
            for idx, sm in enumerate(static_mesh_objs or []):
                if not sm:
                    created_components.append(None)
                    continue

                # prepare params: add a StaticMeshComponent subobject
                params = unreal.AddNewSubobjectParams(parent_handle=blueprint_handle,
                                                      new_class=unreal.StaticMeshComponent,
                                                      blueprint_context=bp_asset,
                                                      skip_mark_blueprint_modified=False,
                                                      conform_transform_to_parent=True)

                sub_handle, fail_reason = subsystem.add_new_subobject(params)
                if fail_reason and not getattr(fail_reason, 'is_empty', lambda: False)():
                    # some builds return a Text-like object for fail_reason; check emptiness defensively
                    try:
                        if not fail_reason.is_empty():
                            unreal.log_warning(f'add_new_subobject failed for mesh {getattr(sm, "get_name", lambda: "?")()}: {fail_reason}')
                            created_components.append(None)
                            continue
                    except Exception:
                        # fallback: treat non-empty as failure
                        unreal.log_warning(f'add_new_subobject returned a failure for mesh {getattr(sm, "get_name", lambda: "?")()}: {fail_reason}')
                        created_components.append(None)
                        continue

                # Attach subobject to the blueprint root
                attached_ok = subsystem.attach_subobject(blueprint_handle, sub_handle)
                if not attached_ok:
                    unreal.log_warning(f'attach_subobject failed for mesh {getattr(sm, "get_name", lambda: "?")()}; continuing.')

                # Find the SubobjectData and convert to an object we can edit
                sub_data = subsystem.k2_find_subobject_data_from_handle(sub_handle)
                if not sub_data:
                    unreal.log_warning('Failed to fetch SubobjectData for new component handle.')
                    created_components.append(None)
                    continue

                comp_obj = BFL.get_object(sub_data)
                if not comp_obj:
                    unreal.log_warning('Failed to resolve Python object for newly created component.')
                    created_components.append(None)
                    continue

                # Rename the component to a friendly name (e.g. SM_<meshname> or SM0, SM1...)
                try:
                    mesh_name = getattr(sm, 'get_name', lambda: f'sm{idx}')()
                    desired_comp_name = f"{mesh_name}"
                    # SubobjectDataSubsystem rename helper expects a Text; call rename_subobject
                    ren_ok = subsystem.rename_subobject(sub_handle, unreal.Text(desired_comp_name))
                    if not ren_ok:
                        # best-effort: try to set runtime name property on component (some builds)
                        try:
                            comp_obj.set_editor_property('name', desired_comp_name)
                        except Exception:
                            pass
                except Exception:
                    pass

                # Assign static mesh to the component (editor setter)
                try:
                    if hasattr(comp_obj, 'set_static_mesh'):
                        comp_obj.set_static_mesh(sm)
                    else:
                        comp_obj.set_editor_property('static_mesh', sm)
                except Exception as e:
                    unreal.log_warning(f'Failed to set static mesh on component {getattr(comp_obj, "get_name", lambda: "?")()}: {e}')

                # Optionally set mobility / collision / transform defaults here:
                try:
                    comp_obj.set_editor_property('mobility', unreal.ComponentMobility.MOVABLE)
                except Exception:
                    pass

                created_components.append(comp_obj)

            # Mark blueprint dirty and save
            try:
                unreal.EditorAssetLibrary.save_loaded_asset(bp_asset)
                unreal.log(f'Blueprint "{blueprint_name}" saved to {version_folder}.')
            except Exception:
                unreal.log_warning(f'Could not save blueprint "{blueprint_name}".')

            return bp_asset

        except Exception as e:
            unreal.log_warning(f'_create_blueprint_with_static_meshes: unexpected exception: {e}\n{traceback.format_exc()}')
            return None

    @staticmethod
    def get_object_from_binding(bind: unreal.MovieSceneBindingProxy):
        seq: unreal.MovieSceneSequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
        id = seq.get_binding_id(bind)
        obj = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(id)
        if obj:
            return obj[0]
        return None

    @staticmethod
    def copy_attach_tracks(src_binding: unreal.SequencerBindingProxy,
                           dst_binding: unreal.SequencerBindingProxy,
                           verbose: bool = True):
        """
        Copy all MovieScene3DAttachTrack + sections from src_binding to dst_binding.
        src_binding and dst_binding should be SequencerBindingProxy objects (you said you already have them).
        """

        # Aliases to extension libraries (stable helpers)
        binding_ext = unreal.MovieSceneBindingExtensions
        track_ext = unreal.MovieSceneTrackExtensions
        section_ext = unreal.MovieSceneSectionExtensions

        # Find all attach tracks on the source binding
        src_attach_tracks = binding_ext.find_tracks_by_type(src_binding, unreal.MovieScene3DAttachTrack)

        if verbose:
            unreal.log("Found {} attach track(s) on source binding '{}'".format(
                len(src_attach_tracks), binding_ext.get_display_name(src_binding)))

        for src_track in src_attach_tracks:
            # create an attach track on the destination binding
            dst_track = dst_binding.add_track(unreal.MovieScene3DAttachTrack)
            try:
                # copy display name / tint if you want
                src_display = src_track.get_display_name()
                track_ext.set_display_name(dst_track, src_display)
            except Exception:
                pass

            # copy sections
            src_sections = src_track.get_sections()
            if verbose:
                unreal.log("  Copying {} section(s) from track '{}'".format(len(src_sections), src_display))

            for src_section in src_sections:
                # add a new section to destination track (returns appropriate section subclass)
                dst_section: unreal.MovieSceneSection = dst_track.add_section()

                # 1) copy constraint / binding id (the thing the attach points to)
                #    If both bindings are in the same sequence, this MovieSceneObjectBindingID
                #    can be copied directly. If not, see note below about portable IDs.
                try:
                    constraint_id = src_section.get_constraint_binding_id()
                    # MovieScene3DConstraintSection exposes set_constraint_binding_id()
                    dst_section.set_constraint_binding_id(constraint_id)
                except Exception as e:
                    unreal.log_warning("Couldn't copy constraint binding id: {}".format(e))

                # 2) copy attach/socket names and attachment/detachment rules
                editor_props = [
                    "attach_component_name",
                    "attach_socket_name",
                    "attachment_location_rule",
                    "attachment_rotation_rule",
                    "attachment_scale_rule",
                    "detachment_location_rule",
                    "detachment_rotation_rule",
                    "detachment_scale_rule",
                    "color_tint"  # MovieScene3DAttachSection specific
                ]
                for p in editor_props:
                    try:
                        val = src_section.get_editor_property(p)
                        dst_section.set_editor_property(p, val)
                    except Exception:
                        # property might not exist on all versions/types — ignore quietly
                        pass
                dst_section.set_start_frame_bounded(False)
                dst_section.set_end_frame_bounded(False)
                # # 3) copy section range (start/end frames) using the SectionExtensions helpers
                # try:
                #     if section_ext.has_start_frame(src_section):
                #         start = section_ext.get_start_frame(src_section)
                #     else:
                #         start = None
                #     if section_ext.has_end_frame(src_section):
                #         end = section_ext.get_end_frame(src_section)
                #     else:
                #         end = None

                #     # choose frame-based set_range when possible
                #     if start is not None and end is not None:
                #         dst_section.set_range(start, end)
                #     elif start is not None:
                #         # no end frame: set start and leave end unset by setting a large end
                #         dst_section.set_range(start, start)
                #     # otherwise leave default
                # except Exception:
                #     # fallback: try seconds-based copy if frames fail
                #     try:
                #         s_seconds = section_ext.get_start_frame_seconds(src_section)
                #         e_seconds = section_ext.get_end_frame_seconds(src_section)
                #         dst_section.set_range_seconds(s_seconds, e_seconds)
                #     except Exception:
                #         pass

                # 4) copy active/locked/easing/post/pre-roll etc.
                simple_props = ["is_active", "is_locked"]
                for p in simple_props:
                    try:
                        val = src_section.get_editor_property(p)
                        dst_section.set_editor_property(p, val)
                    except Exception:
                        pass

                try:
                    # post-roll / pre-roll frames
                    dst_section.set_post_roll_frames(src_section.get_editor_property("post_roll_frames"))
                    dst_section.set_pre_roll_frames(src_section.get_editor_property("pre_roll_frames"))

                except Exception:
                    pass

                if verbose:
                    unreal.log("    Added attach section (copied)")

        if verbose:
            unreal.log("Copy complete.")

    # -------------------------
    # Example call (you said you already have src_binding and dst_binding variables):
    # sequence = unreal.load_asset("/Game/MySequence")  # only needed if you need sequence context
    # copy_attach_tracks_between_bindings(sequence, src_binding, dst_binding)

    @staticmethod
    def add_blueprint_spawnable_with_component_material_frame_tracks_from_animinfo(
            sequence: unreal.MovieSceneSequence,
            blueprint_asset: unreal.Blueprint,
            anim_info_by_component: dict,
            frame_param_name: str = "Frame",
            sample_rate: float = 24.0,
            spawn_location: unreal.Vector = unreal.Vector(),
            add_only: bool = True):
        """
        Create a spawnable (via a temporary actor instance) from blueprint_asset and add a
        component->material-slot->Frame track per StaticMeshComponent. Use per-component AnimInfo
        from anim_info_by_component (dict keyed by component name) to set section ranges and keys.

        Keys are placed at absolute sequence frame times (converted into the movie-scene tick resolution),
        while their values are VAT frame indices (0..animation_length_frames-1) mapped linearly across the section.

        NOTE: This variant defines a local helper get_vats_parameter_info(material_obj) (local-only),
        which returns True when the material/instance has the "VATs textures" parameter enabled.
        All attribute access is direct and no try/except blocks are used.
        """

        to_ignore = ['LOD2', 'LOD3', 'LOD4', 'LOD5to7']

        # def get_vats_parameter_info(material_obj) -> bool:
        #     target_name = "VATs textures"
        #     target_lc = target_name.lower()

        #     # Query static switch parameter names (works for both Material and MaterialInstanceConstant)
        #     switch_names = unreal.MaterialEditingLibrary.get_static_switch_parameter_names(material_obj) or []
        #     for sname in switch_names:
        #         if str(sname).lower() == target_lc or sname == unreal.Name(target_name):
        #             # If it's a material instance, use the instance-specific getter; otherwise use the default getter.
        #             if isinstance(material_obj, unreal.MaterialInstanceConstant):
        #                 # returns boolean-like value for the instance
        #                 value = unreal.MaterialEditingLibrary.get_material_instance_static_switch_parameter_value(material_obj, sname)
        #                 return bool(value)
        #             else:
        #                 # material default static switch getter (expects a base Material)
        #                 value = unreal.MaterialEditingLibrary.get_material_default_static_switch_parameter_value(material_obj, sname)
        #                 return bool(value)

        #     # # Fallback: scalar parameters on the instance/object
        #     # for sv in material_obj.scalar_parameter_values:
        #     #     pname = sv.parameter_info.name
        #     #     if str(pname).lower() == target_lc or pname == unreal.Name(target_name):
        #     #         return float(sv.parameter_value) != 0.0

        #     return False

        def get_vats_parameter_info(material_obj) -> bool:
            target_name = "VATs textures"
            target_lc = target_name.lower()

            # Utility to attempt safe retrieval of a "material-like" asset to query with MaterialEditingLibrary.
            def _resolve_editable_target(mat):
                # MaterialInstanceConstant is good as-is
                if isinstance(mat, unreal.MaterialInstanceConstant):
                    return ("instance", mat)

                # Material is good as-is
                if isinstance(mat, unreal.Material):
                    return ("material", mat)

                # MaterialInstanceDynamic: try to get the parent (base) material or base instance
                if isinstance(mat, unreal.MaterialInstanceDynamic):
                    parent = None
                    # Try common attribute names / methods defensively
                    if hasattr(mat, "parent") and mat.parent:
                        parent = mat.parent
                    else:
                        # try method names that may exist
                        try:
                            parent = mat.get_parent()
                        except Exception:
                            parent = None
                        if not parent:
                            try:
                                parent = mat.get_base_material()
                            except Exception:
                                parent = None

                    if parent:
                        if isinstance(parent, unreal.MaterialInstanceConstant):
                            return ("instance", parent)
                        if isinstance(parent, unreal.Material):
                            return ("material", parent)
                    # give up and return None
                    return (None, None)

                # Other MaterialInterface derived objects: try to treat as material
                if isinstance(material_obj, unreal.MaterialInterface):
                    # fallback: we will try calling default getter and hope for best
                    return ("material_interface", mat)

                return (None, None)

            # Query static switch parameter names (works for Material and MaterialInstanceConstant)
            switch_names = []
            try:
                switch_names = unreal.MaterialEditingLibrary.get_static_switch_parameter_names(material_obj) or []
            except Exception as e:
                unreal.log_warning(f"[CROWD CONVERSION]: Could not get static switch names for {material_obj} ({type(material_obj)}): {e}")
                switch_names = []

            for sname in switch_names:
                if str(sname).lower() == target_lc or sname == unreal.Name(target_name):
                    kind, target = _resolve_editable_target(material_obj)
                    if kind == "instance":
                        try:
                            value = unreal.MaterialEditingLibrary.get_material_instance_static_switch_parameter_value(target, sname)
                            return bool(value)
                        except Exception as e:
                            unreal.log_warning(f"[CROWD CONVERSION]: Failed instance switch query on {target} : {e}")
                            return False
                    elif kind == "material":
                        try:
                            value = unreal.MaterialEditingLibrary.get_material_default_static_switch_parameter_value(target, sname)
                            return bool(value)
                        except Exception as e:
                            unreal.log_warning(f"[CROWD CONVERSION]: Failed material switch query on {target} : {e}")
                            return False
                    else:
                        unreal.log_warning(f"[CROWD CONVERSION]: Could not resolve a usable material/instance for {material_obj} (type={type(material_obj)}).")
                        return False

            return False

        # resolve bp class and spawn an editor actor instance so we can inspect components
        bp_class = blueprint_asset.generated_class()
        if bp_class is None:
            raise Exception("Could not determine class from blueprint asset.")

        actor = unreal.EditorLevelLibrary.spawn_actor_from_class(bp_class, spawn_location)
        if not actor:
            raise Exception("Failed to spawn actor instance in the level.")

        # level-sequence subsystem for conversion later
        ls_sub = unreal.get_editor_subsystem(unreal.LevelSequenceEditorSubsystem)

        # collect the parent binding (we create a possessable for each component – convert parent at end)
        parent_binding = None

        display_fps = None
        tick_fps = None
        dr = sequence.get_display_rate()
        disp_num = dr.numerator
        disp_den = dr.denominator
        display_fps = float(disp_num) / float(disp_den)

        tr = sequence.get_tick_resolution()
        tick_num = tr.numerator
        tick_den = tr.denominator
        tick_fps = float(tick_num) / float(tick_den)

        # iterate StaticMeshComponents on the temporary actor
        static_comps = actor.get_components_by_class(unreal.StaticMeshComponent)
        unreal.log(f"[CROWD CONVERSION]: Found {len(static_comps)} StaticMeshComponent(s) on spawned actor.")

        for idx, comp in enumerate(static_comps):
            comp: unreal.StaticMeshComponent

            # add a possessable binding for the component (component-level possessable)
            comp_binding = sequence.add_possessable(comp)

            # record parent binding (the first component's parent is the actor possessable)
            if parent_binding is None:
                parent_binding = comp_binding.get_parent()

            # determine the material slot names from the StaticMesh assigned on the component (if any)
            static_mesh = comp.static_mesh

            name = None
            if static_mesh:
                sm_slots = static_mesh.static_materials or []
                for material_slot_index, slot in enumerate(sm_slots):
                    name = slot.material_slot_name

                    # get the material assigned on this component for the given slot index
                    mat_asset = comp.get_material(material_slot_index)
                    comp.set_evaluate_world_position_offset(True)
                    comp.set_evaluate_world_position_offset_in_ray_tracing(True)
                    # Quick skip if no material at this slot
                    if not mat_asset:
                        continue

                    # Use the local helper to determine if VATs are enabled on this material/instance.
                    vats_enabled = get_vats_parameter_info(mat_asset)
                    if not vats_enabled:
                        # skip this slot if VATs are not enabled on this material
                        continue

                    # only proceed when we have a slot name and VATs enabled
                    if name:
                        name: unreal.Name
                        to_continue = False
                        for lod in to_ignore:
                            if lod in str(name):
                                to_continue = True

                        if to_continue:  # skip all lods > 1
                            continue

                        # create the material track on the component binding
                        mat_track: unreal.MovieSceneComponentMaterialTrack = comp_binding.add_track(unreal.MovieSceneComponentMaterialTrack)
                        mat_track.set_display_name(mat_asset.get_name())
                        if slot.material_slot_name == "MaterialSlot":
                            slot.material_slot_name = mat_asset.get_name()

                        # set the material info (index + slot-name + type)
                        mat_track.set_material_info(unreal.ComponentMaterialInfo(
                            material_slot_index=material_slot_index,
                            material_slot_name=name,
                            material_type=unreal.ComponentMaterialType.INDEXED_MATERIAL
                        ))

                        # unreal.log(f"[CROWD CONVERSION]: Component binding created for '{comp.get_name()}' slot {material_slot_index} -> {comp_binding}")

                        # find AnimInfo for this component by name
                        comp_name = comp.get_name()
                        anim_info: UppCrowdsConvertionBlueprintLibrary.AnimInfo = anim_info_by_component.get(comp_name) if anim_info_by_component else None

                        # If add_only is True and the material parameter already exists on the track, skip adding duplicate
                        param_already_present = False
                        if add_only:
                            for existing_section in mat_track.get_sections():
                                names = existing_section.get_parameter_names()
                                if unreal.Name(frame_param_name) in names or frame_param_name in [str(n) for n in names]:
                                    param_already_present = True
                                    break
                        if add_only and param_already_present:
                            unreal.log(f"[CROWD CONVERSION]: Parameter '{frame_param_name}' already exists on material track for '{comp_name}' slot {material_slot_index}, skipping.")
                            continue

                        # create a new material-parameter section on this track
                        section: unreal.MovieSceneSection = mat_track.add_section()

                        # If AnimInfo present and provides section frame range, use it; otherwise create a short placeholder
                        if anim_info and anim_info.section_start_frame is not None and anim_info.section_end_frame is not None:
                            start_display_frame = int(anim_info.section_start_frame)
                            end_display_frame = int(anim_info.section_end_frame)

                            # set section range in display frames
                            section.set_range(start_display_frame, end_display_frame)
                            # compute VAT baked-frame domain
                            baked_frame_count = anim_info.animation_length_frames
                            if baked_frame_count is None or int(baked_frame_count) <= 1:
                                unreal.log_warning(f"AnimInfo for component '{comp_name}' missing valid animation_length_frames; creating placeholder keys instead.")
                                section.set_range(0, 30)
                                start_fn = unreal.FrameNumber(0)
                                end_fn = unreal.FrameNumber(30)
                                param_info = unreal.MaterialParameterInfo(
                                    name=unreal.Name(frame_param_name),
                                    association=unreal.MaterialParameterAssociation.GLOBAL_PARAMETER,
                                    index=-1
                                )
                                mat_asset = comp.get_material(material_slot_index)
                                asset_name = mat_asset.get_name() if mat_asset else ""
                                channel.add_scalar_parameter_key(param_info, start_fn, 0.0, "", asset_name)
                                channel.add_scalar_parameter_key(param_info, end_fn, 1.0, "", asset_name)
                                unreal.log(f"[CROWD CONVERSION]: Added placeholder Frame keys for '{comp_name}' (0..30) on slot {material_slot_index}.")
                                continue

                            # Build MaterialParameterInfo
                            param_info = unreal.MaterialParameterInfo(
                                name=unreal.Name(frame_param_name),
                                association=unreal.MaterialParameterAssociation.GLOBAL_PARAMETER,
                                index=-1
                            )
                            mat_asset = comp.get_material(material_slot_index)
                            asset_name = mat_asset.get_name() if mat_asset else ""

                            # # Convert display-frame (sequence display rate frames) -> tick-frame (movie-scene tick resolution)
                            # def _to_tick_frame(display_frame):
                            #     time_seconds = float(display_frame) / float(display_fps)
                            #     tick_frame_f = time_seconds * float(tick_fps)
                            #     return int(round(tick_frame_f))

                            # start_tick_frame = _to_tick_frame(start_display_frame)
                            # end_tick_frame = _to_tick_frame(end_display_frame)

                            # build FrameNumber instances with tick frame values
                            start_time_fn = unreal.FrameNumber(start_display_frame * 1000)
                            end_time_fn = unreal.FrameNumber((end_display_frame) * 1000)
                            # end_time_fn = unreal.FrameNumber((end_display_frame - 1) * 1000)

                            # target values: 0 .. baked_frame_count - 1 (VAT index domain)
                            start_value = anim_info.animation_first_offset_frames
                            end_value = start_value + (end_display_frame - start_display_frame)

                            if end_value - start_value < baked_frame_count:  # animation is within the baked range
                                # add keys at absolute positions (tick-frame times) with VAT index values (linear)
                                section.add_scalar_parameter_key(param_info, start_time_fn, float(start_value), "", asset_name, unreal.MovieSceneKeyInterpolation.LINEAR)
                                section.add_scalar_parameter_key(param_info, end_time_fn, float(end_value + 1), "", asset_name, unreal.MovieSceneKeyInterpolation.LINEAR)
                            else:
                                # animation is not within the baked range -> loop the anim
                                # --- START replacement block ---
                                baked = int(baked_frame_count) if baked_frame_count is not None else 0
                                global_start = int(start_value)
                                global_end = int(end_value)

                                if baked <= 0 or global_end < global_start:
                                    unreal.log_warning(f"Invalid VAT range for '{comp_name}', skipping.")
                                else:
                                    def vat_to_display_frame(vat_global):
                                        return int(start_display_frame) + (vat_global - global_start)

                                    # Build VAT points: start, every boundary (m * baked), boundary+1, end
                                    vat_points = [global_start]
                                    first_mult = (global_start // baked) + 1
                                    last_mult = (global_end // baked)
                                    for m in range(first_mult, last_mult + 1):
                                        mult = m * baked
                                        if global_start < mult <= global_end:
                                            vat_points.append(mult)         # boundary
                                        post = mult + 1
                                        if global_start < post <= global_end:
                                            vat_points.append(post)        # post-boundary

                                    if vat_points[-1] != global_end:
                                        vat_points.append(global_end)

                                    # sort & unique (defensive)
                                    vat_points = sorted(dict.fromkeys(vat_points))

                                    keys_written = 0
                                    for vat_global in vat_points:
                                        df = vat_to_display_frame(vat_global)
                                        if df < int(start_display_frame) or df > int(end_display_frame):
                                            continue

                                        fn = unreal.FrameNumber(int(df * 1000))

                                        # Decide value & interpolation strictly following the rules:
                                        if vat_global == global_start:
                                            value = float(global_start)    # start: absolute global value
                                            interp = unreal.MovieSceneKeyInterpolation.LINEAR

                                        elif (vat_global % baked) == 0:
                                            # boundary: baked + 1, CONSTANT
                                            value = float(baked + 1)
                                            interp = unreal.MovieSceneKeyInterpolation.CONSTANT

                                        elif ((vat_global - 1) % baked) == 0:
                                            # post-boundary: 0, LINEAR
                                            value = 1.0
                                            interp = unreal.MovieSceneKeyInterpolation.LINEAR

                                        elif vat_global == global_end:
                                            # final: wrapped end value, LINEAR
                                            value = float(global_end % baked)
                                            interp = unreal.MovieSceneKeyInterpolation.LINEAR

                                        else:
                                            # fallback: wrapped VAT value, LINEAR
                                            value = float(vat_global % baked)
                                            interp = unreal.MovieSceneKeyInterpolation.LINEAR

                                        section.add_scalar_parameter_key(
                                            param_info,
                                            fn,
                                            value,
                                            "",
                                            asset_name,
                                            default_interpolation=interp
                                        )

                                        # channel: unreal.MovieSceneScriptingFloatChannel
                                        # channel.add_key(fn, value, interpolation=interp)
                                        keys_written += 1

                                        #  unreal.log(f"DEBUG: SEQ Wrote key {df}: {value}, {interp}")

                                    # unreal.log(
                                    #     f"[CROWD CONVERSION]: Added {keys_written} VAT keys for '{comp_name}' "
                                    #     f"VAT [{global_start}..{global_end}], baked={baked}, slot={material_slot_index}. "
                                    #     f"VAT points: {vat_points}"
                                    # )
                                # --- END replacement block ---
                            channels = section.get_all_channels()
                            try:
                                channel = channels[0]
                                keys = channel.get_keys()
                                for key in keys:
                                    key: unreal.MovieSceneScriptingFloatKey
                                    if key.get_value() == baked_frame_count + 1:
                                        key.set_interpolation_mode(unreal.MovieSceneKeyInterpolation.CONSTANT)
                            except:
                                unreal.log_warning("DEBUG: Did not get any keys.")
                                # Convert the parent possessable (actor) to a spawnable so it behaves as a spawnable in Sequencer
                                # convert parent -> spawnable(s) and get the new binding proxies
        new_bindings = ls_sub.convert_to_spawnable(parent_binding)

        # Iterate any spawnable bindings returned and update their templates
        for new_bind in new_bindings:
            new_bind: unreal.MovieSceneBindingProxy
            # 1) Update the spawnable *template* inside the sequence
            template = unreal.MovieSceneBindingExtensions.get_object_template(new_bind)
            if template:
                # template is an Actor (the spawnable template). Update its components.
                for tcomp in template.get_components_by_class(unreal.StaticMeshComponent):
                    # set the ray-tracing WPO flag on the template component
                    tcomp.set_evaluate_world_position_offset_in_ray_tracing(True)
                # mark the template as modified so editor knows it's changed (undo/dirty)
                try:
                    template.modify()
                except Exception:
                    pass

            # iterate child possessables of the new binding and update each one
            child_possessables = new_bind.get_child_possessables()
            unreal.log(f"[CROWD CONVERSION]: # child_possessables {len(child_possessables)}")
            for child_possessable in child_possessables:
                # Set EvaluateWorldPositionOffsetInRayTracing to True - add a track for a spawnable
                child_possessable: unreal.MovieSceneBindingProxy
                track = child_possessable.add_track(unreal.MovieSceneBoolTrack)
                track.set_property_name_and_path(
                    "bEvaluateWorldPositionOffsetInRayTracing",
                    "bEvaluateWorldPositionOffsetInRayTracing"
                )

                section = track.add_section()
                section.set_start_frame_bounded(False)
                section.set_end_frame_bounded(False)
                channel: unreal.MovieSceneScriptingBoolChannel = section.get_all_channels()[0]
                channel.set_default(True)

        # mark the sequence dirty and optionally save
        sequence.modify()
        try:
            sequence.post_edit_change()
        except:
            pass
        # optional: save sequence asset to persist changes immediately
        # unreal.EditorAssetLibrary.save_loaded_asset(sequence)

        return new_bindings[0]

    @unreal.ufunction(static=True, ret=bool)
    def solo_binding_and_record_temp_mute_tag():
        """
        Solo the selected binding(s): keep selected binding(s) active and
        deactivate all others. Record bindings we actively changed
        (active -> inactive) in the LevelSequence's MovieSceneMetaData.notes
        under the "temp_mute_bindings" key as JSON.
        """
        seq = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
        if not seq:
            unreal.log_error("No Level Sequence is currently open in Sequencer.")
            return False

        # get selected bindings (treat all selected as targets)
        selected = unreal.LevelSequenceEditorBlueprintLibrary.get_selected_bindings()
        if not selected:
            unreal.log_error("No binding selected in Sequencer.")
            return False

        # helper to get all bindings (works across engine versions)
        if hasattr(seq, "get_bindings"):
            bindings = seq.get_bindings()
        else:
            bindings = unreal.MovieSceneSequenceExtensions.get_bindings(seq)

        # robust matcher: compare GUIDs first, then display names, then object/str fallback
        def is_binding_target(candidate_binding, target_bindings):
            for t in target_bindings:
                try:
                    # GUID comparison (preferred)
                    if hasattr(candidate_binding, "get_id") and hasattr(t, "get_id"):
                        try:
                            cid = candidate_binding.get_id()
                            tid = t.get_id()
                            if cid == tid:
                                return True
                        except Exception:
                            # ignore and try next method
                            pass
                    # display name comparison
                    if hasattr(candidate_binding, "get_display_name") and hasattr(t, "get_display_name"):
                        try:
                            cname = candidate_binding.get_display_name()
                            tname = t.get_display_name()
                            if cname == tname:
                                return True
                        except Exception:
                            pass
                    # direct object comparsion
                    try:
                        if candidate_binding == t:
                            return True
                    except Exception:
                        pass
                    # fallback to string comparison
                    try:
                        if str(candidate_binding) == str(t):
                            return True
                    except Exception:
                        pass
                except Exception:
                    # ensure robust loop even if one target misbehaves
                    continue
            return False

        changed_binding_ids = []

        # single pass: set selected bindings active, others inactive
        for b in bindings:
            b: unreal.MovieSceneBindingProxy
            is_target = is_binding_target(b, selected)
            if not is_target and b.get_parent():
                is_target = is_binding_target(b.get_parent(), selected)

            # compute whether any section currently active for this binding
            binding_was_active = False
            try:
                tracks = b.get_tracks()
            except Exception:
                tracks = []
            for t in tracks:
                try:
                    sections = t.get_sections()
                except Exception:
                    sections = []
                for s in sections:
                    try:
                        if s.get_editor_property('is_active'):
                            binding_was_active = True
                            break
                    except Exception:
                        pass
                if binding_was_active:
                    break

            # toggle sections: target -> active, others -> inactive
            for t in tracks:
                try:
                    sections = t.get_sections()
                except Exception:
                    sections = []
                for s in sections:
                    try:
                        s.set_editor_property('is_active', is_target)
                    except Exception as e:
                        unreal.log_warning("Couldn't set section.is_active: {}".format(e))

            # if we just turned this binding off (was active, now not target) record it
            if (not is_target) and binding_was_active:
                try:
                    bid = b.get_id()
                    changed_binding_ids.append(str(bid))
                except Exception:
                    try:
                        changed_binding_ids.append(str(b.get_display_name()))
                    except Exception:
                        changed_binding_ids.append(str(b))

        # refresh UI once
        unreal.LevelSequenceEditorBlueprintLibrary.refresh_current_level_sequence()

        # write metadata if we changed any bindings
        if changed_binding_ids:
            try:
                meta = seq.find_or_add_meta_data_by_class(unreal.MovieSceneMetaData)
                existing_notes = meta.get_notes() or ""
                parsed = {}
                try:
                    parsed = json.loads(existing_notes) if existing_notes else {}
                    if not isinstance(parsed, dict):
                        parsed = {"notes": existing_notes}
                except Exception:
                    parsed = {"notes": existing_notes} if existing_notes else {}

                parsed["temp_mute_bindings"] = changed_binding_ids
                meta.set_notes(json.dumps(parsed))
            except Exception as e:
                unreal.log_warning("Failed to write temp mute metadata: {}".format(e))

        unreal.log("Soloed {} selected binding(s); recorded {} bindings as temp-mute.".format(
            len(selected), len(changed_binding_ids)))
        return True

    @staticmethod
    def restore_temp_mute_bindings_from_tag(tag_key="temp_mute_bindings"):
        """
        Restore bindings that were recorded under `tag_key` in the current open Level Sequence's
        MovieSceneMetaData.notes. Re-activates all sections for each recorded binding, then removes
        the tag from the metadata. Returns True if at least one binding was restored.
        """
        seq = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
        if not seq:
            unreal.log_error("No Level Sequence is currently open in Sequencer.")
            return False

        # find metadata
        try:
            meta = seq.find_meta_data_by_class(unreal.MovieSceneMetaData)
        except Exception:
            meta = None

        if not meta:
            unreal.log_warning("Restore: No metadata found on sequence.")
            return False

        try:
            notes = meta.get_notes() or ""
        except Exception:
            notes = ""

        if not notes:
            unreal.log_warning("Restore: Metadata notes empty.")
            return False

        try:
            parsed = json.loads(notes)
        except Exception:
            unreal.log_warning("Restore: Metadata notes are not valid JSON.")
            return False

        if tag_key not in parsed:
            unreal.log_warning(f"Restore: No '{tag_key}' key in metadata.")
            return False

        id_list = parsed.get(tag_key, [])
        if not isinstance(id_list, list) or not id_list:
            unreal.log_warning(f"Restore: '{tag_key}' is empty or invalid.")
            return False

        # resolve bindings from the sequence
        try:
            if hasattr(seq, "get_bindings"):
                bindings = seq.get_bindings()
            else:
                bindings = unreal.MovieSceneSequenceExtensions.get_bindings(seq)
        except Exception:
            unreal.log_warning("Restore: Failed to retrieve bindings from sequence.")
            return False

        restored = 0
        for saved_id in id_list:
            for b in bindings:
                matched = False
                # try GUID match where possible
                try:
                    if hasattr(b, "get_id"):
                        if str(b.get_id()) == str(saved_id):
                            matched = True
                except Exception:
                    pass

                # fallback: match display name
                if not matched:
                    try:
                        if hasattr(b, "get_display_name") and str(b.get_display_name()) == str(saved_id):
                            matched = True
                    except Exception:
                        pass

                if matched:
                    # activate all sections for this binding
                    try:
                        tracks = b.get_tracks() if hasattr(b, "get_tracks") else []
                    except Exception:
                        tracks = []
                    for t in tracks:
                        try:
                            secs = t.get_sections() if hasattr(t, "get_sections") else []
                        except Exception:
                            secs = []
                        for s in secs:
                            try:
                                s.set_editor_property('is_active', True)
                            except Exception:
                                try:
                                    s.set_is_active(True)
                                except Exception:
                                    pass
                    restored += 1
                    break

        # remove the saved entry from JSON and write back (preserve other keys)
        try:
            if tag_key in parsed:
                del parsed[tag_key]
            new_notes = json.dumps(parsed) if parsed else ""
            meta.set_notes(new_notes)
        except Exception as e:
            unreal.log_warning(f"Restore: Failed to clear metadata tag: {e}")

        # refresh UI
        try:
            unreal.LevelSequenceEditorBlueprintLibrary.refresh_current_level_sequence()
        except Exception:
            pass

        unreal.log(f"RestoreTempMuteBindings: Restored {restored} bindings.")
        return restored > 0

    @unreal.ufunction(static=True, params=[], ret=bool, meta=dict(Category="Sequencer|Utilities"))
    def mute_all_bindings():
        """
        Mute (deactivate) all sections on all bindings in the currently open Level Sequence.
        Returns True if at least one section was deactivated.
        """
        seq: unreal.MovieSceneSequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
        if not seq:
            unreal.log_error("mute_all_bindings: No Level Sequence is currently open in Sequencer.")
            return False

        # get bindings (engine-version safe)
        try:
            if hasattr(seq, "get_bindings"):
                bindings = seq.get_bindings()
            else:
                bindings = unreal.MovieSceneSequenceExtensions.get_bindings(seq)
        except Exception:
            unreal.log_warning("mute_all_bindings: Failed to retrieve bindings from sequence.")
            return False

        changed = 0

        for b in bindings:
            try:
                tracks = b.get_tracks() if hasattr(b, "get_tracks") else []
            except Exception:
                tracks = []

            for t in tracks:
                try:
                    sections = t.get_sections() if hasattr(t, "get_sections") else []
                except Exception:
                    sections = []

                for s in sections:
                    try:
                        # read current state if possible
                        try:
                            active = s.get_editor_property("is_active")
                        except Exception:
                            try:
                                active = s.is_active()
                            except Exception:
                                active = True  # assume active if unreadable

                        if active:
                            # deactivate section
                            try:
                                s.set_editor_property("is_active", False)
                            except Exception:
                                try:
                                    s.set_is_active(False)
                                except Exception:
                                    unreal.log_warning(f"mute_all_bindings: Could not mute section {s}.")
                            changed += 1
                    except Exception:
                        pass

        # refresh Sequencer UI
        try:
            unreal.LevelSequenceEditorBlueprintLibrary.refresh_current_level_sequence()
        except Exception:
            pass

        unreal.log(f"mute_all_bindings: Muted {changed} sections.")
        return changed > 0

    @unreal.ufunction(static=True, params=[], ret=bool, meta=dict(Category="Sequencer|Utilities"))
    def unmute_all_bindings():
        """
        Unmute (activate) all sections on all bindings in the currently open Level Sequence.
        Returns True if at least one section was re-activated.
        """
        seq = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
        if not seq:
            unreal.log_error("unmute_all_bindings: No Level Sequence is currently open in Sequencer.")
            return False

        # get bindings (works across engine versions)
        try:
            if hasattr(seq, "get_bindings"):
                bindings = seq.get_bindings()
            else:
                bindings = unreal.MovieSceneSequenceExtensions.get_bindings(seq)
        except Exception:
            unreal.log_warning("unmute_all_bindings: Failed to retrieve bindings from sequence.")
            return False

        changed = 0

        for b in bindings:
            try:
                tracks = b.get_tracks() if hasattr(b, "get_tracks") else []
            except Exception:
                tracks = []

            for t in tracks:
                try:
                    sections = t.get_sections() if hasattr(t, "get_sections") else []
                except Exception:
                    sections = []

                for s in sections:
                    try:
                        # read current active state (try editor property, then method)
                        active = None
                        try:
                            active = s.get_editor_property("is_active")
                        except Exception:
                            try:
                                # some engine versions expose is_active() method
                                active = s.is_active()
                            except Exception:
                                # if we can't read, assume active (skip)
                                active = True

                        if not active:
                            # try to set editor property, fallback to method
                            try:
                                s.set_editor_property("is_active", True)
                            except Exception:
                                try:
                                    s.set_is_active(True)
                                except Exception:
                                    unreal.log_warning(f"unmute_all_bindings: Could not set active on section {s}.")
                            changed += 1
                    except Exception:
                        # keep going if any single section fails
                        pass

        # refresh sequencer UI so the change is visible
        try:
            unreal.LevelSequenceEditorBlueprintLibrary.refresh_current_level_sequence()
        except Exception:
            pass

        unreal.log(f"unmute_all_bindings: Restored {changed} sections.")
        return changed > 0

    @staticmethod
    def refresh_actor_render_state(actor: unreal.Actor):
        comps = actor.get_components_by_class(unreal.PrimitiveComponent)
        for c in comps:
            try:
                c.mark_render_state_dirty()
            except Exception:
                # Some builds may expose a different name; ignore failures and try alternatives
                pass
        try:
            actor.mark_render_state_dirty()
        except:
            pass

    @unreal.ufunction(static=True, params=[bool])
    def convert_meshes(is_delete_original_bindings_active: bool):
        """
        Revised convert_meshes() with:
         - proper selection of anim_sequences_for_job (component -> binding -> body fallback)
         - set post_process_anim_blueprint = None and regenerate duplicated skeletal mesh LOD when needed
         - keeps the rest of your pipeline (conversion, material duplication, AnimToTexture job, TX move,
           per-animation blueprint creation and Frame-track wiring) intact.
        """
        debug = False

        def debug_print(message):
            if debug:
                unreal.log(message)

        assets_to_delete = []
        bindings_to_delete = []
        # sequence, err = utils.duplicate_and_open_current_level_sequence("_dup")
        # if not sequence:
        #     unreal.log_error(f"Sequence not loaded, {err}")
        sequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
        display_rate = unreal.MovieSceneSequenceExtensions.get_display_rate(sequence)

        numerator = getattr(display_rate, "numerator", 24)
        denominator = getattr(display_rate, "denominator", 1)
        fps = float(numerator) / float(denominator)

        body_anim = None
        body_anim_info = None
        hide_material: unreal.Material = unreal.load_asset("/Game/MetaHumans/Common/Materials/M_Hide.M_Hide", unreal.Material)

        # Per-actor scope: root_static_by_skeletal ensures single root static per skeletal per version_folder
        root_static_by_skeletal = {}
        version_folder_per_vats_root = {}

        # Get selected spawnable bindings (if sequence open)
        try:
            selected_bindings = unreal.LevelSequenceEditorBlueprintLibrary.get_selected_bindings() or []
        except Exception:
            selected_bindings = []

        if selected_bindings:
            unreal.log(f'Using {len(selected_bindings)} spawnable binding(s) from current Level Sequence as the top-level iteration.')
        else:
            unreal.log('No spawnable bindings found in open Level Sequence; falling back to selection.')

        asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
        static_mesh_editor_subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        anim_to_texture_lib = None
        try:
            anim_to_texture_lib = unreal.AnimToTextureBPLibrary
        except Exception:
            anim_to_texture_lib = None

        # iterate selected spawnable bindings
        if selected_bindings:
            for binding_index, top_binding in enumerate(selected_bindings):
                top_binding: unreal.MovieSceneBindingProxy
                # unreal.LevelSequenceEditorBlueprintLibrary.refresh_current_level_sequence()

                data_assets = []
                map_mats_to_da = []
                textures = []
                binding_folder = utils.find_folder_for_binding(sequence, top_binding)
                # one anim_map per binding: a single anim_key (binding-level) -> entry
                anim_map = {}

                resolved_actors = UppCrowdsConvertionBlueprintLibrary._resolve_actors_from_binding(top_binding)
                if not resolved_actors:
                    unreal.log(f'[CROWD CONVERSION]: spawnable binding {getattr(top_binding, "get_display_name", lambda: "?")()} resolved no actor instances.')
                    continue

                # we'll choose a single binding-level AnimInfo (first found among components)
                binding_anim_info = None

                transform_loc = None
                sequence: unreal.MovieSceneSequence
                top_id = sequence.get_binding_id(top_binding)
                ids = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(top_id)
                if ids:
                    obj: unreal.Actor = ids[0]
                    transform_loc = obj.get_actor_location()

                # unreal.SystemLibrary.delay_until_next_frame(None, unreal.LatentActionInfo())
                # resolved_actors = [top_binding.get_object_template()]  # DEBUG, IDK
                # iterate actors from this binding
                for actor in resolved_actors:
                    skel_comps = actor.get_components_by_class(unreal.SkeletalMeshComponent) or []
                    debug_print(f"[CROWD CONVERSION]: Found {len(skel_comps)} skeletal components on actor {actor.get_name()}")
                    if not skel_comps:
                        unreal.log(f'Actor "{actor.get_name()}" has no SkeletalMeshComponent. Skipping.')
                        continue

                    blueprint_folder = utils._get_blueprint_folder_from_actor(actor)
                    vats_root = utils._ensure_vats_folder(blueprint_folder)
                    unreal.log(f'Using vats root folder: {vats_root}')
                    # base_name = actor.get_name()
                    base_name = utils.strip_trailing_instance_id(str(actor.get_name()))
                    unreal.log(f'Assets base name: {base_name}')

                    # create a single new version folder per actor (as requested)
                    if vats_root in version_folder_per_vats_root:
                        version_folder_name = version_folder_per_vats_root[vats_root]
                    else:
                        version_folder_name = UppCrowdsConvertionBlueprintLibrary._next_version_folder(vats_root)
                        version_folder_per_vats_root[vats_root] = version_folder_name

                    version_folder = vats_root.rstrip('/') + '/' + version_folder_name
                    try:
                        if not unreal.EditorAssetLibrary.does_directory_exist(version_folder):
                            unreal.EditorAssetLibrary.make_directory(version_folder)
                    except Exception as e:
                        unreal.log_warning(f'Could not make version folder {version_folder}: {e}')
                    unreal.log(f'Using version folder: {version_folder}')

                    # First pass: inspect all components for their AnimInfo to determine binding_anim_info
                    comp_anim_infos = {}
                    for comp in skel_comps:
                        unreal.log_warning(f"Processing {comp.get_name()}")
                        try:
                            child_binding = UppCrowdsConvertionBlueprintLibrary._find_child_possessable_binding_for_component(top_binding, comp)
                        except Exception:
                            child_binding = None
                        if child_binding:
                            infos = UppCrowdsConvertionBlueprintLibrary._get_anim_sequences_from_child_binding(child_binding) or []
                            if infos:
                                comp_anim_infos[comp.get_name()] = infos[0]
                                if not binding_anim_info:
                                    binding_anim_info = infos[0]
                                # prefer body as fallback capture
                                if str(comp.get_name()).lower() == "body" and not body_anim_info:
                                    body_anim_info = infos[0]

                    # binding-level fallback
                    if not binding_anim_info and body_anim_info:
                        binding_anim_info = body_anim_info

                    # determine anim_key for this binding (single)
                    if binding_anim_info and getattr(binding_anim_info, 'animation', None):
                        safe_name = UppCrowdsConvertionBlueprintLibrary._anim_subfolder_safe_name(binding_anim_info)
                        sf = getattr(binding_anim_info, 'section_start_frame', None)
                        ef = getattr(binding_anim_info, 'section_end_frame', None)
                        if sf is not None and ef is not None:
                            anim_key = f"{safe_name}_f{int(sf)}-{int(ef)}"
                        else:
                            anim_key = safe_name
                    else:
                        anim_key = "noanim"

                    # prepare anim folders for this binding's anim_key
                    anim_folder = version_folder.rstrip('/') + '/' + anim_key
                    mats_folder = anim_folder + '/materials'
                    tex_folder = anim_folder + '/textures'
                    if not unreal.EditorAssetLibrary.does_directory_exist(anim_folder):
                        unreal.EditorAssetLibrary.make_directory(anim_folder)
                    if not unreal.EditorAssetLibrary.does_directory_exist(mats_folder):
                        unreal.EditorAssetLibrary.make_directory(mats_folder)
                    if not unreal.EditorAssetLibrary.does_directory_exist(tex_folder):
                        unreal.EditorAssetLibrary.make_directory(tex_folder)

                    entry = anim_map.setdefault(anim_key, {
                        'anim_folder': anim_folder,
                        'mats_folder': mats_folder,
                        'tex_folder': tex_folder,
                        'statics': [],
                        'duplicated_materials': [],
                        'anim_info_by_component': {}
                    })

                    # process components: convert, duplicate materials into mats_folder, duplicate static into anim_folder
                    for comp in skel_comps:
                        comp: unreal.SkeletalMeshComponent
                        comp_name_raw = comp.get_name()
                        debug_print(f"[CROWD CONVERSION]: Processing component {comp_name_raw}")

                        # per-component AnimInfo if available; otherwise use binding_anim_info; body fallback later
                        comp_anim_info = comp_anim_infos.get(comp_name_raw, binding_anim_info)
                        if str(comp_name_raw).lower() == "body" and comp_anim_info and not body_anim_info:
                            body_anim_info = comp_anim_info

                        # get skeletal mesh asset
                        skeletal_mesh = None
                        try:
                            skeletal_mesh = comp.get_skeletal_mesh_asset()
                        except Exception:
                            try:
                                skeletal_mesh = comp.get_editor_property('skeletal_mesh')
                            except Exception:
                                skeletal_mesh = None

                        if not skeletal_mesh:
                            unreal.log(f'Component "{comp_name_raw}" on actor "{actor.get_name()}" has no assigned SkeletalMesh. Skipping this component.')
                            continue

                        created_static_path = None
                        static_mesh_obj = None

                        if skeletal_mesh.get_name() in root_static_by_skeletal:
                            # static mesh previously converted from skeletal mesh
                            static_mesh_obj = root_static_by_skeletal[skeletal_mesh.get_name()]
                        else:
                            # duplicate skeletal mesh into version folder (if helper present)
                            duplicated_skeletal_mesh = UppCrowdsConvertionBlueprintLibrary._duplicate_skeletal_mesh_into_folder(
                                skeletal_mesh, version_folder, asset_tools
                            )
                            skeletal_mesh_used = duplicated_skeletal_mesh if duplicated_skeletal_mesh else skeletal_mesh

                            # If component name matches disable list, clear post_process_anim_blueprint and regenerate LOD on the duplicated skeletal
                            to_disable = ['face']
                            disable = any(k in str(comp_name_raw).lower() for k in to_disable)
                            if disable:
                                try:
                                    debug_print(f"[CROWD CONVERSION]: post process (before) {skeletal_mesh_used.get_editor_property('post_process_anim_blueprint')}")
                                    skeletal_mesh_used.set_editor_property('post_process_anim_blueprint', None)
                                    debug_print(f"[CROWD CONVERSION]: post process (after) {skeletal_mesh_used.get_editor_property('post_process_anim_blueprint')}")
                                    # regenerate LOD 1 — wrapped in try/except because API can vary
                                    try:
                                        skeletal_mesh_used.regenerate_lod(1, True, False)
                                    except Exception:
                                        # some builds may expose different regenerate method names or signatures — ignore if unavailable
                                        pass
                                except Exception as e:
                                    unreal.log_warning(f"[CROWD CONVERSION]: Failed to clear post_process_anim_blueprint or regenerate LOD: {e}")

                            # build versioned base name and convert to static mesh
                            base_asset_name = f"{base_name}_{re.sub(r'[^0-9A-Za-z_]+', '_', comp_name_raw).strip('_') or 'mesh'}"
                            versioned_asset_name = utils._next_versioned_name(version_folder, base_asset_name)
                            unreal.log(f'Preparing conversion for component "{comp_name_raw}" -> {version_folder}/{versioned_asset_name}')
                            if anim_to_texture_lib:
                                try:
                                    pkg_name = version_folder + '/' + versioned_asset_name.replace("BP_", "SM_", 1)
                                    unreal.log(f'Using AnimToTextureBPLibrary.convert_skeletal_mesh_to_static_mesh for "{skeletal_mesh_used.get_name()}" -> {pkg_name}')
                                    static_mesh_obj = anim_to_texture_lib.convert_skeletal_mesh_to_static_mesh(skeletal_mesh_used, pkg_name, 0)
                                    if static_mesh_obj:
                                        created_static_path = static_mesh_obj.get_path_name()
                                        unreal.log(f'AnimToTexture conversion produced StaticMesh: {created_static_path}')
                                except Exception as e:
                                    unreal.log_warning(f'AnimToTexture conversion failed for "{skeletal_mesh_used.get_name()}": {e}\n{traceback.format_exc()}')
                                    static_mesh_obj = None

                            if not created_static_path and static_mesh_obj:
                                created_static_path = static_mesh_obj.get_path_name()

                            desired_pkg = version_folder.rstrip('/') + '/' + versioned_asset_name.replace("BP_", "SM_", 1)
                            try:
                                if created_static_path and created_static_path != desired_pkg:
                                    if unreal.EditorAssetLibrary.does_asset_exist(desired_pkg):
                                        unreal.log_warning(f'Target asset path already exists: {desired_pkg}. Leaving imported asset at {created_static_path}.')
                                    else:
                                        success = unreal.EditorAssetLibrary.rename_asset(created_static_path, desired_pkg)
                                        if success:
                                            unreal.log(f'Renamed StaticMesh asset "{created_static_path}" -> "{desired_pkg}"')
                                            created_static_path = desired_pkg
                                            static_mesh_obj = unreal.EditorAssetLibrary.load_asset(created_static_path)
                                        else:
                                            unreal.log_warning(f'Failed to rename "{created_static_path}" to "{desired_pkg}" (rename_asset returned False).')
                            except Exception as ren_e:
                                unreal.log_warning(f'Exception during rename of "{created_static_path}" -> "{desired_pkg}": {ren_e}')

                        if not static_mesh_obj:
                            unreal.log_warning(f'No StaticMesh object found after conversion for component "{comp_name_raw}".')
                            continue

                        if not (skeletal_mesh.get_name() in root_static_by_skeletal):
                            root_static_by_skeletal[skeletal_mesh.get_name()] = static_mesh_obj

                        # gather component materials
                        comp_materials = UppCrowdsConvertionBlueprintLibrary._get_component_materials(comp, skeletal_mesh_used)

                        # duplicate materials into mats_folder for this binding-level anim and set VAT params
                        duplicated_materials = []
                        for idx, src_mat in enumerate(comp_materials):
                            if not src_mat:
                                duplicated_materials.append(hide_material)
                                unreal.log_warning("[Crowd conversion]: Source material was not found, using the hide material!")
                                continue

                            mat_to_duplicate = src_mat
                            try:
                                src_name = src_mat.get_name()
                            except Exception:
                                src_name = None
                            if isinstance(src_name, str) and src_name.startswith("MID_"):
                                parent = UppCrowdsConvertionBlueprintLibrary._resolve_mi_parent_if_mid(src_mat)
                                if parent:
                                    mat_to_duplicate = parent

                            dup_obj = UppCrowdsConvertionBlueprintLibrary._duplicate_material_into_folder(mat_to_duplicate, entry['mats_folder'], asset_tools)
                            if not dup_obj:
                                duplicated_materials.append(None)
                                continue

                            UppCrowdsConvertionBlueprintLibrary._set_mic_max_wpo_displacement(dup_obj, 50000.0)
                            UppCrowdsConvertionBlueprintLibrary._enable_vats_textures_param(dup_obj)
                            duplicated_materials.append(dup_obj)

                        # Duplicate static mesh into animation folder and assign duplicated materials
                        per_anim_static = UppCrowdsConvertionBlueprintLibrary._duplicate_static_mesh_into_folder(static_mesh_obj, entry['anim_folder'], asset_tools)
                        if not per_anim_static:
                            unreal.log_warning(f'Failed to produce per-animation static mesh in {entry["anim_folder"]}; falling back to converted static.')
                            per_anim_static = static_mesh_obj

                        UppCrowdsConvertionBlueprintLibrary._assign_materials_to_static_mesh(per_anim_static, duplicated_materials)
                        UppCrowdsConvertionBlueprintLibrary._enable_nanite(per_anim_static, static_mesh_editor_subsystem)

                        # Build anim sequence list for this data asset: priority component -> binding -> body -> empty
                        anim_sequences_for_job = []
                        if comp_anim_info and getattr(comp_anim_info, 'animation', None):
                            anim_sequences_for_job = [comp_anim_info.animation]
                        elif binding_anim_info and getattr(binding_anim_info, 'animation', None):
                            anim_sequences_for_job = [binding_anim_info.animation]
                        elif body_anim_info and getattr(body_anim_info, 'animation', None):
                            anim_sequences_for_job = [body_anim_info.animation]
                        else:
                            anim_sequences_for_job = []

                        # Create AnimToTextureDataAsset in anim_folder
                        data_asset = UppCrowdsConvertionBlueprintLibrary._create_anim_to_texture_job(
                            static_mesh_obj=per_anim_static,
                            skeletal_mesh_obj=skeletal_mesh_used,
                            version_folder=entry['anim_folder'],
                            asset_tools=asset_tools,
                            anim_to_texture_lib=anim_to_texture_lib,
                            asset_name=None,
                            anim_sequences=anim_sequences_for_job,
                            mode=unreal.AnimToTextureMode.BONE,
                            uv_channel=1,
                            sample_rate=fps
                        )

                        if data_asset:
                            tex_dict = UppCrowdsConvertionBlueprintLibrary._create_and_assign_bone_textures_for_data_asset(
                                data_asset=data_asset,
                                version_folder=entry['anim_folder'],
                                base_name=versioned_asset_name,
                                asset_tools=asset_tools,
                                rt_width=8192,
                                rt_height=8192
                            )
                            moved_textures = {}
                            if tex_dict:
                                for k, tx_obj in (tex_dict or {}).items():
                                    if not tx_obj:
                                        moved_textures[k] = None
                                        continue
                                    try:
                                        old_pkg = tx_obj.get_path_name().split('.')[0]
                                        new_pkg = entry['tex_folder'].rstrip('/') + '/' + tx_obj.get_name()
                                        if old_pkg != new_pkg:
                                            try:
                                                ok = unreal.EditorAssetLibrary.rename_asset(old_pkg, new_pkg)
                                                if ok:
                                                    new_obj = unreal.EditorAssetLibrary.load_asset(new_pkg)
                                                    moved_textures[k] = new_obj
                                                else:
                                                    moved_textures[k] = tx_obj
                                            except Exception:
                                                moved_textures[k] = tx_obj
                                        else:
                                            moved_textures[k] = tx_obj
                                    except Exception:
                                        moved_textures[k] = tx_obj
                                # assign textures back to data_asset (best-effort)
                                try:
                                    if moved_textures.get('pos'):
                                        data_asset.set_editor_property('bone_position_texture', moved_textures.get('pos'))
                                    if moved_textures.get('rot'):
                                        data_asset.set_editor_property('bone_rotation_texture', moved_textures.get('rot'))
                                    if moved_textures.get('wgt'):
                                        data_asset.set_editor_property('bone_weight_texture', moved_textures.get('wgt'))
                                    unreal.EditorAssetLibrary.save_loaded_asset(data_asset)
                                except Exception:
                                    pass
                            textures.append(moved_textures if moved_textures else {})

                            # record items to delete later
                            if duplicated_skeletal_mesh and duplicated_skeletal_mesh not in assets_to_delete:
                                assets_to_delete.append(duplicated_skeletal_mesh)
                            if data_asset and data_asset not in assets_to_delete:
                                assets_to_delete.append(data_asset)

                            # update duplicated material instances from data asset
                            UppCrowdsConvertionBlueprintLibrary._update_material_instances_from_data_asset(data_asset, per_anim_static, anim_to_texture_lib)

                            data_assets.append(data_asset)
                            map_mats_to_da.append(duplicated_materials)

                        entry['statics'].append(per_anim_static)
                        entry['duplicated_materials'].append(duplicated_materials)
                        # map static name -> anim_info_for_comp (use binding_anim_info fallback)
                        try:
                            entry['anim_info_by_component'][per_anim_static.get_name()] = comp_anim_info if comp_anim_info else binding_anim_info
                        except Exception:
                            entry['anim_info_by_component'][per_anim_static.get_name() if hasattr(per_anim_static, 'get_name') else comp_name_raw] = comp_anim_info if comp_anim_info else binding_anim_info

                # done processing actors/components for this binding. Create blueprints per anim_map entry
                for anim_key, entry in anim_map.items():
                    statics_for_bp = entry.get('statics') or []
                    if not statics_for_bp:
                        unreal.log_warning(f'No static meshes found for animation entry {anim_key}; skipping blueprint creation.')
                        continue

                    bp_name = f"BP_VAT_{base_name}_{anim_key}"
                    bp = UppCrowdsConvertionBlueprintLibrary._create_blueprint_with_static_meshes(
                        version_folder=entry['anim_folder'],
                        blueprint_name=bp_name,
                        static_mesh_objs=statics_for_bp,
                        asset_tools=asset_tools
                    )

                    if not bp:
                        unreal.log_warning(f'Failed to create blueprint for animation {anim_key} (folder {entry["anim_folder"]})')
                        continue

                    # Build anim_info_by_component mapping by static names
                    anim_info_by_component = {}
                    for sm in statics_for_bp:
                        try:
                            comp_key = sm.get_name()
                        except Exception:
                            comp_key = getattr(sm, 'get_path_name', lambda: None)()
                            if comp_key:
                                comp_key = comp_key.split('/')[-1]
                        anim_info_by_component[comp_key] = entry['anim_info_by_component'].get(comp_key, body_anim_info)

                    debug_print(f"[CROWD CONVERSION]: anim_info_by_component for blueprint {bp_name}:")
                    for k, v in anim_info_by_component.items():
                        unreal.log(f"  {k} -> {getattr(v, 'animation', None)}")

                    bp_binding: unreal.MovieSceneBindingProxy = UppCrowdsConvertionBlueprintLibrary.add_blueprint_spawnable_with_component_material_frame_tracks_from_animinfo(
                        sequence=unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence(),
                        blueprint_asset=bp,
                        anim_info_by_component=anim_info_by_component,
                        frame_param_name="Frame",
                        sample_rate=fps,
                        spawn_location=transform_loc,
                        add_only=True,
                    )

                    unreal.MovieSceneFolderExtensions.add_child_object_binding(binding_folder, bp_binding)
                    # copy transform track from original binding to the new blueprint binding
                    try:
                        utils.copy_transform_track_between_bindings(top_binding, bp_binding)
                    except Exception:
                        unreal.log_warning(f"[CROWD CONVERSION]: Transform track to {base_name} could not be copied!")
                    # copy attach track from original binding to the new blueprint binding
                    UppCrowdsConvertionBlueprintLibrary.copy_attach_tracks(top_binding, bp_binding)

                    try:
                        UppCrowdsConvertionBlueprintLibrary.refresh_actor_render_state(UppCrowdsConvertionBlueprintLibrary.get_object_from_binding(bp_binding))
                    except:
                        pass
                # assign anim_sequences to data_assets if missing (unchanged)
                for data_asset in data_assets:
                    try:
                        if len(data_asset.anim_sequences) == 0:
                            unreal.log("[CROWD CONVERSION]: Found no attached anim_sequence!")
                            seq_structs = UppCrowdsConvertionBlueprintLibrary._make_anim_sequence_structs([body_anim])
                            try:
                                try:
                                    setattr(data_asset, 'anim_sequences', seq_structs)
                                except Exception:
                                    try:
                                        setattr(data_asset, 'AnimSequences', seq_structs)
                                    except Exception:
                                        data_asset.set_editor_property('AnimSequences', seq_structs)
                            except Exception:
                                try:
                                    data_asset.set_editor_property('anim_sequences', seq_structs)
                                except Exception as e:
                                    unreal.log_warning(f'Failed to assign AnimSequences array on DA {getattr(data_asset, "get_name", lambda: "?")()}: {e}')
                    except Exception:
                        pass

                # Bake animations into VAT textures and update materials (existing logic retained)
                for i, data_asset in enumerate(data_assets):
                    try:
                        if unreal.AnimToTextureBPLibrary.set_light_map_index(data_asset.bp_get_static_mesh(), data_asset.static_lod_index, 2):
                            debug_print(f"[CROWD CONVERSION]: Data asset static mesh name '{data_asset.bp_get_static_mesh()}'")
                            if unreal.AnimToTextureBPLibrary.animation_to_texture(data_asset):
                                static_mesh = UppCrowdsConvertionBlueprintLibrary.get_static_mesh_from_data_asset(data_asset)
                                if static_mesh is None:
                                    unreal.log_warning("Could not resolve UStaticMesh for DA; dumping data_asset keys")
                                    unreal.log(str(dir(data_asset)))
                                else:
                                    materials = UppCrowdsConvertionBlueprintLibrary.get_all_materials_from_static_mesh(static_mesh)
                                    debug_print(f"[CROWD CONVERSION]: Found {len(materials)} materials:")
                                    for i, m in enumerate(materials):
                                        if isinstance(m, unreal.MaterialInstance):
                                            m: unreal.MaterialInstance
                                            if m:
                                                unreal.AnimToTextureBPLibrary.update_material_instance_from_data_asset(data_asset, m, unreal.MaterialParameterAssociation.GLOBAL_PARAMETER)
                    except Exception:
                        unreal.log_warning(f"Exception baking data_asset {getattr(data_asset, 'get_name', lambda: '?')()}")

                # Set filtering after baking animations
                for _textures in textures:
                    for key in (_textures.keys() if _textures else []):
                        tex: unreal.Texture2D = _textures[key]
                        if not tex:
                            continue
                        debug_print(f"[CROWD CONVERSION]: Filter {tex.filter}")
                        if key == 'wgt':
                            tex.filter = unreal.TextureFilter.TF_NEAREST
                        else:
                            tex.filter = unreal.TextureFilter.TF_DEFAULT
                        debug_print(f"[CROWD CONVERSION]: Filter changed to {tex.filter}")
                        unreal.EditorAssetLibrary.save_loaded_asset(tex, False)

                if binding_index % 2 == 0:  # collect garbage after 3 processings
                    unreal.collect_garbage()  # only a flag
                    gc.collect()  # actual garbage collection
                if is_delete_original_bindings_active:
                    bindings_to_delete.append(top_binding)
                duplicated_materials = None
                moved_textures.clear()
                unreal.EditorAssetLibrary.save_loaded_asset(data_asset)

            # utils.ui_mute_all_tracks_for_binding(top_binding)
            # delete temporary/duplicated assets that are not needed
            for asset in assets_to_delete:
                try:
                    path = asset.get_path_name()
                except Exception:
                    path = None
                if path:
                    try:
                        if unreal.EditorAssetLibrary.does_asset_exist(path):
                            ok = unreal.EditorAssetLibrary.delete_asset(path)
                            if ok:
                                unreal.log(f'Deleted temporary asset: {path}')
                            else:
                                unreal.log_warning(f'Could not delete temporary asset: {path}')
                    except Exception:
                        pass
            assets_to_delete.clear()
        for binding in bindings_to_delete:
            utils.delete_binding(binding)

        unreal.log('All selected actors processed.')
