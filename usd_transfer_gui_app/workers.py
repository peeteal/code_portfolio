from PyQt5.QtCore import QThread, pyqtSignal
import traceback
from backend import SSHBackend

class FolderLoaderWorker(QThread):
    """
    Loads full project/sequence/shot/version hierarchy using DataManager + backend.sftp.
    Emits progress, log, finished, error signals.
    """
    progress = pyqtSignal(int, int, str)      # current, total, project_name
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, data_manager, root_path="/upp/servers/vfxstore2/VFXstore/PROJECTS/"):
        super().__init__()
        self.data_manager = data_manager
        self.root_path = root_path

    def run(self):
        log = []
        try:
            projects = self.data_manager.load_projects(self.root_path)
            total = len(projects)
            for i, proj in enumerate(projects, start=1):
                self.progress.emit(i, total, proj)
                sequences = self.data_manager.load_sequences(proj)
                if not sequences:
                    self.error.emit(f"{proj}: Access denied. Ask for permissions.")

                for seq in sequences:
                    shots = self.data_manager.load_shots(proj, seq)
                    for shot in shots:
                        _ = self.data_manager.load_versions(proj, seq, shot)
                        if len(_) == 0:
                            self.error.emit(f"{proj} {seq} {shot}: No available versions - renderManifest.bty_env_main.usd does not exist.")
                            
            self.data_manager.filter_empty_branches()
            self.finished.emit()
        except Exception as e:
            tb = traceback.format_exc()
            self.error.emit(f"{e}\n{tb}")


class CommandRunnerWorker(QThread):
    """
    Runs the provided list of commands (strings). For each command it:
      - creates a temporary SSH client (safer for multithreaded usage),
      - runs the remote script,
      - downloads remote output dir to local_dir (using temporary SFTP),
      - removes remote output dir.
    Emits progress(index,total), log messages, finished and error.
    """
    progress = pyqtSignal(int, int)       # processed, total
    log = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, backend: SSHBackend, commands: str, local_dir, remote_script="/home/svc_vpvfxtransfer_ro/dev/usd_transfer/run.sh"):
        super().__init__()
        self.backend = backend
        self.commands = commands[:]
        self.local_dir = local_dir
        self.remote_script = remote_script

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
            self.finished.emit()
            return

        try:
            for idx, cmd_line in enumerate(self.commands, start=1):
                self.log.emit(f"Starting command ({idx}/{total}): {cmd_line}")
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

                # run remote script on this temporary ssh
                stdin, stdout, stderr = ssh.exec_command(full_cmd)
                exit_status = stdout.channel.recv_exit_status()
                out = stdout.read().decode()
                err = stderr.read().decode()
                if exit_status != 0:
                    ssh.close()
                    sftp.close()
                    self.error.emit(f"Command failed: {full_cmd}\n{err}")
                    return
                self.log.emit(f"Remote script finished ({idx}/{total}). Output:\n{out}")

                # download produced folder -> local_dir/<command_folder>-<tmp>
                #local_target_dir = os.path.join(self.local_dir, os.path.basename(remote_output_dir))
                local_target_dir = self.local_dir
                try:
                    self.backend.download_recursive(sftp, remote_output_dir, local_target_dir)
                    self.log.emit(f"Downloaded remote '{remote_output_dir}' to '{local_target_dir}'")
                except Exception as e:
                    sftp.close()
                    ssh.close()
                    self.error.emit(f"Download failed for {remote_output_dir}: {e}")
                    return

                # remove remote folder
                try:
                    self.backend.rmdir_recursive(sftp, remote_output_dir)
                    self.log.emit(f"Removed remote folder {remote_output_dir}")
                except Exception:
                    # not fatal; log and continue
                    self.log.emit(f"Could not remove remote folder {remote_output_dir} (ignored)")

                sftp.close()
                ssh.close()

                self.progress.emit(idx, total)

            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))
