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
        self.data_manager = data_manager
        self.root_path = root_path

        # Callbacks
        self.on_progress = on_progress or (lambda *a, **k: None)
        self.on_log = on_log or (lambda *a, **k: None)
        self.on_error = on_error or (lambda *a, **k: None)
        self.on_finished = on_finished or (lambda *a, **k: None)

    def start(self):
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t

    def run(self):
        try:
            projects = self.data_manager.load_projects(self.root_path)
            total = len(projects)
            for i, proj in enumerate(projects, start=1):
                self.on_progress(i, total, proj)
                sequences = self.data_manager.load_sequences(proj)
                if not sequences:
                    self.on_error(f"{proj}: Access denied. Ask for permissions.")

                for seq in sequences:
                    shots = self.data_manager.load_shots(proj, seq)
                    for shot in shots:
                        versions = self.data_manager.load_versions(proj, seq, shot)
                        if len(versions) == 0:
                            self.on_error(f"{proj} {seq} {shot}: No available versions - renderManifest.bty_env_main.usd does not exist.")

            self.data_manager.filter_empty_branches()
            self.on_finished()
        except Exception as e:
            tb = traceback.format_exc()
            self.on_error(f"{e}\n{tb}")


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
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t

    def find_unique_remote_folder_tmp(self, sftp, base_path):
        """Find first candidate base_path-<n> that does not exist, using provided sftp."""
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
        total = len(self.commands)
        if total == 0:
            self.on_finished()
            return

        try:
            for idx, cmd_line in enumerate(self.commands, start=1):
                self.on_log(f"Starting command ({idx}/{total}): {cmd_line}")
                # prepare tokens & folder name
                tokens = cmd_line.split()
                has_format = tokens and tokens[-1] in ("usd", "usda")
                tokens_no_format = tokens[:-1] if has_format else tokens[:]
                command_folder = "-".join(tokens_no_format)

                base_remote = f"/home/svc_vpvfxtransfer_ro/dev/usd_transfer_export/{command_folder}"

                # create temporary ssh + sftp for this command
                ssh = self.backend.create_ssh_client()
                sftp = ssh.open_sftp()

                # find unique output dir (candidate, tmp suffix)
                remote_output_dir, tmp = self.find_unique_remote_folder_tmp(sftp, base_remote)

                # recompose command with tmp inserted (before format if present)
                tokens_mod = tokens[:]
                if has_format:
                    tokens_mod.insert(-1, tmp)
                else:
                    tokens_mod.append(tmp)

                final_cmd_str = " ".join(tokens_mod)
                full_cmd = f"bash {self.remote_script} {final_cmd_str}"

                # run remote script
                stdin, stdout, stderr = ssh.exec_command(full_cmd)
                exit_status = stdout.channel.recv_exit_status()
                out = stdout.read().decode()
                err = stderr.read().decode()
                if exit_status != 0:
                    ssh.close()
                    sftp.close()
                    self.on_error(f"Command failed: {full_cmd}\n{err}")
                    return
                self.on_log(f"Remote script finished ({idx}/{total}). Output:\n{out}")

                # download produced folder -> local_dir
                local_target_dir = self.local_dir
                try:
                    self.backend.download_recursive(sftp, remote_output_dir, local_target_dir)
                    self.on_log(f"Downloaded remote '{remote_output_dir}' to '{local_target_dir}'")
                except Exception as e:
                    sftp.close()
                    ssh.close()
                    self.on_error(f"Download failed for {remote_output_dir}: {e}")
                    return

                # remove remote folder
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
            self.on_error(f"{e}\n{tb}")
