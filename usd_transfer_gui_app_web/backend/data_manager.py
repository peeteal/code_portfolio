from typing import List, Dict, Any, Optional
import threading


class DataManager:
    """
    Maintains hierarchical project data and provides legacy caches for compatibility.

    Key behavior change: load_sequences and load_shots will now only expose sequences
    and shots that actually contain at least one version (i.e. at least one
    <version>/renderManifest.bty_env_main.usd exists). This ensures the UI does not
    present shots without available versions.
    """

    def __init__(self, backend, default_root: Optional[str] = None, manifest_filename: str = "renderManifest.bty_env_main.usd"):
        self.backend = backend
        self.default_root = default_root
        self.manifest_filename = manifest_filename

        # Authoritative hierarchical structure
        # project_tree: project -> sequence -> shot -> [versions]
        self.project_tree: Dict[str, Dict[str, Dict[str, List[str]]]] = {}

        # Legacy caches (kept for backward compatibility with the rest of the app)
        self.projects: List[str] = []
        self.sequences_per_project: Dict[str, List[str]] = {}
        # NOTE: existing code expects concatenated keys for shots_per_sequence and versions_per_shot
        # (previously implemented as project+sequence and project+sequence+shot). We'll keep that
        # behavior for maximum compatibility but centralize the key creation in _composite_key().
        self.shots_per_sequence: Dict[str, List[str]] = {}
        self.versions_per_shot: Dict[str, List[str]] = {}

        # Lock to make cache updates thread-safe
        self._lock = threading.RLock()

    # ---------------------- Helper utilities ----------------------
    def _composite_key(self, project: str, sequence: Optional[str] = None, shot: Optional[str] = None) -> str:
        parts = [project or ""]
        if sequence is not None:
            parts.append(sequence)
        if shot is not None:
            parts.append(shot)
        return "".join(parts)

    def safe_listdir(self, path: str):
        """Return [] on permission errors / missing path."""
        try:
            items = self.backend.listdir_attr(path)
            return items
        except Exception:
            return []

    # ---------------------- Loading methods (public API preserved) ----------------------
    def load_projects(self, root: Optional[str] = None, eager: bool = False) -> List[str]:
        root = root or self.default_root
        if not root:
            raise ValueError("root path must be provided to load_projects")

        items = self.safe_listdir(root)
        projects = []
        if items:
            projects = [f.filename for f in items if str(f.longname).startswith("d")]
            projects.sort()

        with self._lock:
            self.project_tree = {p: {} for p in projects}
            self.projects = projects
            # clear lower-level caches
            self.sequences_per_project.clear()
            self.shots_per_sequence.clear()
            self.versions_per_shot.clear()

        # Optionally perform an eager scan to populate sequences/shots/versions for each project.
        # This ensures filtering (removal of empty sequences/shots) persists immediately after
        # loading projects (useful on explicit reload/connect actions). This is optional because
        # it can be expensive for very large trees; call with eager=True when you want the
        # immediate, fully-filtered view.
        if eager:
            for p in projects:
                try:
                    # call with same root; load_sequences will in turn load_shots and load_versions as needed
                    self.load_sequences(p, root)
                except Exception:
                    # ignore per-project errors during eager scan -- keep behavior resilient
                    continue
            self.filter_empty_branches()

        return projects

    def load_sequences(self, project: str, root: Optional[str] = None) -> List[str]:
        """
        Load sequences for a project. Only sequences that contain at least one shot
        which in turn contains at least one version will be returned.
        This method calls load_shots(seq) to determine whether the sequence should
        be kept — thus ensuring UI won't show empty branches.
        """
        root = root or self.default_root
        if not root:
            raise ValueError("root path must be provided to load_sequences")

        path = f"{root.rstrip('/')}/{project}/usd/seq/"
        items = self.safe_listdir(path)
        raw_sequences = []
        if items:
            raw_sequences = [f.filename for f in items if str(f.longname).startswith("d")]
            raw_sequences.sort()

        filtered_sequences: List[str] = []
        with self._lock:
            self.project_tree.setdefault(project, {})
            # For each sequence, load shots and keep only sequences with at least one shot that has versions
            for seq in raw_sequences:
                shots = self.load_shots(project, seq, root)
                if shots:
                    filtered_sequences.append(seq)
                    # ensure entry exists in tree (load_shots will have created shot entries)
                    self.project_tree[project].setdefault(seq, self.project_tree[project].get(seq, {}))

            # legacy cache: store only filtered sequences
            self.sequences_per_project[project] = filtered_sequences
            # remove any stale lower-level caches under this project
            keys_to_remove = [k for k in self.shots_per_sequence.keys() if k.startswith(project)]
            for k in keys_to_remove:
                self.shots_per_sequence.pop(k, None)

            keys_to_remove = [k for k in self.versions_per_shot.keys() if k.startswith(project)]
            for k in keys_to_remove:
                self.versions_per_shot.pop(k, None)

        return filtered_sequences

    def load_shots(self, project: str, sequence: str, root: Optional[str] = None) -> List[str]:
        """
        Load shots for project/sequence and return the list. Only shots that have
        at least one valid version (manifest file) are returned.
        """
        root = root or self.default_root
        if not root:
            raise ValueError("root path must be provided to load_shots")

        path = f"{root.rstrip('/')}/{project}/usd/seq/{sequence}/"
        items = self.safe_listdir(path)
        raw_shots: List[str] = []
        if items:
            raw_shots = [f.filename for f in items if str(f.longname).startswith("d") and f.filename.isdigit()]
            raw_shots.sort()

        shots: List[str] = []
        with self._lock:
            # ensure tree nodes exist
            self.project_tree.setdefault(project, {}).setdefault(sequence, {})

            for sh in raw_shots:
                # load_versions will check for presence of manifest file and will
                # populate versions_per_shot and project_tree accordingly
                versions = self.load_versions(project, sequence, sh, root)
                if versions:
                    shots.append(sh)
                    # project_tree entry was set by load_versions; ensure it's present
                    self.project_tree[project][sequence].setdefault(sh, versions[:])

            # legacy cache uses concatenated key
            key = self._composite_key(project, sequence)
            self.shots_per_sequence[key] = shots

            # clear versions for any shots that were removed (safety)
            to_remove = []
            for existing_key in list(self.versions_per_shot.keys()):
                if existing_key.startswith(self._composite_key(project, sequence)):
                    # if this key corresponds to a shot not in current shots list, remove it
                    suffix = existing_key[len(self._composite_key(project, sequence)):]
                    # suffix starts with shot name if present
                    # crude check: if shot name not in shots list, remove
                    matched = False
                    for sname in shots:
                        if existing_key == self._composite_key(project, sequence, sname):
                            matched = True
                            break
                    if not matched:
                        to_remove.append(existing_key)
            for r in to_remove:
                self.versions_per_shot.pop(r, None)

        return shots

    def load_versions(self, project: str, sequence: str, shot: str, root: Optional[str] = None) -> List[str]:
        root = root or self.default_root
        if not root:
            raise ValueError("root path must be provided to load_versions")

        base = f"{root.rstrip('/')}/{project}/usd/seq/{sequence}/{shot}/usdManifest/renderManifest/"
        items = self.safe_listdir(base)
        versions: List[str] = []
        if items:
            for v in items:
                if str(v.longname).startswith("d"):
                    usd_path = f"{base}{v.filename}/{self.manifest_filename}"
                    if self.backend.file_exists(usd_path):
                        versions.append(v.filename)
        versions.sort(reverse=True)

        # with self._lock:
        #     # make sure the structure exists in project_tree
        #     self.project_tree.setdefault(project, {}).setdefault(sequence, {}).setdefault(shot, versions[:])

        #     # legacy cache
        #     vkey = self._composite_key(project, sequence, shot)
        #     self.versions_per_shot[vkey] = versions[:]

        # return versions
        with self._lock:
            vkey = self._composite_key(project, sequence, shot)

            if versions:
                # Only create/populate the shot entry when there are actual versions.
                self.project_tree.setdefault(project, {}).setdefault(sequence, {})[shot] = versions[:]
                # legacy cache
                self.versions_per_shot[vkey] = versions[:]
            else:
                # No versions -> remove any stale entries for this shot from both
                # authoritative tree and legacy caches so UI won't show empty shots.
                if project in self.project_tree:
                    seq_dict = self.project_tree[project]
                    if sequence in seq_dict:
                        seq_dict[sequence].pop(shot, None)
                        # If the sequence becomes empty, remove it too.
                        if not seq_dict[sequence]:
                            seq_dict.pop(sequence, None)
                    # If project now empty, remove project entry as well (keeps tree tidy)
                    if not seq_dict:
                        self.project_tree.pop(project, None)
                # clean legacy cache entry
                self.versions_per_shot.pop(vkey, None)

        return versions

    # ---------------------- Getters (convenience + single source of truth) ----------------------
    def get_projects(self) -> List[str]:
        with self._lock:
            return list(self.project_tree.keys()) if self.project_tree else list(self.projects)

    def get_sequences(self, project: str) -> List[str]:
        with self._lock:
            seqs = list(self.project_tree.get(project, {}).keys())
            if seqs:
                return sorted(seqs)
            return self.sequences_per_project.get(project, [])

    def get_shots(self, project: str, sequence: str) -> List[str]:
        with self._lock:
            shots = list(self.project_tree.get(project, {}).get(sequence, {}).keys())
            if shots:
                return sorted(shots)
            key = self._composite_key(project, sequence)
            return self.shots_per_sequence.get(key, [])

    def get_versions(self, project: str, sequence: str, shot: str) -> List[str]:
        with self._lock:
            versions = self.project_tree.get(project, {}).get(sequence, {}).get(shot)
            if versions is not None:
                return list(versions)
            key = self._composite_key(project, sequence, shot)
            return self.versions_per_shot.get(key, [])

    # ---------------------- Filtering & housekeeping ----------------------
    def filter_empty_branches(self):
        with self._lock:
            new_tree: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
            for project, sequences in self.project_tree.items():
                kept_sequences = {}
                for seq, shots in sequences.items():
                    kept_shots = {}
                    for shot, versions in shots.items():
                        if versions:
                            kept_shots[shot] = list(versions)
                    if kept_shots:
                        kept_sequences[seq] = kept_shots
                if kept_sequences:
                    new_tree[project] = kept_sequences

            self.project_tree = new_tree

            # rebuild legacy caches from the pruned project_tree
            self.projects = sorted(list(self.project_tree.keys()))
            self.sequences_per_project.clear()
            self.shots_per_sequence.clear()
            self.versions_per_shot.clear()

            for project, sequences in self.project_tree.items():
                seqs = sorted(list(sequences.keys()))
                self.sequences_per_project[project] = seqs
                for seq, shots in sequences.items():
                    shots_list = sorted(list(shots.keys()))
                    s_key = self._composite_key(project, seq)
                    self.shots_per_sequence[s_key] = shots_list
                    for shot, versions in shots.items():
                        v_key = self._composite_key(project, seq, shot)
                        self.versions_per_shot[v_key] = list(versions)

    # Optional utility for debugging/inspection
    def dump_tree(self) -> Dict[str, Any]:
        with self._lock:
            return {p: {s: {sh: list(v) for sh, v in shots.items()} for s, shots in seqs.items()} for p, seqs in self.project_tree.items()}
