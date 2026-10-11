import unreal


def rail_get_binding_guid_by_object(sequence: unreal.LevelSequence, actor: unreal.Actor) -> unreal.Guid:
    for binding in sequence.get_bindings():

        id: unreal.MovieSceneObjectBindingID
        id = sequence.get_binding_id(binding)
        bound_objects = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(id)
        if actor in bound_objects:
            return binding.get_id()
    return None  # Empty GUID if not found


def rail_get_binding_by_guid(sequence: unreal.LevelSequence, guid: unreal.Guid) -> unreal.MovieSceneBindingProxy:
    for binding in sequence.get_bindings():
        if binding.get_id() == guid:
            return binding
    return None  # Not found


def rail_is_actor_spawnable(sequence: unreal.LevelSequence, actor: unreal.Actor) -> bool:
    for binding in sequence.get_spawnables():
        id: unreal.MovieSceneObjectBindingID
        id = sequence.get_binding_id(binding)
        bound_objects = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(id)
        if actor in bound_objects:
            return True
    return False


def rail_get_binding_for_actor(sequence: unreal.LevelSequence, actor: unreal.Actor) -> unreal.MovieSceneBindingProxy:
    bindings = sequence.get_bindings()

    for binding in bindings:
        # binding_id = binding.get_id()
        id = sequence.get_binding_id(binding)
        bound_objects = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(id)
        if actor in bound_objects:
            return binding
    return None  # Not found


# def rail_attach_binding_to_rail_track(binding: unreal.MovieSceneBindingProxy, bind_to: unreal.MovieSceneBindingProxy):

#     seq: unreal.LevelSequence
#     seq = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()

#     ls_system: unreal.LevelSequenceEditorSubsystem
#     ls_system = unreal.get_editor_subsystem(unreal.LevelSequenceEditorSubsystem)

#     act_binds = ls_system.convert_to_spawnable(binding)
#     act_bind: unreal.MovieSceneBindingProxy
#     act_bind = act_binds[0]

#     id: unreal.MovieSceneObjectBindingID
#     id = seq.get_binding_id(binding=bind_to)

#     this_obj: unreal.RigRail
#     this_obj = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(id)[0]

#     this_obj.get_rail_spline_component()

#     act_attach_track = None
#     tracks = act_bind.get_tracks()
#     for track in tracks:
#         if isinstance(track, unreal.MovieScene3DAttachTrack):
#             act_attach_track = track

#     if act_attach_track is None:
#         act_attach_track: unreal.MovieScene3DAttachTrack
#         act_attach_track = act_bind.add_track(unreal.MovieScene3DAttachTrack)

#     attach_section: unreal.MovieSceneSection = act_attach_track.add_section()
#     attach_section.set_start_frame_bounded(False)
#     attach_section.set_end_frame_bounded(False)

#     attach_section.set_editor_property("AttachComponentName", bind_to.get_name())
#     attach_section.set_editor_property("ConstraintBindingID", id, unreal.PropertyAccessChangeNotifyMode.ALWAYS)
def _get_bound_editor_object(seq: unreal.LevelSequence, binding: unreal.MovieSceneBindingProxy):
    """Return the first editor object bound to `binding`, or None."""
    try:
        bind_id = seq.get_binding_id(binding=binding)
        objs = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(bind_id)
        if objs and len(objs) > 0:
            return objs[0]
    except Exception:
        pass
    return None


def _determine_label_for_binding(seq: unreal.LevelSequence, binding: unreal.MovieSceneBindingProxy, fallback="Rail_LocalTransform"):
    """Return a readable base label derived from the object bound to `binding` (label or name)."""
    try:
        obj = _get_bound_editor_object(seq, binding)
        if obj is not None:
            try:
                return obj.get_actor_label()
            except Exception:
                try:
                    return obj.get_name()
                except Exception:
                    return fallback
    except Exception:
        pass
    return fallback


def _spawn_transform_actor(actor_system: unreal.EditorActorSubsystem, label_base: str):
    """Spawn a lightweight empty Actor at origin and label/tag it. Returns the editor Actor or None."""
    try:
        transform_parent = actor_system.spawn_actor_from_class(unreal.Actor, unreal.Vector(0, 0, 0))
        try:
            transform_parent.set_actor_label(f"{label_base}_LocalTransform")
        except Exception:
            pass
        try:
            transform_parent.tags.append("RailLocalTransform")
        except Exception:
            pass
        return transform_parent
    except Exception as e:
        print(f"[_spawn_transform_actor] failed to spawn transform actor: {e}")
        return None


def _add_actor_and_convert_to_spawnable(ls_system: unreal.LevelSequenceEditorSubsystem,
                                        seq: unreal.LevelSequence,
                                        actor: unreal.Actor):
    """
    Add `actor` to `seq` and convert the binding to a spawnable.
    Returns a MovieSceneBindingProxy (spawnable) or None.
    """
    try:
        added = ls_system.add_actors([actor])
        binding = added[0] if (added and len(added) > 0) else rail_get_binding_for_actor(seq, actor)
        if binding is None:
            return None
        try:
            converted = ls_system.convert_to_spawnable(binding)
            return converted[0] if (converted and len(converted) > 0) else binding
        except Exception:
            return binding
    except Exception as e:
        print(f"[_add_actor_and_convert_to_spawnable] failed to add/convert actor: {e}")
        return None


def _create_attach_track_and_section(target_bind: unreal.MovieSceneBindingProxy,
                                     parent_binding_id: unreal.MovieSceneObjectBindingID,
                                     attach_component_name: str):
    """
    On `target_bind` (the child binding proxy), find or create a MovieScene3DAttachTrack,
    add a section and set it to attach to `parent_binding_id` using `attach_component_name`.
    """
    try:
        tracks = target_bind.get_tracks()
    except Exception:
        tracks = []

    attach_track = None
    for t in tracks:
        if isinstance(t, unreal.MovieScene3DAttachTrack):
            attach_track = t
            break

    if attach_track is None:
        try:
            attach_track = target_bind.add_track(unreal.MovieScene3DAttachTrack)
        except Exception as e:
            print(f"[_create_attach_track_and_section] failed to add attach track: {e}")
            return None

    try:
        section = attach_track.add_section()
        section.set_start_frame_bounded(False)
        section.set_end_frame_bounded(False)
        section.set_editor_property("AttachComponentName", attach_component_name)
        section.set_editor_property("ConstraintBindingID", parent_binding_id, unreal.PropertyAccessChangeNotifyMode.ALWAYS)
        return section
    except Exception as e:
        print(f"[_create_attach_track_and_section] failed to create/initialize section: {e}")
        return None


def rail_attach_binding_to_rail_track(binding: unreal.MovieSceneBindingProxy, bind_to: unreal.MovieSceneBindingProxy):
    """
    Main function:
      - spawn a transform Actor (named after the child),
      - make it a spawnable in the sequence,
      - create attach track transform->rail (bind_to),
      - convert `binding` to spawnable and create attach track binding->transform,
      - attach editor object under transform Actor (KEEP_WORLD).
    """
    seq: unreal.LevelSequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
    if seq is None:
        print("[rail_attach_binding_to_rail_track] no current level sequence; aborting")
        return

    ls_system: unreal.LevelSequenceEditorSubsystem = unreal.get_editor_subsystem(unreal.LevelSequenceEditorSubsystem)
    actor_system: unreal.EditorActorSubsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

    # get rail id (parent in sequence)
    try:
        rail_id: unreal.MovieSceneObjectBindingID = seq.get_binding_id(binding=bind_to)
    except Exception as e:
        print(f"[rail_attach_binding_to_rail_track] failed to get binding id for bind_to: {e}")
        return

    # determine label for transform actor based on child bound object
    label_base = _determine_label_for_binding(seq, binding, fallback="Rail_LocalTransform")

    # spawn transform actor in editor
    transform_parent = _spawn_transform_actor(actor_system, label_base)
    if transform_parent is None:
        print("[rail_attach_binding_to_rail_track] transform_parent is None; aborting")
        return

    # add transform actor to sequence and convert to spawnable
    transform_spawnable = _add_actor_and_convert_to_spawnable(ls_system, seq, transform_parent)
    if transform_spawnable is None:
        print("[rail_attach_binding_to_rail_track] transform_spawnable is None; aborting")
        return

    # create transform -> rail attach track (sequence)
    _create_attach_track_and_section(transform_spawnable, rail_id, bind_to.get_name())

    # Convert the incoming `binding` to spawnable (or use it if conversion fails)
    try:
        converted = ls_system.convert_to_spawnable(binding)
        act_bind = converted[0] if (converted and len(converted) > 0) else binding
    except Exception:
        act_bind = binding

    if act_bind is None:
        print("[rail_attach_binding_to_rail_track] failed to obtain binding as spawnable; aborting second attach")
        return

    # editor-side: attach the bound editor object under transform_parent (KEEP_WORLD)
    try:
        bound_obj_editor = _get_bound_editor_object(seq, binding)
        if bound_obj_editor is not None:
            bound_obj_editor.attach_to_actor(
                transform_parent,
                unreal.Name(""),
                unreal.AttachmentRule.KEEP_WORLD,
                unreal.AttachmentRule.KEEP_WORLD,
                unreal.AttachmentRule.KEEP_WORLD,
                False
            )
    except Exception as e:
        print(f"[rail_attach_binding_to_rail_track] editor attach of bound object failed: {e}")

    # create binding -> transform attach track (sequence)
    try:
        transform_id = seq.get_binding_id(binding=transform_spawnable)
    except Exception as e:
        print(f"[rail_attach_binding_to_rail_track] failed to get binding id for transform_spawnable: {e}")
        return

    _create_attach_track_and_section(act_bind, transform_id, transform_spawnable.get_name())


@unreal.uclass()
class UPPRailLibrary(unreal.BlueprintFunctionLibrary):

    @unreal.ufunction(static=True, ret=unreal.LevelSequence)
    def get_current_level_sequence():
        return unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()

    @unreal.ufunction(static=True, params=[float, float], ret=float)
    def compute_target_time(distance: float, speed: float):
        print(distance)
        print(speed)
        return ((distance) / (speed/3.6))

    @unreal.ufunction(static=True, ret=float)
    def get_current_seq_display_rate():
        return (unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence().get_display_rate().denominator / unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence().get_display_rate().numerator)

    @unreal.ufunction(static=True, ret=int)
    def get_sequence_real_time():
        seq = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
        frame_range = seq.get_playback_range()
        current_range = frame_range.get_end_seconds() - frame_range.get_start_seconds()
        return current_range

    @unreal.ufunction(static=True, ret=int)
    def get_sequence_num_frames():
        seq = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()
        frame_range = seq.get_playback_range()
        current_range = frame_range.get_end_frame() - frame_range.get_start_frame()
        return current_range

    @unreal.ufunction(static=True, ret=float)
    def compute_selected_spline_distance():
        actor: unreal.Actor = unreal.EditorLevelLibrary.get_selected_level_actors()[0]
        spline: unreal.SplineComponent
        spline = actor.get_component_by_class(unreal.CineSplineComponent)
        # print(spline.get_spline_length())
        return spline.get_spline_length() * 0.01

    @unreal.ufunction(static=True, params=[unreal.CineSplineComponent])
    def draw_spline_length_labels(spline_comp: unreal.CineSplineComponent):
        total_length = spline_comp.get_spline_length()
        num_points = spline_comp.get_number_of_spline_points()

        for i in range(num_points):
            loc = spline_comp.get_location_at_spline_point(i, unreal.SplineCoordinateSpace.WORLD)
            dist = spline_comp.get_distance_along_spline_at_spline_point(i)
            label = f"{dist:.1f} cm / {dist / 100:.2f} m"

            # Draw text at the spline point location
            unreal.SystemLibrary.draw_debug_string(spline_comp,
                                                   loc + unreal.Vector(0, 0, 20),  # Offset upward
                                                   label,
                                                   None,
                                                   unreal.LinearColor.YELLOW,
                                                   0.0)  # Duration (0 = permanent in editor until play/reset)

            # unreal.SystemLibrary.dr

    @unreal.ufunction(static=True, params=[unreal.Actor])
    def invert_rail_spline_points(actor: unreal.Actor):
        # actor = unreal.EditorLevelLibrary.get_selected_level_actors()[0]
        spline: unreal.SplineComponent
        spline = actor.get_component_by_class(unreal.SplineComponent)

        num_points = spline.get_number_of_spline_points()
        spline_points = []

        # Store all points
        for i in range(num_points):
            point_data = {
                "position": spline.get_location_at_spline_point(i, unreal.SplineCoordinateSpace.LOCAL),
                "rotation": spline.get_rotation_at_spline_point(i, unreal.SplineCoordinateSpace.LOCAL),
                "tangent": spline.get_tangent_at_spline_point(i, unreal.SplineCoordinateSpace.LOCAL),
                "scale": spline.get_scale_at_spline_point(i),
                "type": spline.get_spline_point_type(i)
            }
            spline_points.append(point_data)

        # Clear and add reversed
        spline.clear_spline_points()

        for point in reversed(spline_points):
            spline.add_spline_point(point["position"], unreal.SplineCoordinateSpace.LOCAL)
            idx = spline.get_number_of_spline_points() - 1
            spline.set_spline_point_type(idx, point["type"])
            spline.set_rotation_at_spline_point(idx, point["rotation"], unreal.SplineCoordinateSpace.LOCAL, False)
            spline.set_scale_at_spline_point(idx, point["scale"], False)
            spline.set_tangent_at_spline_point(idx, -point["tangent"], unreal.SplineCoordinateSpace.LOCAL)

        spline.update_spline()

    @unreal.ufunction(static=True, params=[unreal.Actor])
    def attach_this_and_selected_actors(this: unreal.Actor):
        actor_system: unreal.EditorActorSubsystem
        actor_system = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

        ls_system: unreal.LevelSequenceEditorSubsystem
        ls_system = unreal.get_editor_subsystem(unreal.LevelSequenceEditorSubsystem)

        seq: unreal.LevelSequence = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()

        if seq is None:
            return

        this_binding = ls_system.add_actors([this])
        if (len(this_binding) > 0):  # this was not in the level sequence
            this_binding = this_binding[0]
            spawnable = rail_is_actor_spawnable(seq, this)
            # if not spawnable:
            this_binding = ls_system.convert_to_spawnable(this_binding)[0]
        else:  # was in level sequence and has binding
            this_binding = rail_get_binding_for_actor(seq, this)
            # print(this_binding)

        # resolve spawnable bindings first, then possesable actors
        selected_bindings = unreal.LevelSequenceEditorBlueprintLibrary.get_selected_bindings()

        for binding in selected_bindings:
            id: unreal.MovieSceneObjectBindingID
            id = seq.get_binding_id(binding=binding)

            obj: unreal.Actor = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(id)[0]
            obj.set_actor_location(unreal.Vector(0, 0, 0), sweep=False, teleport=True)  # Relative location workaround
            rail_attach_binding_to_rail_track(binding, this_binding)

        actors = actor_system.get_selected_level_actors()
        for actor in actors:
            actor: unreal.Actor
            actor.set_actor_location(unreal.Vector(0, 0, 0), sweep=False, teleport=True)  # Relative location workaround

            # Arrays are for the subsystem functions
            bindings: unreal.Array
            bindings = ls_system.add_actors([actor])

            for binding in bindings:
                rail_attach_binding_to_rail_track(binding, this_binding)

    @unreal.ufunction(static=True)
    def convert_actor():
        actor_system: unreal.EditorActorSubsystem
        actor_system = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

        ls_system: unreal.LevelSequenceEditorSubsystem
        ls_system = unreal.get_editor_subsystem(unreal.LevelSequenceEditorSubsystem)

        seq: unreal.LevelSequence
        seq = unreal.LevelSequenceEditorBlueprintLibrary.get_current_level_sequence()

        # Add selected actors to current level sequence as possessables
        actors = actor_system.get_selected_level_actors()
        for actor in actors:
            tags = actor.tags
            if not tags.has_tag("RailBlend"):
                tags.append("RailBlend")

        location_backup = []
        for actor in actors:
            actor: unreal.Actor
            location_backup.append(actor.get_actor_location())
            actor.set_actor_location(unreal.Vector(0, 0, 0), sweep=False, teleport=True)  # Relative location workaround

        # Arrays are for the subsystem functions
        bindings: unreal.Array
        bindings = ls_system.add_actors(actors)
        spline_bindings: unreal.Array
        spline_bindings = []

        for i, actor in enumerate(actors):
            rails = []
            cam_rig_rail: unreal.RigRail
            cam_rig_rail = actor_system.spawn_actor_from_class(unreal.RigRail, location_backup[i])
            cam_rig_rail.set_editor_property(name="bUseAbsolutePosition", value=False,
                                             notify_mode=unreal.PropertyAccessChangeNotifyMode.ALWAYS)

            rails.append(cam_rig_rail)
            spline_bindings.extend(ls_system.add_actors(rails))

            act_binds = ls_system.convert_to_spawnable(bindings[i])
            act_bind: unreal.MovieSceneBindingProxy
            act_bind = act_binds[0]

            rail_binds = ls_system.convert_to_spawnable(spline_bindings[i])
            rail_bind: unreal.MovieSceneBindingProxy
            rail_bind = rail_binds[0]

            id: unreal.MovieSceneObjectBindingID
            id = seq.get_binding_id(binding=rail_bind)

            # Set spawnable rail obj as hidden
            rail_obj: unreal.RigRail
            rail_obj = unreal.LevelSequenceEditorBlueprintLibrary.get_bound_objects(id)[0]
            rail_obj.set_editor_property(name="bUseAbsolutePosition", value=False,
                                         notify_mode=unreal.PropertyAccessChangeNotifyMode.ALWAYS)
            rail_obj.set_editor_property("bShowRailVisualization", False, unreal.PropertyAccessChangeNotifyMode.ALWAYS)
            rail_obj.attach_rotation_x = False
            rail_obj.attach_rotation_y = False
            rail_obj.attach_rotation_z = False

            spline = rail_obj.get_rail_spline_component()

            spline.clear_spline_points()
            spline.set_spline_points([unreal.Vector(0, 0, 0), unreal.Vector(10000, 0, 0)],
                                     coordinate_space=unreal.SplineCoordinateSpace.LOCAL)

            act_attach_track: unreal.MovieScene3DAttachTrack
            act_attach_track = act_bind.add_track(unreal.MovieScene3DAttachTrack)

            attach_section: unreal.MovieSceneSection = act_attach_track.add_section()
            attach_section.set_start_frame_bounded(False)
            attach_section.set_end_frame_bounded(False)

            attach_section.set_editor_property("AttachComponentName", rail_bind.get_name())
            attach_section.set_editor_property("ConstraintBindingID", id, unreal.PropertyAccessChangeNotifyMode.ALWAYS)

        # for i in len(actors), spline_binding in bindings, spline_bindings:

    # @unreal.ufunction(static = True, params=[unreal.Actor, unreal.MeshComponent])
    # def set_mesh_actor(spawn_to : unreal.Actor, asset : unreal.MeshComponent):
    #     spawn_to.

    # @unreal.ufunction(static = True, params=[unreal.Actor, unreal.MeshComponent])
    # def set_mesh_actor(spawn_to : unreal.Actor, asset : unreal.MeshComponent):
    #     spawn_to.
    # def get_selected_lights_paths():
