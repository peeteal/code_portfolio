import unreal


def get_level_sequence_actor() -> unreal.LevelSequenceActor:
    for actor in unreal.EditorLevelLibrary.get_all_level_actors():
        if isinstance(actor, unreal.LevelSequenceActor):
            return actor


@unreal.uclass()
class UPPTurntableLibrary(unreal.BlueprintFunctionLibrary):

    @unreal.ufunction(static=True)
    def duplicate_turntable_level():
        # unreal.EditorAssetLibrary.delete_asset(
        #     "/UPPTechviz/Tools/Rendering/TurntableSystemV3/temp_turntable_level")
        original_path = "/Script/Engine.World'/UPPTechviz/Tools/Rendering/TurntableSystemV3/template/Turntable_Showroom_Template.Turntable_Showroom_Template'"
        original_level = unreal.load_asset(original_path)

        new_level = unreal.EditorAssetLibrary.duplicate_loaded_asset(
            original_level, "/UPPTechviz/Tools/Rendering/TurntableSystemV3/temp_turntable_level")
        unreal.EditorAssetLibrary.save_loaded_asset(new_level)

    @unreal.ufunction(static=True, ret=unreal.LevelSequence)
    def open_turntable_level_sequence():
        # Load the original asset
        unreal.EditorAssetLibrary.delete_asset(
            "/UPPTechviz/Tools/Rendering/TurntableSystemV3/temp_turntable_seq")
        original_path = "/Script/LevelSequence.LevelSequence'/UPPTechviz/Tools/Rendering/TurntableSystemV3/template/turntable_seq_template.turntable_seq_template'"
        original_sequence = unreal.load_asset(original_path)

        new_sequence = unreal.EditorAssetLibrary.duplicate_loaded_asset(
            original_sequence, "/UPPTechviz/Tools/Rendering/TurntableSystemV3/temp_turntable_seq")

        unreal.LevelSequenceEditorBlueprintLibrary.open_level_sequence(
            new_sequence)
        return new_sequence

    @unreal.ufunction(static=True, params=[str, str, bool], ret=unreal.LevelSequence)
    def open_turntable_level_sequence_at_asset(sequence_path: str, path: str, open: bool = True):
        # Load the original asset

        if unreal.EditorAssetLibrary.does_asset_exist(path):
            unreal.EditorAssetLibrary.delete_asset(path)

        # unreal.EditorAssetLibrary.delete_asset(
        #     "/UPPTechviz/Tools/Rendering/TurntableSystemV3/temp_turntable_seq")
        # original_path = sequence_path
        if sequence_path is None or sequence_path == "":
            sequence_path = "/Script/LevelSequence.LevelSequence'/UPPTechviz/Tools/Rendering/TurntableSystemV3/template/turntable_seq_template.turntable_seq_template'"

        original_sequence = unreal.load_asset(sequence_path)

        new_sequence = unreal.EditorAssetLibrary.duplicate_loaded_asset(
            original_sequence, path)

        if open:
            unreal.LevelSequenceEditorBlueprintLibrary.open_level_sequence(
                new_sequence)
        return new_sequence

    @unreal.ufunction(static=True)
    def delete_turntable_level_sequence():
        if unreal.EditorAssetLibrary.does_asset_exist("/UPPTechviz/Tools/Rendering/TurntableSystemV3/temp_turntable_seq"):
            unreal.EditorAssetLibrary.delete_asset(
                "/UPPTechviz/Tools/Rendering/TurntableSystemV3/temp_turntable_seq")

    @unreal.ufunction(static=True)
    def delete_turntable_level():
        unreal.EditorAssetLibrary.delete_asset(
            "/UPPTechviz/Tools/Rendering/TurntableSystemV3/temp_turntable_level")

    @unreal.ufunction(static=True, params=[str, bool])
    def replace_target_actor(preview_asset_path: str, spawnable: bool):
        seq: unreal.LevelSequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
        seq_actor = get_level_sequence_actor()
        bindings = unreal.MovieSceneSequenceExtensions.get_bindings(seq)

        t_binding = None
        for binding in bindings:
            if binding.get_display_name() == "TargetActor":
                t_binding = binding
                break
        if t_binding is not None:
            # Detach proxy binding and attach to the actor to keep the target actor sequence properties.
            loaded_asset = unreal.load_asset(preview_asset_path)

            sp_actor = unreal.EditorLevelLibrary.spawn_actor_from_object(
                loaded_asset, unreal.Vector())

            ls_system: unreal.LevelSequenceEditorSubsystem
            ls_system = unreal.get_editor_subsystem(
                unreal.LevelSequenceEditorSubsystem)
            bind = ls_system.add_actors([sp_actor])[0]

            bind_actor = ls_system.convert_to_spawnable(bind)

            a_binding = bind_actor.pop(0)
            id = unreal.MovieSceneSequenceExtensions.get_binding_id(
                seq, a_binding)
            old_id = seq.get_binding_id(t_binding)
            t_binding.set_spawnable_binding_id(id)

            a_binding.remove_track(a_binding.find_tracks_by_type(
                unreal.MovieScene3DTransformTrack).pop(0))
            t_binding.remove_track(t_binding.find_tracks_by_type(
                unreal.MovieScene3DTransformTrack).pop(0))

            to_remove = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(old_id)[
                0]
            unreal.EditorLevelLibrary.destroy_actor(to_remove)

            if not spawnable:
                ls_system.convert_to_possessable(a_binding)
                ls_system.convert_to_possessable(t_binding)

        unreal.LevelSequenceEditorBlueprintLibrary.refresh_current_level_sequence()

    @unreal.ufunction(static=True, params=[unreal.LevelSequence, unreal.MovieSceneBindingProxy, str, bool])
    def spawn_reference_object(seq: unreal.LevelSequence, camera_bind: unreal.MovieSceneBindingProxy, preview_asset_path: str, spawnable: bool):
        # seq: unreal.LevelSequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
        bindings = unreal.MovieSceneSequenceExtensions.get_bindings(seq)
        # for bind in bindings:
        #     if bind == camera_bind:
        #         print("cam binding found smh")
        # Detach proxy binding and attach to the actor to keep the target actor sequence properties.
        loaded_asset = unreal.load_asset(preview_asset_path)
        sp_actor = unreal.EditorLevelLibrary.spawn_actor_from_object(
            loaded_asset, unreal.Vector())
        sp_actor.root_component.set_mobility(unreal.ComponentMobility.MOVABLE)
        ls_system: unreal.LevelSequenceEditorSubsystem
        ls_system = unreal.get_editor_subsystem(
            unreal.LevelSequenceEditorSubsystem)
        bind: unreal.MovieSceneBindingProxy = ls_system.add_actors([sp_actor])[
            0]

        id = unreal.MovieSceneSequenceExtensions.get_binding_id(seq, bind)

        tracks = camera_bind.find_tracks_by_type(
            unreal.MovieSceneActorReferenceTrack)

        track: unreal.MovieSceneActorReferenceTrack = None
        if len(tracks) > 0:
            track = tracks[0]
        else:
            track = camera_bind.add_track(unreal.MovieSceneActorReferenceTrack)

        track.set_property_name_and_path("ReferenceObject", "ReferenceObject")

        sect: unreal.MovieSceneSection
        sects = track.get_sections()
        if len(sects) >= 1:
            sect = sects[0]
        else:
            sect = track.add_section()

        sect.set_start_frame_bounded(False)
        sect.set_end_frame_bounded(False)

        channel: unreal.MovieSceneScriptingActorReferenceChannel = sect.get_all_channels()[
            0]
        channel.set_default(seq.get_binding_id(bind))

        if spawnable:
            ls_system.convert_to_spawnable(bind)
        else:
            ls_system.convert_to_possessable(bind)

        # TODO: Check changed
        bind.remove_track(bind.find_tracks_by_type(
            unreal.MovieScene3DTransformTrack).pop(0))

        unreal.LevelSequenceEditorBlueprintLibrary.refresh_current_level_sequence()

    @unreal.ufunction(static=True, params=[unreal.MovieSceneBindingProxy])
    def convert_to_possessable(cam_binding: unreal.MovieSceneBindingProxy):
        subsys = unreal.LevelSequenceEditorSubsystem()
        subsys.convert_to_possessable(cam_binding)
        unreal.LevelSequenceEditorBlueprintLibrary.refresh_current_level_sequence()

    @unreal.ufunction(static=True, params=[unreal.LevelSequence], ret=unreal.TurntableCineCamera)
    def get_turntable_camera(seq: unreal.LevelSequence):
        # if seq is None:
        # seq = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
        for bind in seq.get_bindings():
            if bind.get_object_template() is not None:
                if isinstance(bind.get_object_template(),  unreal.TurntableCineCamera):
                    cam = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(
                        seq.get_binding_id(bind))[0]
                    return cam
        print("cam not found")
        return None

    # If seq == None, returns from this sequence
    @unreal.ufunction(static=True, params=[unreal.LevelSequence], ret=unreal.MovieSceneBindingProxy)
    def get_turntable_camera_binding(seq: unreal.LevelSequence):
        if seq is None:
            seq = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
        for bind in seq.get_bindings():
            if bind.get_object_template() is not None:
                if isinstance(bind.get_object_template(),  unreal.TurntableCineCamera):
                    print("cam binding found")
                    return bind
        print("cam not found")
        return unreal.MovieSceneBindingProxy()

    @unreal.ufunction(static=True, params=[unreal.MovieSceneBindingProxy], ret=unreal.Object)
    def get_object_from_binding(bind: unreal.MovieSceneBindingProxy):
        seq: unreal.LevelSequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
        id = seq.get_binding_id(bind)
        obj = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(id)[
            0]
        return obj

    @unreal.ufunction(static=True, params=[unreal.LevelSequence], ret=unreal.Object)
    def get_actor_from_binding_with_grid_tag(sequence: unreal.LevelSequence):
        bindings = sequence.get_bindings()
        for binding in bindings:
            binding: unreal.MovieSceneBindingProxy
            if binding.get_name() == "ground" or binding.get_name() == "BP_ground":
                binding_id = sequence.get_binding_id(binding)
                found = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(binding_id)[
                    0]

        if found:
            unreal.log(
                f"Found actor from a binding with 'Grid' tag: {found.get_name()}")
            return found

        unreal.log_warning("No actor with 'Grid' tag found.")
        return None

    @unreal.ufunction(static=True, params=[unreal.MovieSceneBindingProxy, unreal.LevelSequence])
    def ensure_transform_track_for_binding(
        binding: unreal.MovieSceneBindingProxy,
        level_sequence: unreal.LevelSequence = None,
    ):
        """
        Create or reuse a 3D transform track on the given binding,
        then create or reuse a transform section and return it.
        """

        if not binding or not unreal.MovieSceneBindingExtensions.is_valid(binding):
            raise ValueError("Invalid MovieSceneBindingProxy")

        if level_sequence is None:
            level_sequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()

        # Add or reuse the transform track
        tracks = unreal.MovieSceneBindingExtensions.find_tracks_by_type(
            binding, unreal.MovieScene3DTransformTrack
        )
        if tracks:
            transform_track = tracks[0]
        else:
            transform_track = unreal.MovieSceneBindingExtensions.add_track(
                binding, unreal.MovieScene3DTransformTrack
            )

        if not transform_track:
            raise RuntimeError("Failed to create MovieScene3DTransformTrack")

        id = level_sequence.get_binding_id(binding)
        obj: unreal.Actor = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(id)[
            0]
        actor_location = obj.get_actor_location()
        actor_rotation = obj.get_actor_rotation()
        actor_scale = obj.get_actor_scale3d()

        # Add or reuse a section
        sections = transform_track.get_sections()
        if sections:
            transform_section = sections[0]
        else:
            transform_section = transform_track.add_section()

        if not transform_section:
            raise RuntimeError("Failed to create MovieScene3DTransformSection")

        # Set the section to cover the current sequence playback range
        if level_sequence:
            # start_frame = level_sequence.get_playback_start()
            # end_frame = level_sequence.get_playback_end()
            # transform_section.set_range(start_frame, end_frame)
            transform_section.set_start_frame_bounded(False)
            transform_section.set_end_frame_bounded(False)

        channels = transform_section.get_all_channels()
        channels[0].set_default(actor_location.x)
        channels[1].set_default(actor_location.y)
        channels[2].set_default(actor_location.z)
        channels[3].set_default(actor_rotation.roll)
        channels[4].set_default(actor_rotation.pitch)
        channels[5].set_default(actor_rotation.yaw)
        channels[6].set_default(actor_scale.x)
        channels[7].set_default(actor_scale.y)
        channels[8].set_default(actor_scale.z)

        unreal.LevelSequenceEditorBlueprintLibrary.refresh_current_level_sequence()
        # return transform_section

    @unreal.ufunction(static=True, params=[unreal.LevelSequence], ret=unreal.MovieSceneBindingProxy)
    def get_binding_with_grid_tag(sequence: unreal.LevelSequence):
        bindings = sequence.get_bindings()
        for binding in bindings:
            binding: unreal.MovieSceneBindingProxy
            if binding.get_name() == "ground" or binding.get_name() == "BP_ground":
                found = binding

        if found:
            unreal.log(f"Found binding with 'Grid' tag: {found.get_name()}")
            return found

        unreal.log_warning("No binding with 'Grid' tag found.")
        return None

    @unreal.ufunction(static=True, ret=unreal.Object)
    def get_actor_with_grid_tag():
        # def get_actor_with_grid_tag(sequence: unreal.LevelSequence):
        # Get all actors in the current level
        all_actors = unreal.EditorLevelLibrary.get_all_level_actors()
        found = None
        # Filter actors by tag
        for actor in all_actors:
            if 'Grid' in actor.tags or actor.get_name() == "ground" or actor.get_name() == "BP_ground":
                found = actor
        if found:
            unreal.log(f"Found actor with 'Grid' tag: {found.get_name()}")
            return found

        sequence: unreal.MovieSceneSequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
        # sequence: unreal.MovieSceneSequence
        bindings = sequence.get_bindings()
        for binding in bindings:
            binding: unreal.MovieSceneBindingProxy
            if binding.get_name() == "ground" or binding.get_name() == "BP_ground":
                binding_id = sequence.get_binding_id(binding)
                found = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(binding_id)[
                    0]

        if found:
            unreal.log(f"Found binding with 'Grid' tag: {found.get_name()}")
            return found

        unreal.log_warning("No actor with 'Grid' tag found.")
        return None

    @unreal.ufunction(
        static=True,
        params=[unreal.Array(unreal.LevelSequence), str, str, bool],
        ret=unreal.Array(unreal.MoviePipelineExecutorJob)
    )
    def enqueue_turntable_renders(
        level_sequences: list[unreal.LevelSequence],
        map_path: str,
        graph_config_path: str,
        clear_queue: bool = True
    ):
        """
        Adds one MRQ job per Level Sequence.

        level_sequences:
            Array of LevelSequence assets

        graph_config_path:
            /Game/... MovieGraphConfig asset path

        clear_queue:
            If true, clears existing queue before adding jobs
        """

        created_jobs = []

        # Load graph config
        graph_cfg = unreal.load_asset(graph_config_path)

        if not graph_cfg:
            unreal.log_error(
                f"Could not load graph config: {graph_config_path}"
            )
            return created_jobs

        if not isinstance(graph_cfg, unreal.MovieGraphConfig):
            unreal.log_error(
                f"Asset is not a MovieGraphConfig: {graph_config_path}"
            )
            return created_jobs

        # MRQ subsystem
        queue_subsystem = unreal.get_editor_subsystem(
            unreal.MoviePipelineQueueSubsystem
        )

        if not queue_subsystem:
            unreal.log_error("MoviePipelineQueueSubsystem unavailable.")
            return created_jobs

        queue = queue_subsystem.get_queue()

        if clear_queue:
            queue.delete_all_jobs()

        # Create one job per sequence
        for seq in level_sequences:
            if not seq:
                continue

            if not isinstance(seq, unreal.LevelSequence):
                unreal.log_warning(
                    f"Item is not a LevelSequence: {seq}"
                )
                continue

            job = queue.allocate_new_job()

            job.sequence = unreal.SoftObjectPath(seq.get_path_name())
            job.job_name = seq.get_name()
            job.map = unreal.SoftObjectPath(map_path)
            job.set_graph_preset(graph_cfg)

            created_jobs.append(job)

            unreal.log(f"Added MRQ Job: {job.job_name}")

        queue.set_is_dirty(True)

        unreal.log(
            f"Added {len(created_jobs)} jobs to Movie Render Queue."
        )

        return created_jobs

    @unreal.ufunction(static=True, params=[str], ret=unreal.MoviePipelineExecutorJob)
    def load_queue_then_enqueue(sequence_path: str):
        """
        Example variant if you want to load a saved queue asset first.
        """
        queue_subsystem = unreal.get_editor_subsystem(
            unreal.MoviePipelineQueueSubsystem)
        queue = queue_subsystem.get_queue()
        # Then use the same allocate_new_job / sequence / config flow above.
        return None

    @unreal.ufunction(
        static=True,
        params=[unreal.Actor, unreal.MovieSceneBindingProxy,
                unreal.LevelSequence],
        ret=unreal.MovieSceneBindingProxy
    )
    def sync_turntable_camera_defaults_to_binding(
        actor: unreal.Actor,
        binding: unreal.MovieSceneBindingProxy,
        level_sequence: unreal.LevelSequence = None,
    ):
        print("sync start")

        if not actor or not isinstance(actor, unreal.Actor):
            raise ValueError("actor must be a valid Actor")

        if level_sequence is None:
            level_sequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()

        if not level_sequence:
            raise RuntimeError("No current LevelSequence is open.")

        def _normalize_name(value):
            if value is None:
                return ""
            return (
                str(value)
                .replace(" ", "")
                .replace("_", "")
                .replace("-", "")
                .lower()
            )

        def _track_name_candidates(prop_name: str):
            # Accept both the raw property name and a spaced/display-style variant.
            spaced = []
            if prop_name:
                spaced = [prop_name.replace("Camera", "Camera "), prop_name.replace(
                    "Reference", "Reference ")]
            return {_normalize_name(prop_name), *(_normalize_name(x) for x in spaced if x)}

        def _track_matches_property(track, prop_name: str):
            wanted = _track_name_candidates(prop_name)

            candidates = []

            try:
                candidates.append(track.get_editor_property("property_name"))
            except Exception:
                pass

            try:
                candidates.append(track.get_editor_property("property_path"))
            except Exception:
                pass

            try:
                candidates.append(track.get_display_name())
            except Exception:
                pass

            try:
                candidates.append(track.get_name())
            except Exception:
                pass

            for c in candidates:
                if _normalize_name(c) in wanted:
                    return True

            return False

        def _ensure_track(binding_proxy, track_cls, prop_name=None):
            tracks = binding_proxy.find_tracks_by_type(track_cls)

            if prop_name is not None:
                for track in tracks:
                    if _track_matches_property(track, prop_name):
                        print(
                            f"[track] Reusing track: {track_cls.__name__} / {prop_name}")
                        return track

            if tracks:
                print(
                    f"[track] Reusing first track of type: {track_cls.__name__}")
                return tracks[0]

            print(f"[track] Creating track: {track_cls.__name__}")
            track = binding_proxy.add_track(track_cls)
            if prop_name is not None:
                try:
                    track.set_property_name_and_path(prop_name, prop_name)
                except Exception:
                    pass
            return track

        def _ensure_section(track):
            sections = track.get_sections()
            if sections:
                print(
                    f"[section] Reusing section on track: {track.get_name()}")
                return sections[0]
            print(f"[section] Creating section on track: {track.get_name()}")
            return track.add_section()

        def _set_single_channel_default(section, value, label=""):
            channels = section.get_all_channels()
            if not channels:
                raise RuntimeError("Section has no channels.")
            ch = channels[0]
            if not hasattr(ch, "set_default"):
                raise RuntimeError("Channel does not support set_default().")

            print(f"[write] {label} default = {value}")
            ch.set_default(value)

            try:
                readback = ch.get_default()
                print(f"[read ] {label} default = {readback}")
            except Exception as e:
                print(f"[read ] {label} default readback failed: {e}")

        def _set_vector3_defaults(section, vec, label=""):
            channels = section.get_all_channels()
            if len(channels) < 3:
                raise RuntimeError(
                    "Vector section does not expose 3 channels.")

            print(f"[write] {label} default = ({vec.x}, {vec.y}, {vec.z})")
            channels[0].set_default(vec.x)
            channels[1].set_default(vec.y)
            channels[2].set_default(vec.z)

            try:
                print(
                    f"[read ] {label} default x = {channels[0].get_default()}")
                print(
                    f"[read ] {label} default y = {channels[1].get_default()}")
                print(
                    f"[read ] {label} default z = {channels[2].get_default()}")
            except Exception as e:
                print(f"[read ] {label} vector default readback failed: {e}")

        def _set_transform_defaults(section, xform: unreal.Transform):
            channels = section.get_all_channels()
            if len(channels) < 9:
                raise RuntimeError(
                    "Transform section does not expose 9 channels.")

            rot = xform.rotation.euler()

            print(
                "[write] Transform default = "
                f"T({xform.translation.x}, {xform.translation.y}, {xform.translation.z}) "
                f"R({rot.x}, {rot.y}, {rot.z}) "
                f"S({xform.scale3d.x}, {xform.scale3d.y}, {xform.scale3d.z})"
            )

            channels[0].set_default(xform.translation.x)
            channels[1].set_default(xform.translation.y)
            channels[2].set_default(xform.translation.z)

            channels[3].set_default(rot.x)
            channels[4].set_default(rot.y)
            channels[5].set_default(rot.z)

            channels[6].set_default(xform.scale3d.x)
            channels[7].set_default(xform.scale3d.y)
            channels[8].set_default(xform.scale3d.z)

            try:
                print(f"[read ] Transform T.x = {channels[0].get_default()}")
                print(f"[read ] Transform T.y = {channels[1].get_default()}")
                print(f"[read ] Transform T.z = {channels[2].get_default()}")
                print(f"[read ] Transform R.x = {channels[3].get_default()}")
                print(f"[read ] Transform R.y = {channels[4].get_default()}")
                print(f"[read ] Transform R.z = {channels[5].get_default()}")
                print(f"[read ] Transform S.x = {channels[6].get_default()}")
                print(f"[read ] Transform S.y = {channels[7].get_default()}")
                print(f"[read ] Transform S.z = {channels[8].get_default()}")
            except Exception as e:
                print(f"[read ] Transform default readback failed: {e}")

        def _sync_float(prop_name: str):
            value = float(actor.get_editor_property(prop_name))
            print(f"[prop ] float {prop_name} = {value}")
            track = _ensure_track(
                binding, unreal.MovieSceneFloatTrack, prop_name)
            track.set_property_name_and_path(prop_name, prop_name)
            section = _ensure_section(track)
            _set_single_channel_default(section, value, prop_name)

        def _sync_bool(prop_name: str):
            value = bool(actor.get_editor_property(prop_name))
            print(f"[prop ] bool {prop_name} = {value}")
            track = _ensure_track(
                binding, unreal.MovieSceneBoolTrack, prop_name)
            track.set_property_name_and_path(prop_name, prop_name)
            section = _ensure_section(track)
            _set_single_channel_default(section, value, prop_name)

        # def _sync_vector(prop_name: str):
        #     value = actor.get_editor_property(prop_name)
        #     print(f"[prop ] vector {prop_name} = ({value.x}, {value.y}, {value.z})")

        #     track = _ensure_track(binding, unreal.MovieSceneFloatVectorTrack, prop_name)
        #     unreal.MovieSceneFloatVectorTrackExtensions.set_num_channels_used(track, 3)
        #     track.set_property_name_and_path(prop_name, prop_name)

        #     section = _ensure_section(track)
        #     _set_vector3_defaults(section, value, prop_name)

        def _sync_vector(prop_name: str):
            vec = actor.get_editor_property(prop_name)

            x = float(vec.x)
            y = float(vec.y)
            z = float(vec.z)

            print(f"[prop ] vector {prop_name} = ({x}, {y}, {z})")
            print(f"[debug] value type = {type(vec)}")
            print(
                f"[debug] component types = {type(vec.x)}, {type(vec.y)}, {type(vec.z)}")

            track = _ensure_track(
                binding, unreal.MovieSceneDoubleVectorTrack, prop_name)
            track.set_property_name_and_path(prop_name, prop_name)
            # unreal.MovieSceneDoubleVectorTrackExtensions.set_num_channels_used(track, 3)

            sections = track.get_sections()
            section = sections[0] if sections else track.add_section()

            channels = section.get_channels_by_type(
                unreal.MovieSceneScriptingDoubleChannel)
            if len(channels) < 3:
                raise RuntimeError(
                    f"{prop_name}: expected 3 channels, got {len(channels)}")

            channels[0].set_default(x)
            channels[1].set_default(y)
            channels[2].set_default(z)

            print(
                f"[write] {prop_name} default = ({channels[0].get_default()}, {channels[1].get_default()}, {channels[2].get_default()})")

        def _sync_string(prop_name: str):
            value = str(actor.get_editor_property(prop_name))
            print(f"[prop ] string {prop_name} = {value}")
            track = _ensure_track(
                binding, unreal.MovieSceneStringTrack, prop_name)
            track.set_property_name_and_path(prop_name, prop_name)
            section = _ensure_section(track)
            _set_single_channel_default(section, value, prop_name)

        if binding is None:
            print("[bind ] Resolving binding from current level sequence...")
            for candidate in unreal.MovieSceneSequenceExtensions.get_bindings(level_sequence):
                candidate_id = unreal.MovieSceneSequenceExtensions.get_binding_id(
                    candidate)
                bound_objects = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(
                    candidate_id)
                for obj in bound_objects:
                    if obj == actor:
                        binding = candidate
                        print(f"[bind ] Found binding: {candidate.get_name()}")
                        break
                if binding:
                    break

        if not binding:
            raise RuntimeError(
                f"Could not resolve a Sequencer binding for {actor.get_name()}")

        print(f"[bind ] Using binding: {binding.get_name()}")

        try:
            transform_track = _ensure_track(
                binding, unreal.MovieScene3DTransformTrack, "__transform__")
            transform_section = _ensure_section(transform_track)
            _set_transform_defaults(
                transform_section, actor.get_actor_transform())
        except Exception as e:
            unreal.log_warning(f"Transform sync skipped: {e}")

        for prop_name in [
            "CameraDistance",
            "CameraRotation",
            "CameraRotationOffset",
            "CameraTilt",
            "CameraFocalLength",
            # "CameraFocusOffset",
        ]:
            try:
                _sync_float(prop_name)
            except Exception as e:
                unreal.log_warning(
                    f"Skipped float default for {prop_name}: {e}")

        for prop_name in [
            # "RotateReferenceObject",
            # "LockCameraLookAt",
            # "InitialBoundsLock",
        ]:
            try:
                _sync_bool(prop_name)
            except Exception as e:
                unreal.log_warning(
                    f"Skipped bool default for {prop_name}: {e}")

        for prop_name in [
            "CameraTargetLocation",
            # "Camera Target Location",
            # "CameraTargetLocationOffset",
        ]:
            try:
                _sync_vector(prop_name)
            except Exception as e:
                unreal.log_warning(
                    f"Skipped vector default for {prop_name}: {e}")

        try:
            _sync_string("ActorName")
        except Exception as e:
            unreal.log_warning(f"Skipped string default for ActorName: {e}")

        unreal.LevelSequenceEditorBlueprintLibrary.refresh_current_level_sequence()

        print("sync end")

        print("----- binding readback after sync -----")
        print(f"[bind ] binding name = {binding.get_name()}")

        try:
            for track in binding.get_tracks():
                try:
                    p_name = track.get_editor_property("property_name")
                except Exception:
                    p_name = "<?>"
                try:
                    p_path = track.get_editor_property("property_path")
                except Exception:
                    p_path = "<?>"

                print(
                    f"[bind ] track = {track.get_name()} "
                    f"({track.get_class().get_name()}) "
                    f"prop_name={p_name} prop_path={p_path}"
                )
        except Exception as e:
            print(f"[bind ] track enumeration failed: {e}")

        unreal.LevelSequenceEditorBlueprintLibrary.refresh_current_level_sequence()
        return binding

    @unreal.ufunction(
        static=True,
        params=[unreal.Actor, unreal.LevelSequence],
        ret=unreal.MovieSceneBindingProxy
    )
    def get_reference_object_binding_from_camera(
        camera_actor: unreal.Actor,
        level_sequence: unreal.LevelSequence = None,
    ) -> unreal.MovieSceneBindingProxy:
        """
        Reads camera_actor.ReferenceObject and returns the matching binding
        from the given LevelSequence, or an empty binding proxy if none is found.
        """

        if not camera_actor:
            return unreal.MovieSceneBindingProxy()

        if level_sequence is None:
            level_sequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()

        if not level_sequence:
            return unreal.MovieSceneBindingProxy()

        reference_object = camera_actor.get_editor_property("ReferenceObject")
        if not reference_object:
            print("[ref] ReferenceObject is None")
            return unreal.MovieSceneBindingProxy()

        print(f"[ref] ReferenceObject = {reference_object.get_name()}")

        for binding in unreal.MovieSceneSequenceExtensions.get_bindings(level_sequence):
            binding_id = unreal.MovieSceneSequenceExtensions.get_binding_id(
                level_sequence, binding)
            bound_objects = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(
                binding_id)

            for obj in bound_objects:
                if obj == reference_object:
                    print(f"[ref] Found binding: {binding.get_name()}")
                    return binding

        print("[ref] No matching binding found")

        return unreal.MovieSceneBindingProxy()
