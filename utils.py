import datetime
import re
import unreal
import os


def copy_attach_tracks_between_bindings(level_sequence: unreal.LevelSequence,
                                        src_binding,
                                        dst_binding,
                                        reuse_existing_track: bool = True,
                                        replace_existing: bool = False):
    """
    Copy attach tracks (and their sections) from src_binding to dst_binding.
    - level_sequence: unreal.LevelSequence that owns the bindings (required if you need portable-binding translation later)
    - src_binding, dst_binding: binding objects you already have (MovieSceneBindingProxy / binding proxy-like object)
    - reuse_existing_track: if True and dst already has an attach track, append sections to the first existing attach track
    - replace_existing: if True, remove existing attach tracks on dst before copying

    Returns True on success, False on fatal error.
    """
    MovieSceneSeqExt = unreal.MovieSceneSequenceExtensions
    TrackExt = unreal.MovieSceneTrackExtensions
    SectionExt = unreal.MovieSceneSectionExtensions
    BindingExt = unreal.MovieSceneBindingExtensions

    # Basic validation (binding proxies should be truthy)
    if not src_binding:
        unreal.log_error("copy_attach_tracks_between_bindings: src_binding is None/invalid.")
        return False
    if not dst_binding:
        unreal.log_error("copy_attach_tracks_between_bindings: dst_binding is None/invalid.")
        return False

    # Gather src attach tracks
    src_tracks = BindingExt.get_tracks(src_binding)
    attach_tracks = [t for t in src_tracks if isinstance(t, unreal.MovieScene3DAttachTrack)]
    if not attach_tracks:
        unreal.log("No attach tracks found on source binding; nothing to copy.")
        return True

    # Optionally remove existing attach tracks on destination
    dst_tracks = BindingExt.get_tracks(dst_binding)
    existing_attach_tracks = [t for t in dst_tracks if isinstance(t, unreal.MovieScene3DAttachTrack)]
    if replace_existing and existing_attach_tracks:
        for t in existing_attach_tracks:
            try:
                BindingExt.remove_track(dst_binding, t)
            except Exception as e:
                unreal.log_warning(f"Could not remove existing attach track: {e}")

        existing_attach_tracks = []

    # Decide on destination track to reuse (first one if requested)
    dest_reuse_track = existing_attach_tracks[0] if (reuse_existing_track and existing_attach_tracks) else None

    # Copy each attach track from source
    for src_track in attach_tracks:
        if dest_reuse_track:
            dest_track = dest_reuse_track
        else:
            dest_track = BindingExt.add_track(dst_binding, unreal.MovieScene3DAttachTrack)

        # Copy some track-level editor properties where available
        try:
            TrackExt.set_display_name(dest_track, src_track.get_display_name())
        except Exception:
            pass
        try:
            if hasattr(src_track, "track_tint") and hasattr(TrackExt, "set_color_tint"):
                TrackExt.set_color_tint(dest_track, getattr(src_track, "track_tint"))
        except Exception:
            pass

        # Copy sections
        for src_section in src_track.get_sections():
            try:
                new_section = dest_track.add_section()
            except Exception as e:
                unreal.log_warning(f"Failed to add section on destination track: {e}")
                continue

            # Copy section range (seconds getters/setters are more stable across versions)
            try:
                if SectionExt.has_start_frame(src_section):
                    SectionExt.set_start_frame_seconds(new_section, SectionExt.get_start_frame_seconds(src_section))
                if SectionExt.has_end_frame(src_section):
                    SectionExt.set_end_frame_seconds(new_section, SectionExt.get_end_frame_seconds(src_section))
            except Exception:
                # non-fatal
                pass

            # Copy common attach-section properties if present (safe getattr/setattr)
            props_to_try = [
                "attach_component_name", "attach_socket_name",
                "attachment_location_rule", "attachment_rotation_rule", "attachment_scale_rule",
                "detachment_location_rule", "detachment_rotation_rule", "detachment_scale_rule",
                "color_tint", "is_active", "is_locked"
            ]
            for p in props_to_try:
                try:
                    if hasattr(src_section, p) and hasattr(new_section, p):
                        setattr(new_section, p, getattr(src_section, p))
                except Exception:
                    # some properties can be read-only; ignore silently
                    pass

            # Copy the constraint (target) binding id
            try:
                # If the section is a MovieScene3DConstraintSection, these methods exist.
                constraint_binding_id = src_section.get_constraint_binding_id()
                # If you need to translate this constraint between subsequences, convert to portable ID using
                # MovieSceneSequenceExtensions.get_portable_binding_id(level_sequence, constraint_binding_id)
                # (not done here unless required).
                new_section.set_constraint_binding_id(constraint_binding_id)
            except Exception as e:
                unreal.log_warning(f"Could not copy constraint binding id for a section: {e}")

    unreal.log("copy_attach_tracks_between_bindings: Completed.")
    return True

# -------------------------
# Example call (you said you already have src_binding and dst_binding variables):
# sequence = unreal.load_asset("/Game/MySequence")  # only needed if you need sequence context
# copy_attach_tracks_between_bindings(sequence, src_binding, dst_binding)


def _get_blueprint_folder_from_actor(actor: unreal.Actor) -> str:
    """Try to extract the content folder of the actor's Blueprint class.
    Falls back to '/Game' if parsing fails.
    Example class path formats this handles:
      '/Game/Path/To/BP_MyActor.BP_MyActor_C'
      'BlueprintGeneratedClass /Game/Path/To/BP_MyActor.BP_MyActor_C'
    """
    try:
        class_path = actor.get_class().get_path_name()
    except Exception:
        return '/Game'

    # Find a substring that begins with /Game and go until the '.' (asset name delimiter)
    m = re.search(r'(/Game[^\s\.]*)', class_path)
    if not m:
        # try a more permissive match
        m2 = re.search(r'(/Game/.+?)/', class_path)
        if m2:
            return m2.group(1)
        return '/Game'

    asset_full = m.group(1)
    # asset_full is like '/Game/Path/To/BP_MyActor' -> folder is dirname
    parts = asset_full.split('/')
    if len(parts) <= 2:
        return '/Game'
    folder = '/'.join(parts[:-1])
    return folder


def _ensure_vats_folder(folder: str) -> str:
    dest = folder.rstrip('/') + '/vats'
    if not unreal.EditorAssetLibrary.does_directory_exist(dest):
        unreal.EditorAssetLibrary.make_directory(dest)
    return dest


def _next_versioned_name(dest_folder: str, base_name: str) -> str:
    assets = unreal.EditorAssetLibrary.list_assets(dest_folder, recursive=False)
    pattern = re.compile(re.escape(base_name) + r'_v(\d{3})$')
    maxv = 0
    for a in assets:
        name = a.split('/')[-1]
        m = pattern.match(name)
        if m:
            try:
                v = int(m.group(1))
                if v > maxv:
                    maxv = v
            except Exception:
                pass
    return f"{base_name}_v{(maxv+1):03d}"


def _convert_actor_to_static(actor: unreal.Actor, dest_folder: str) -> list:
    """Call the editor conversion routine. Returns the new actors (if any).
    Note: convert_actors is part of the Editor scripting utilities (deprecated wrapper to subsystems
    in newer engine versions) but is commonly available in the Python API.
    """
    try:
        # convert_actors expects: (actors, actor_class, static_mesh_package_path)
        new_actors = unreal.EditorLevelLibrary.convert_actors([actor], unreal.StaticMeshActor, dest_folder)
        # Save assets created in the folder
        unreal.EditorAssetLibrary.save_directory(dest_folder, only_if_is_dirty=True, recursive=True)
        return new_actors
    except Exception as e:
        unreal.log_warning(f"Conversion failed for {actor.get_name()}: {e}")
        return []


def convert_selected_to_static():
    selected = unreal.EditorLevelLibrary.get_selected_level_actors()
    if not selected:
        unreal.log_warning('No actors selected. Please select Blueprint actor instances in the level.')
        return

    for actor in selected:
        # Find skeletal mesh components
        skel_comps = actor.get_components_by_class(unreal.SkeletalMeshComponent)
        body_comp = None
        if skel_comps:
            # prefer a component with 'body' in the name
            for c in skel_comps:
                try:
                    if 'body' in c.get_name().lower():
                        body_comp = c
                        break
                except Exception:
                    continue
            if not body_comp:
                body_comp = skel_comps[0]
        else:
            unreal.log(f'Actor "{actor.get_name()}" has no SkeletalMeshComponent. Skipping.')
            continue

        skeletal_mesh = None
        try:
            skeletal_mesh = body_comp.get_editor_property('skeletal_mesh')
        except Exception:
            skeletal_mesh = None

        if not skeletal_mesh:
            unreal.log(f'Component on actor "{actor.get_name()}" has no assigned SkeletalMesh asset. Skipping.')
            continue

        blueprint_folder = os.path.dirname(_get_blueprint_folder_from_actor(actor))
        dest_folder = _ensure_vats_folder(blueprint_folder)

        base_name = actor.get_name()
        versioned_name = _next_versioned_name(dest_folder, base_name)

        unreal.log(f'Converting "{actor.get_name()}" (skeletal: {skeletal_mesh.get_path_name()}) to static mesh in: {dest_folder} as ~{versioned_name}')

        # new_actors = _convert_actor_to_static(actor, dest_folder)
        new_actors = True
        if new_actors:
            unreal.log(f'Conversion succeeded for "{actor.get_name()}".')
        else:
            unreal.log_warning(f'Conversion for "{actor.get_name()}" did not produce new actors/assets.')


def resolve_binding_object(
    binding: unreal.MovieSceneBindingProxy
) -> unreal.Object:
    """
    Resolves a binding proxy to its live editor object (Actor or Component).
    """

    binding_id = unreal.MovieSceneSequenceExtensions.get_binding_id(unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence(), binding)

    objects = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(binding_id)

    if not objects:
        raise RuntimeError("Selected binding has no resolved objects")

    return objects[0]


def get_object_world_transform(obj: unreal.Object) -> unreal.Transform:
    """
    Returns world transform for Actor or SceneComponent.
    """

    if isinstance(obj, unreal.Actor):
        return obj.get_actor_transform()

    if isinstance(obj, unreal.SceneComponent):
        return obj.get_world_transform()

    raise TypeError(f"Unsupported bound object type: {type(obj).__name__}")


def get_binding_transform(binding: unreal.MovieSceneBindingProxy):
    obj = resolve_binding_object(binding)
    transform = get_object_world_transform(obj)
    unreal.log(f"DEBUG: binding transform: {transform}")
    return transform


def get_bound_object_from_binding(
    sequence: unreal.LevelSequence,
    binding: unreal.MovieSceneBindingProxy | str
) -> unreal.Object:
    """
    Resolve the first bound object for a given binding in the active Sequencer editor.

    :param sequence: LevelSequence containing the binding
    :param binding: MovieSceneBindingProxy or binding display name
    :return: Resolved UObject (Actor or Component)
    """

    # Allow passing a binding name for convenience
    if isinstance(binding, str):
        binding = unreal.MovieSceneSequenceExtensions.find_binding_by_name(sequence, binding)

    if not binding:
        raise RuntimeError("Binding not found in sequence")

    binding_id = unreal.MovieSceneSequenceExtensions.get_binding_id(sequence, binding)

    bound_objects = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(binding_id)

    if not bound_objects:
        raise RuntimeError("Binding has no resolved objects in the Sequencer editor")

    return bound_objects[0]


def get_object_world_transform(obj: unreal.Object) -> unreal.Transform:
    """
    Returns world transform for Actor or SceneComponent.
    """

    if isinstance(obj, unreal.Actor):
        return obj.get_actor_transform()

    if isinstance(obj, unreal.SceneComponent):
        return obj.get_world_transform()
    raise TypeError(f"Unsupported bound object type: {type(obj).__name__}")


@staticmethod
def copy_transform_track_between_bindings(src_binding: unreal.MovieSceneBindingProxy,
                                          dst_binding: unreal.MovieSceneBindingProxy,
                                          replace_existing: bool = True) -> bool:
    """
    Copy the first MovieScene3DTransformTrack from src_binding to dst_binding.
    If replace_existing is True, all existing transform tracks on dst_binding are removed first.
    Returns True on success, False otherwise.
    """
    if not unreal.MovieSceneBindingExtensions.is_valid(src_binding):
        unreal.log_error("Source binding is not valid.")
        return False
    if not unreal.MovieSceneBindingExtensions.is_valid(dst_binding):
        unreal.log_error("Destination binding is not valid.")
        return False

    # Find source transform track(s)
    src_transform_tracks = unreal.MovieSceneBindingExtensions.find_tracks_by_type(
        src_binding, unreal.MovieScene3DTransformTrack)
    if not src_transform_tracks:
        unreal.log_warning("No MovieScene3DTransformTrack found on source binding.")
        return False

    src_track = src_transform_tracks[0]

    # Editor subsystem that provides copy/paste track operations
    seq_subsys = unreal.get_editor_subsystem(unreal.LevelSequenceEditorSubsystem)

    # Optionally remove existing transform track(s) on destination
    if replace_existing:
        dst_transform_tracks = unreal.MovieSceneBindingExtensions.find_tracks_by_type(
            dst_binding, unreal.MovieScene3DTransformTrack)
        for t in dst_transform_tracks:
            unreal.MovieSceneBindingExtensions.remove_track(dst_binding, t)

    # Wrap in an editor transaction so operation is undoable in one step
    with unreal.ScopedEditorTransaction("Copy Transform Track"):
        # Copy the source track(s) to exported text
        try:
            exported_text = seq_subsys.copy_tracks([src_track])
        except Exception as e:
            unreal.log_error(f"copy_tracks failed: {e}")
            return False

        # Prepare paste params: paste into the destination sequence and binding
        paste_params = unreal.MovieScenePasteTracksParams()
        # sequence field controls which sequence will own the pasted tracks
        paste_params.sequence = dst_binding.sequence
        paste_params.bindings = [dst_binding]

        try:
            out_tracks = seq_subsys.paste_tracks(exported_text, paste_params)
        except Exception as e:
            unreal.log_error(f"paste_tracks failed: {e}")
            return False

    if out_tracks:
        unreal.log("Successfully pasted transform track(s) onto destination binding.")
        return True
    else:
        unreal.log_warning("paste_tracks returned no tracks (nothing pasted).")
        return False


def find_folder_for_binding(level_sequence: unreal.LevelSequence, binding):
    # movie_scene = level_sequence.get_movie_scene()
    # level_sequence: unreal.MovieSceneSequence
    roots = level_sequence.get_root_folders_in_sequence()

    def recurse(folder):
        if binding in unreal.MovieSceneFolderExtensions.get_child_object_bindings(folder):
            return folder
        for child in unreal.MovieSceneFolderExtensions.get_child_folders(folder):
            found = recurse(child)
            if found:
                return found
        return None

    for root in roots:
        found = recurse(root)
        if found:
            return found
    return None


def disable_all_tracks_for_binding(binding, level_sequence=None, use_local=False):
    """
    Disable evaluation for every track on `binding` (MovieSceneBindingProxy).

    - binding: unreal.MovieSceneBindingProxy
    - level_sequence: optional unreal.LevelSequence to mark dirty (if provided)
    - use_local: if True, prefer set_local_eval_disabled (editor-only, NOT serialized).
                 If False, prefer set_eval_disabled (serialized) when available.
    Returns: dict with counts: {'tracks_handled':n, 'used_track_api':m, 'sections_disabled':k}
    """
    tracks = binding.get_tracks() or []
    if not tracks:
        return {'tracks_handled': 0, 'used_track_api': 0, 'sections_disabled': 0}

    tracks_handled = 0
    used_track_api = 0
    sections_disabled = 0

    with unreal.ScopedEditorTransaction("Disable Binding Tracks"):
        for tr in tracks:
            tracks_handled += 1
            tr.modify()
            # prefer local if requested (editor-only, not serialized)
            if use_local and callable(getattr(tr, "set_local_eval_disabled", None)):
                tr.set_local_eval_disabled(True)
                used_track_api += 1
                continue

            # prefer the persistent/serialized API
            if callable(getattr(tr, "set_eval_disabled", None)):
                tr.set_eval_disabled(True)
                used_track_api += 1
                continue

            # fallback: disable all sections (works on all versions)
            for sec in tr.get_sections():
                sec.modify()
                sec.set_is_active(False)
                sections_disabled += 1

    # mark sequence dirty so editor can save the change
    if level_sequence:
        try:
            level_sequence.mark_package_dirty()
        except Exception:
            pass
    else:
        # best-effort: try to find an owning sequence and mark dirty
        try:
            outer = tracks[0].get_outer()  # often UMovieScene
            if hasattr(outer, "get_outer"):
                seq = outer.get_outer()
                if seq and hasattr(seq, "mark_package_dirty"):
                    seq.mark_package_dirty()
        except Exception:
            pass

    return {
        'tracks_handled': tracks_handled,
        'used_track_api': used_track_api,
        'sections_disabled': sections_disabled
    }


def mute_all_tracks_for_binding(binding, level_sequence=None, mute=True):
    """
    Mute or unmute all tracks on `binding` (MovieSceneBindingProxy).

    - binding: unreal.MovieSceneBindingProxy
    - level_sequence: optional unreal.LevelSequence to mark dirty (if provided)
    - mute: True to mute, False to unmute
    Returns: dict {'tracks_handled':n, 'track_api_used':m, 'sections_toggled':k, 'fallback_used':bool}
    """
    tracks = binding.get_tracks() or []
    if not tracks:
        return {'tracks_handled': 0, 'track_api_used': 0, 'sections_toggled': 0, 'fallback_used': False}

    tracks_handled = 0
    track_api_used = 0
    sections_toggled = 0
    fallback_used = False

    txn_name = "Mute Binding Tracks" if mute else "Unmute Binding Tracks"
    with unreal.ScopedEditorTransaction(txn_name):
        for tr in tracks:
            tracks_handled += 1
            tr.modify()

            # Prefer the explicit mute API if present
            mute_fn = getattr(tr, "set_is_muted", None) or getattr(tr, "set_mute", None) \
                or getattr(tr, "set_local_is_muted", None) or getattr(tr, "set_local_mute", None)
            if callable(mute_fn):
                mute_fn(mute)
                track_api_used += 1
                continue

            # If no mute API is available, fall back to toggling sections.
            # NOTE: toggling sections is NOT the same as muting the track, but prevents evaluation.
            fallback_used = True
            for sec in tr.get_sections():
                sections_toggled += 1
                sec.modify()
                sec.set_is_active(not mute)

    # mark sequence dirty so editor can save the change
    if level_sequence:
        try:
            level_sequence.mark_package_dirty()
        except Exception:
            pass
    else:
        # best-effort try to find owning sequence and mark dirty
        try:
            outer = tracks[0].get_outer()
            if hasattr(outer, "get_outer"):
                seq = outer.get_outer()
                if seq and hasattr(seq, "mark_package_dirty"):
                    seq.mark_package_dirty()
        except Exception:
            pass

    return {
        'tracks_handled': tracks_handled,
        'track_api_used': track_api_used,
        'sections_toggled': sections_toggled,
        'fallback_used': fallback_used
    }


def ui_mute_all_tracks_for_binding(binding, mute=True):
    """
    Mute/unmute tracks using the editor-local flag so the Sequencer UI can toggle them.
    - binding: unreal.MovieSceneBindingProxy
    - mute: True to mute, False to unmute
    Returns number of tracks updated.
    """
    tracks = binding.get_tracks() or []
    if not tracks:
        return 0

    updated = 0
    txn = "UI Mute Tracks" if mute else "UI Unmute Tracks"
    with unreal.ScopedEditorTransaction(txn):
        for tr in tracks:
            tr.modify()
            # Preferred: editor-local mute used by Sequencer UI
            local_fn = getattr(tr, "set_local_eval_disabled", None)
            if callable(local_fn):
                local_fn(mute)
                updated += 1
                continue

            # Some builds may expose a direct mute API (use it if present)
            mute_fn = getattr(tr, "set_is_muted", None) or getattr(tr, "set_mute", None)
            if callable(mute_fn):
                mute_fn(mute)
                updated += 1
                continue

            # Last-resort fallback: disable sections (NOT the same as UI mute;
            # UI may not let you re-toggle if you hit this path)
            for sec in tr.get_sections():
                sec.modify()
                sec.set_is_active(not mute)
                updated += 1

    return updated


def enable_all_selected_bindings(mark_dirty=True):
    """
    Enable / unmute / re-activate all tracks on the bindings that are currently selected in Sequencer.
    Returns a summary dict.
    """
    LSEdit = unreal.LevelSequenceEditorBlueprintLibrary
    selected_bindings = LSEdit.get_selected_bindings() or []

    if not selected_bindings:
        unreal.log_warning("No bindings selected in Sequencer.")
        return {'bindings_processed': 0, 'tracks_touched': 0}

    bindings_processed = 0
    tracks_touched = 0

    # Try to get the focused/active sequence for marking dirty if needed
    current_seq = None
    try:
        current_seq = LSEdit.get_current_level_sequence()
    except Exception:
        current_seq = None

    with unreal.ScopedEditorTransaction("Enable Selected Binding Tracks"):
        for binding in selected_bindings:
            # defensive: binding may be a MovieSceneBindingProxy
            try:
                tracks = binding.get_tracks() or []
            except Exception:
                # Some builds expose different wrappers — try to get tracks via the sequence/movie scene:
                tracks = []
                try:
                    seq = getattr(binding, "sequence", None)
                    if seq:
                        # find tracks for this binding via sequence/movie scene if possible (best-effort)
                        ms = unreal.MovieSceneSequenceExtensions.get_movie_scene(seq)
                        # There is no single universal accessor here; bail if not found
                except Exception:
                    pass

            if not tracks:
                # nothing to do for this binding
                continue

            bindings_processed += 1
            for tr in tracks:
                tracks_touched += 1
                try:
                    tr.modify()
                except Exception:
                    pass

                # 1) Editor-local eval disable (preferred for UI-friendly behavior if exposed)
                local_fn = getattr(tr, "set_local_eval_disabled", None)
                if callable(local_fn):
                    try:
                        local_fn(False)
                    except Exception:
                        pass

                # 2) persistent eval disable (serialized)
                eval_fn = getattr(tr, "set_eval_disabled", None)
                if callable(eval_fn):
                    try:
                        eval_fn(False)
                    except Exception:
                        pass

                # 3) track-level mute API (unmute)
                mute_fn = getattr(tr, "set_is_muted", None) or getattr(tr, "set_mute", None) \
                    or getattr(tr, "set_local_is_muted", None) or getattr(tr, "set_local_mute", None)
                if callable(mute_fn):
                    try:
                        mute_fn(False)
                    except Exception:
                        pass

                # 4) ensure sections are active (fallback / safety-net)
                try:
                    for sec in tr.get_sections():
                        try:
                            sec.modify()
                        except Exception:
                            pass
                        # set_is_active(True) will re-enable the section's evaluation if it was deactivated
                        if callable(getattr(sec, "set_is_active", None)):
                            try:
                                sec.set_is_active(True)
                            except Exception:
                                pass
                except Exception:
                    # some track wrappers might not expose get_sections; ignore
                    pass

    # mark package dirty if requested
    if mark_dirty:
        seq_to_mark = current_seq
        if not seq_to_mark and selected_bindings:
            # try to find owning sequence via first binding as best-effort
            try:
                seq_to_mark = getattr(selected_bindings[0], "sequence", None)
            except Exception:
                seq_to_mark = None

        if seq_to_mark and hasattr(seq_to_mark, "mark_package_dirty"):
            try:
                seq_to_mark.mark_package_dirty()
            except Exception:
                pass

    return {
        'bindings_processed': bindings_processed,
        'tracks_touched': tracks_touched
    }


def strip_trailing_instance_id(name: str) -> str:
    """
    Remove a trailing '_<digits>' instance suffix.
    Example: 'BP_CAN_Chr_villager_Collins_142' -> 'BP_CAN_Chr_villager_Collins'
    """
    return re.sub(r'_\d+$', '', name)


def delete_binding(binding, mark_dirty=True):
    """
    Delete a single binding from its LevelSequence.

    - binding: a unreal.MovieSceneBindingProxy (the binding object you already have).
               If you pass anything else the function will try to be defensive,
               but the recommended input is the MovieSceneBindingProxy you already hold.
    - mark_dirty: mark the owning LevelSequence dirty so the editor prompts save (default True).

    Returns True if deletion succeeded, False otherwise.
    """
    if binding is None:
        unreal.log_error("delete_binding: binding is None")
        return False

    # Best / canonical path: MovieSceneBindingExtensions.remove(binding)
    remove_fn = getattr(unreal, "MovieSceneBindingExtensions", None)
    if remove_fn is None or not hasattr(unreal.MovieSceneBindingExtensions, "remove"):
        unreal.log_error("delete_binding: MovieSceneBindingExtensions.remove not available in this Python build.")
        unreal.log("If you can provide a MovieSceneBindingProxy object, I can attempt other fallbacks or provide a small C++ editor utility to remove by GUID.")
        return False

    try:
        with unreal.ScopedEditorTransaction("Delete Sequencer Binding"):
            # call the high-level remove API
            unreal.MovieSceneBindingExtensions.remove(binding)

        # mark owning sequence dirty if possible (best-effort)
        if mark_dirty:
            seq = None
            try:
                # many builds expose a 'sequence' attribute on the binding proxy
                seq = getattr(binding, "sequence", None)
            except Exception:
                seq = None

            # fallback: try LevelSequenceEditorBlueprintLibrary current sequence
            if seq is None:
                try:
                    seq = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
                except Exception:
                    seq = None

            if seq and hasattr(seq, "mark_package_dirty"):
                try:
                    seq.mark_package_dirty()
                except Exception:
                    # ignore mark failures
                    pass

        unreal.log("delete_binding: removed binding.")
        return True

    except Exception as exc:
        unreal.log_error("delete_binding: failed to remove binding: {}".format(exc))
        return False


def duplicate_and_open_current_level_sequence(suffix="_copy"):
    """
    Duplicate the currently opened Level Sequence and open the duplicated copy.
    Returns (duplicated_sequence_asset, error_message) where duplicated_sequence_asset is
    the loaded asset object on success, or None on failure. error_message is None on success.
    """
    LSEdit = unreal.LevelSequenceEditorBlueprintLibrary
    try:
        src_seq = LSEdit.get_current_level_sequence()
    except Exception as e:
        unreal.log_error("Failed to get current level sequence: {}".format(e))
        return None, "no_current_sequence"

    if not src_seq:
        unreal.log_warning("No level sequence currently open in Sequencer.")
        return None, "no_current_sequence"

    # Try to resolve a usable source path and name
    src_path_name = None
    src_package_path = None
    src_asset_name = None

    # Attempt #1: get_path_name() -> usually returns "/Game/.../Asset.Asset"
    try:
        src_path_name = src_seq.get_path_name()
    except Exception:
        src_path_name = None

    # Attempt #2: fallback to str(src_seq) or get_name/outer
    if not src_path_name:
        try:
            src_path_name = str(src_seq)
        except Exception:
            src_path_name = None

    # Parse package path and asset name
    if src_path_name and "." in src_path_name:
        pkg, name = src_path_name.rsplit(".", 1)
        src_package_path = pkg  # "/Game/Folder/Asset"
        src_asset_name = name
    else:
        # Try other accessors
        try:
            src_asset_name = src_seq.get_name()
        except Exception:
            src_asset_name = None
        try:
            outer = src_seq.get_outer()
            if outer:
                src_package_path = outer.get_path_name()
        except Exception:
            src_package_path = None

    if not src_package_path or not src_asset_name:
        # try EditorAssetLibrary helper
        try:
            src_package_path = unreal.EditorAssetLibrary.get_path_name_for_loaded_asset(src_seq)
            if src_package_path and "." in src_package_path:
                pkg, name = src_package_path.rsplit(".", 1)
                src_package_path = pkg
                src_asset_name = name
        except Exception:
            pass

    if not src_package_path or not src_asset_name:
        unreal.log_error("Could not determine source asset package/name for the opened Level Sequence.")
        return None, "could_not_resolve_source_path"

    # Build base package and request a unique name using AssetTools
    asset_tools = None
    try:
        asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    except Exception:
        asset_tools = None

    desired_suffix = suffix
    unique_name = None
    unique_package = None

    if asset_tools:
        try:
            # create_unique_asset_name often expects something like "/Game/Folder/Asset"
            base_package = src_package_path + "/" + src_asset_name
            unique_name, unique_package = asset_tools.create_unique_asset_name(base_package, desired_suffix)
            # create_unique_asset_name returns (unique_name, unique_package_path)
            # unique_name may include path; ensure we have both parts
            if unique_package and "." in unique_package:
                # if package contains dot, split; else ignore
                pass
        except Exception:
            unique_name = None
            unique_package = None

    # Final fallback: append timestamp to name in same folder
    if not unique_name or not unique_package:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        new_name = "{}{}_{:s}".format(src_asset_name, desired_suffix, ts)
        unique_package = src_package_path + "/" + new_name
        unique_name = new_name

    # Build fully qualified source and destination object paths for duplicate_asset
    # some duplicate implementations expect "Package.Asset" form
    src_obj_path = "{0}.{1}".format(src_package_path + "/" + src_asset_name, src_asset_name)
    dest_obj_path = "{0}.{1}".format(unique_package, unique_name)  # unique_package already contains folder + name

    # Try duplication
    duplicated = None
    try:
        # EditorAssetLibrary.duplicate_asset expects source and destination package paths ("/Game/.../Name")
        # Some builds accept either "Package.Asset" or "/Game/Folder/Asset". We'll try both.
        try:
            duplicated_path = unreal.EditorAssetLibrary.duplicate_asset(src_package_path + "/" + src_asset_name,
                                                                        unique_package)
            # duplicate_asset returns the path to the duplicated asset (or None)
            if duplicated_path:
                duplicated = unreal.EditorAssetLibrary.load_asset(duplicated_path)
        except Exception:
            # fallback: try object-path form
            duplicated_path = unreal.EditorAssetLibrary.duplicate_asset(src_obj_path, dest_obj_path)
            if duplicated_path:
                # duplicated_path may be package path or object path; try to load
                try:
                    duplicated = unreal.EditorAssetLibrary.load_asset(duplicated_path)
                except Exception:
                    # try load by package/name
                    try:
                        duplicated = unreal.EditorAssetLibrary.load_asset(unique_package)
                    except Exception:
                        duplicated = None
    except Exception as e:
        unreal.log_error("Failed to duplicate Level Sequence: {}".format(e))
        return None, "duplicate_failed"

    if not duplicated:
        # last-ditch: try to load by package path
        try:
            duplicated = unreal.EditorAssetLibrary.load_asset(unique_package)
        except Exception:
            duplicated = None

    if not duplicated:
        unreal.log_error("Duplication appears to have failed: could not load duplicated asset at '{}'".format(unique_package))
        return None, "dup_load_failed"

    # Open the duplicated sequence in Sequencer
    opened = False
    try:
        # preferred: LevelSequenceEditorBlueprintLibrary.open_level_sequence
        if hasattr(unreal.LevelSequenceEditorBlueprintLibrary, "open_level_sequence"):
            try:
                unreal.LevelSequenceEditorBlueprintLibrary.open_level_sequence(duplicated)
                opened = True
            except Exception:
                opened = False

        # fallback: use AssetEditorSubsystem to open the asset
        if not opened:
            try:
                asset_editor = unreal.AssetEditorSubsystem()
                asset_editor.open_editor_for_assets([duplicated])
                opened = True
            except Exception:
                opened = False
    except Exception:
        opened = False

    if not opened:
        unreal.log_warning("Duplicated sequence created but could not be opened automatically. Asset path: {}".format(unique_package))

    # mark the duplicated package dirty (so it appears as modified if you want)
    try:
        if duplicated and hasattr(duplicated, "mark_package_dirty"):
            duplicated.mark_package_dirty()
    except Exception:
        pass

    unreal.log("Duplicate created: '{}' and opened: {}".format(unique_package, opened))
    return duplicated, None
