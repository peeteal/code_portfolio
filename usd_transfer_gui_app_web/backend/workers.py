import traceback
import threading
from backend.backend import SSHBackend

class FolderLoaderWorker:
    """
    Loads full project/sequence/shot/version hierarchy using DataManager + backend.sftp.
    Uses callbacks for progress, log, finished, error.
    """

    def __init__(self, data_manager, root_path="/upp/servers/vfxstore2/VFXstore/PROJECTS/",
                 on_progress=None, on_log=None, on_error=None, on_finished=None):
        """
        Store the data manager to fill, the remote root path to scan and the optional
        callbacks: on_progress(index, total, project), on_log(message),
        on_error(message) and on_finished().
        """
        self.data_manager = data_manager
        self.root_path = root_path

        # Callbacks
        self.on_progress = on_progress or (lambda *a, **k: None)
        self.on_log = on_log or (lambda *a, **k: None)
        self.on_error = on_error or (lambda *a, **k: None)
        self.on_finished = on_finished or (lambda *a, **k: None)

    def start(self):
        """Run the worker in a daemon thread and return the thread."""
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t

    def run(self):
        """
        Load the whole project -> sequence -> shot -> version tree into the data manager.

        Calls on_progress(index, total, project) for every project and on_finished()
        when the scan is complete. on_error(message) is called for a project without
        accessible sequences, for a shot without versions and for an unexpected failure.
        """
        try:
            projects = self.data_manager.load_projects(self.root_path)
            total = len(projects)
            for i, proj in enumerate(projects, start=1):
                self.on_progress(i, total, proj)
                sequences = self.data_manager.load_sequences(proj, self.root_path)
                if not sequences:
                    self.on_error(f"{proj}: Access denied. Ask for permissions.")

                for seq in sequences:
                    shots = self.data_manager.load_shots(proj, seq, self.root_path)
                    for shot in shots:
                        versions = self.data_manager.load_versions(proj, seq, shot, self.root_path)
                        if len(versions) == 0:
                            self.on_error(f"{proj} {seq} {shot}: No available versions - renderManifest.bty_env_main.usd does not exist.")

            self.data_manager.filter_empty_branches()
            self.on_finished()
        except Exception as e:
            tb = traceback.format_exc()
            self.on_error(f"{e}{tb}")


class CommandRunnerWorker:
    """
    Runs the provided list of commands (strings). For each command it:
      - creates a temporary SSH client (safer for multithreaded usage),
      - runs the remote script,
      - downloads remote output dir to local_dir (using temporary SFTP),
      - removes remote output dir.
    Uses callbacks for progress, log, finished, error.
    """

    def __init__(self, backend: SSHBackend, commands, local_dir,
                 remote_script="/home/svc_vpvfxtransfer_ro/dev/usd_transfer/run.sh",
                 on_progress=None, on_log=None, on_error=None, on_finished=None):
        """
        Store the run configuration.

        - backend: connected SSHBackend used to create an SSH client per command.
        - commands: command lines "<project> <sequence> <shot> <version> [usd|usda]".
        - local_dir: local directory the exported files are downloaded to.
        - remote_script: script on the server that runs the export.
        - on_progress(index, total), on_log(message), on_error(message), on_finished():
          optional callbacks.
        """
        self.backend = backend
        self.commands = commands[:]
        self.local_dir = local_dir
        self.remote_script = remote_script

        # Callbacks
        self.on_progress = on_progress or (lambda *a, **k: None)
        self.on_log = on_log or (lambda *a, **k: None)
        self.on_error = on_error or (lambda *a, **k: None)
        self.on_finished = on_finished or (lambda *a, **k: None)

    def start(self):
        """Run the worker in a daemon thread and return the thread."""
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t

    def find_unique_remote_folder_tmp(self, sftp, base_path):
        """
        Find a remote folder path of the form <base_path>-<n> that does not exist yet.

        Returns (path, n as a string).
        """
        counter = 0
        while True:
            candidate = f"{base_path}-{counter}"
            try:
                sftp.stat(candidate)
                counter += 1
                continue
            except IOError:
                return candidate, str(counter)

    def run(self):
        """
        Run every command on the server and download its output.

        For each command line, a temporary SSH/SFTP session is opened and the remote
        script is run with a unique output folder suffix. The produced folder is then
        downloaded to local_dir and removed from the server. Calls on_log and
        on_progress along the way and on_finished() when all commands are done. On the
        first failed command or download, on_error(message) is called and the run stops.
        """
        total = len(self.commands)
        if total == 0:
            self.on_finished()
            return

        try:
            for idx, cmd_line in enumerate(self.commands, start=1):
                self.on_log(f"Starting command ({idx}/{total}): {cmd_line}")
                tokens = cmd_line.split()
                has_format = tokens and tokens[-1] in ("usd", "usda")
                tokens_no_format = tokens[:-1] if has_format else tokens[:]
                command_folder = "-".join(tokens_no_format)

                base_remote = f"/home/svc_vpvfxtransfer_ro/dev/usd_transfer_export/{command_folder}"

                ssh = self.backend.create_ssh_client()
                sftp = ssh.open_sftp()

                remote_output_dir, tmp = self.find_unique_remote_folder_tmp(sftp, base_remote)

                tokens_mod = tokens[:]
                if has_format:
                    tokens_mod.insert(-1, tmp)
                else:
                    tokens_mod.append(tmp)

                final_cmd_str = " ".join(tokens_mod)
                full_cmd = f"bash {self.remote_script} {final_cmd_str}"

                stdin, stdout, stderr = ssh.exec_command(full_cmd)
                exit_status = stdout.channel.recv_exit_status()
                out = stdout.read().decode()
                err = stderr.read().decode()
                if exit_status != 0:
                    ssh.close()
                    sftp.close()
                    self.on_error(f"Command failed: {full_cmd}{err}")
                    return
                self.on_log(f"Remote script finished ({idx}/{total}). Output:{out}")

                local_target_dir = self.local_dir
                try:
                    self.backend.download_recursive(sftp, remote_output_dir, local_target_dir)
                    self.on_log(f"Downloaded remote '{remote_output_dir}' to '{local_target_dir}'")
                except Exception as e:
                    sftp.close()
                    ssh.close()
                    self.on_error(f"Download failed for {remote_output_dir}: {e}")
                    return

                try:
                    self.backend.rmdir_recursive(sftp, remote_output_dir)
                    self.on_log(f"Removed remote folder {remote_output_dir}")
                except Exception:
                    self.on_log(f"Could not remove remote folder {remote_output_dir} (ignored)")

                sftp.close()
                ssh.close()

                self.on_progress(idx, total)

            self.on_finished()
        except Exception as e:
            tb = traceback.format_exc()
            self.on_error(f"{e}{tb}")
