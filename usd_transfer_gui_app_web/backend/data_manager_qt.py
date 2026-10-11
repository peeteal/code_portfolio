# data_manager.py
from typing import List
from backend.backend import SSHBackend


class DataManager:
    """
    Encapsulates the domain logic: building projects -> sequences -> shots -> versions mappings.
    Uses SSHBackend instance to perform SFTP/stat operations.
    """

    def __init__(self, backend: SSHBackend):
        self.backend = backend
        # structured caches
        self.projects: List[str] = []
        self.sequences_per_project = {}
        self.shots_per_sequence = {}
        self.versions_per_shot = {}


    def safe_listdir(self, path: str):
        """Return [] on permission errors / missing path."""
        try:
            items = self.backend.listdir_attr(path)
            return items
        except Exception:
            return []

    def load_projects(self, root="/upp/servers/vfxstore2/VFXstore/PROJECTS/"):
        items = self.safe_listdir(root)
        if items:
            self.projects = [f.filename for f in items if str(f.longname).startswith("d")]
            self.projects.sort()
            return self.projects
        return []

    def load_sequences(self, project):
        path = f"/upp/servers/vfxstore2/VFXstore/PROJECTS/{project}/usd/seq/"
        items = self.safe_listdir(path)
        if items:
            sequences = [f.filename for f in items if str(f.longname).startswith("d")]
            sequences.sort()
            self.sequences_per_project[project] = sequences
            return sequences
        return []

    def load_shots(self, project, sequence):
        path = f"/upp/servers/vfxstore2/VFXstore/PROJECTS/{project}/usd/seq/{sequence}/"
        items = self.safe_listdir(path)
        if items:
            shots = [f.filename for f in items if str(f.longname).startswith("d") and f.filename.isdigit()]
            shots.sort()
            self.shots_per_sequence[project + sequence] = shots
            return shots
        return []

    def load_versions(self, project, sequence, shot):
        base = f"/upp/servers/vfxstore2/VFXstore/PROJECTS/{project}/usd/seq/{sequence}/{shot}/usdManifest/renderManifest/"
        items = self.safe_listdir(base)
        if items:
            versions = []
            for v in items:
                if str(v.longname).startswith("d"):
                    usd_path = f"{base}{v.filename}/renderManifest.bty_env_main.usd"
                    if self.backend.file_exists(usd_path):
                        versions.append(v.filename)
            versions.sort(reverse=True)
            self.versions_per_shot[project + sequence + shot] = versions
            return versions
        return []

    def filter_empty_branches(self):
        """
        Prune projects, sequences, and shots, keeping only those
        that have at least one version.
        """
        # New filtered structures
        filtered_projects = []
        filtered_sequences_per_project = {}
        filtered_shots_per_sequence = {}
        filtered_versions_per_shot = {}

        for project in self.projects:
            valid_sequences = []
            for seq in self.sequences_per_project.get(project, []):
                valid_shots = []
                for shot in self.shots_per_sequence.get(project + seq, []):
                    versions = self.versions_per_shot.get(project + seq + shot, [])
                    if versions:  # keep only if versions exist
                        valid_shots.append(shot)
                        filtered_versions_per_shot[project + seq + shot] = versions

                if valid_shots:
                    filtered_shots_per_sequence[project + seq] = valid_shots
                    valid_sequences.append(seq)

            if valid_sequences:
                filtered_sequences_per_project[project] = valid_sequences
                filtered_projects.append(project)

        # Replace original structures
        self.projects = filtered_projects
        self.sequences_per_project = filtered_sequences_per_project
        self.shots_per_sequence = filtered_shots_per_sequence
        self.versions_per_shot = filtered_versions_per_shot

# Regenerated possible, does not work 100%
# # data_manager.py
# from typing import List, Dict, Any, Optional
# from backend.backend import SSHBackend


# class DataManager:
#     """
#     In-memory hierarchical cache for projects -> sequences -> shots -> versions.
#     - `self.tree` is the canonical cache populated by reload_all().
#     - `current_*` lists hold what the UI should display (project/sequence/shot/version).
#     Only reload_all() touches the backend; select_*() just reads from the in-memory tree.
#     """

#     def __init__(self, backend: SSHBackend):
#         self.backend = backend

#         # Canonical hierarchical cache
#         # structure: { project: { sequence: { shot: [versions...] } } }
#         self.tree: Dict[str, Dict[str, Dict[str, List[str]]]] = {}

#         # "What should be shown" lists (UI-facing)
#         self.current_projects: List[str] = []
#         self.current_sequences: List[str] = []
#         self.current_shots: List[str] = []
#         self.current_versions: List[str] = []

#     # ------------------------
#     # Backend helpers
#     # ------------------------
#     def safe_listdir(self, path: str):
#         """Return [] on permission errors / missing path. Uses backend.listdir_attr."""
#         try:
#             return self.backend.listdir_attr(path)
#         except Exception:
#             return []

#     # ------------------------
#     # Reload / build the tree
#     # ------------------------
#     def reload_all(self, root: str = "/upp/servers/vfxstore2/VFXstore/PROJECTS/"):
#         """
#         Walks the remote tree once and builds `self.tree`.
#         Prunes branches that have no versions (same behavior as original filter_empty_branches).
#         After building, updates `current_projects` (top-level list).
#         """
#         tree: Dict[str, Dict[str, Dict[str, List[str]]]] = {}

#         projects_items = self.safe_listdir(root)
#         if not projects_items:
#             # nothing found or permission denied - clear everything
#             self.tree = {}
#             self.current_projects = []
#             self.current_sequences = []
#             self.current_shots = []
#             self.current_versions = []
#             return

#         # Build projects
#         projects = [f.filename for f in projects_items if str(f.longname).startswith("d")]
#         projects.sort()
#         for project in projects:
#             project_node: Dict[str, Dict[str, List[str]]] = {}

#             # sequences
#             seq_path = f"{root}{project}/usd/seq/"
#             seq_items = self.safe_listdir(seq_path)
#             if not seq_items:
#                 continue

#             sequences = [s.filename for s in seq_items if str(s.longname).startswith("d")]
#             sequences.sort()
#             for seq in sequences:
#                 seq_node: Dict[str, List[str]] = {}

#                 # shots
#                 shots_path = f"{root}{project}/usd/seq/{seq}/"
#                 shot_items = self.safe_listdir(shots_path)
#                 if not shot_items:
#                     continue

#                 # filter only directories which are numeric (as in original)
#                 shots = [sh.filename for sh in shot_items if str(sh.longname).startswith("d") and sh.filename.isdigit()]
#                 # sort shots numerically if possible
#                 try:
#                     shots.sort(key=lambda x: int(x))
#                 except Exception:
#                     shots.sort()

#                 for shot in shots:
#                     versions_base = f"{root}{project}/usd/seq/{seq}/{shot}/usdManifest/renderManifest/"
#                     ver_items = self.safe_listdir(versions_base)
#                     if not ver_items:
#                         continue

#                     versions: List[str] = []
#                     for v in ver_items:
#                         if str(v.longname).startswith("d"):
#                             usd_path = f"{versions_base}{v.filename}/renderManifest.bty_env_main.usd"
#                             # keep only versions that contain the usd file
#                             if self.backend.file_exists(usd_path):
#                                 versions.append(v.filename)

#                     if versions:
#                         # sort versions newest first (as before)
#                         versions.sort(reverse=True)
#                         seq_node[shot] = versions

#                 # only add the sequence if it has at least one shot with versions
#                 if seq_node:
#                     project_node[seq] = seq_node

#             # only add the project if it has at least one sequence with shots+versions
#             if project_node:
#                 tree[project] = project_node

#         # replace canonical tree and populate top-level UI lists
#         self.tree = tree
#         self.current_projects = sorted(list(self.tree.keys()))

#         # reset lower-level current_* lists (no selection yet)
#         self.current_sequences = []
#         self.current_shots = []
#         self.current_versions = []

#     # ------------------------
#     # UI selection helpers (read-only, do not touch backend)
#     # ------------------------
#     def select_project(self, project: str):
#         """
#         Set current_sequences based on project and clear deeper lists.
#         Does not call backend; reads from `self.tree`.
#         """
#         if not project or project not in self.tree:
#             self.current_sequences = []
#             self.current_shots = []
#             self.current_versions = []
#             return

#         sequences = sorted(list(self.tree[project].keys()))
#         self.current_sequences = sequences
#         self.current_shots = []
#         self.current_versions = []

#     def select_sequence(self, project: str, sequence: str):
#         """
#         Set current_shots for given project+sequence and clear deeper lists.
#         """
#         if not (project and sequence):
#             self.current_shots = []
#             self.current_versions = []
#             return

#         seq_node = self.tree.get(project, {}).get(sequence, {})
#         shots = list(seq_node.keys())
#         # shots were stored numeric-sorted during reload; preserve order
#         try:
#             shots.sort(key=lambda x: int(x))
#         except Exception:
#             shots.sort()
#         self.current_shots = shots
#         self.current_versions = []

#     def select_shot(self, project: str, sequence: str, shot: str):
#         """
#         Set current_versions for project+sequence+shot.
#         """
#         if not (project and sequence and shot):
#             self.current_versions = []
#             return

#         versions = self.tree.get(project, {}).get(sequence, {}).get(shot, [])
#         # versions already sorted reverse during reload_all
#         self.current_versions = versions

#     # ------------------------
#     # Direct getters (do not mutate current_* lists)
#     # Useful for API endpoints that want to read from the cache without changing UI state.
#     # ------------------------
#     def get_projects(self) -> List[str]:
#         return sorted(list(self.tree.keys()))

#     def get_sequences(self, project: str) -> List[str]:
#         if not project:
#             return []
#         seqs = list(self.tree.get(project, {}).keys())
#         seqs.sort()
#         return seqs

#     def get_shots(self, project: str, sequence: str) -> List[str]:
#         if not (project and sequence):
#             return []
#         shots = list(self.tree.get(project, {}).get(sequence, {}).keys())
#         try:
#             shots.sort(key=lambda x: int(x))
#         except Exception:
#             shots.sort()
#         return shots

#     def get_versions(self, project: str, sequence: str, shot: str) -> List[str]:
#         if not (project and sequence and shot):
#             return []
#         return list(self.tree.get(project, {}).get(sequence, {}).get(shot, []))

#     # ------------------------
#     # Convenience: clear cache
#     # ------------------------
#     def clear_cache(self):
#         self.tree = {}
#         self.current_projects = []
#         self.current_sequences = []
#         self.current_shots = []
#         self.current_versions = []

