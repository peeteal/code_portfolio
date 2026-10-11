# data_manager.py
from typing import List
from backend import SSHBackend


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