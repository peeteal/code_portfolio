# backend.py
import os
import stat
import paramiko
from backend.utils import load_private_file

class SSHBackend:
    """
    Low-level SSH/SFTP operations. Keeps a persistent connection for list/stat ops
    (used by the UI/data loader), and also can create temporary SSH clients
    for worker threads to avoid sharing a single paramiko client across threads.
    """

    def __init__(self):
        self.ssh = None
        self.sftp = None
        self.connected = False
        self.host = None
        self.username = None
        self.password = None
        
        creds = load_private_file()
        self.connect(creds["HOST"], creds["USERNAME"], creds["PASSWORD"])

    def connect(self, host: str, username: str, password: str):
        """Create a persistent connection used for directory listing/stat operations."""
        self.host = host
        self.username = username
        self.password = password

        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(host, username=username, password=password)
        self.sftp = self.ssh.open_sftp()
        self.connected = True
        print("SSH Connected")

    def disconnect(self):
        if self.sftp:
            try:
                self.sftp.close()
            except Exception:
                pass
            self.sftp = None
        if self.ssh:
            try:
                self.ssh.close()
            except Exception:
                pass
            self.ssh = None
        self.connected = False

    def create_ssh_client(self):
        """Create and return a new SSHClient connected with stored credentials."""
        if not (self.host and self.username and self.password):
            raise RuntimeError("Backend has no stored credentials.")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(self.host, username=self.username, password=self.password)
        return client

    # ----------------
    # Basic operations
    # ----------------
    def exec_remote_command(self, command: str, use_temp_client: bool = False):
        """
        Execute `command` on remote host.
        If use_temp_client True: create new temporary SSHClient and close it afterwards.
        Returns (exit_status, stdout_str, stderr_str).
        """
        if use_temp_client:
            ssh = self.create_ssh_client()
            try:
                stdin, stdout, stderr = ssh.exec_command(command)
                exit_status = stdout.channel.recv_exit_status()
                out = stdout.read().decode()
                err = stderr.read().decode()
            finally:
                try:
                    ssh.close()
                except Exception:
                    pass
            return exit_status, out, err
        else:
            if not self.ssh:
                raise RuntimeError("Not connected")
            stdin, stdout, stderr = self.ssh.exec_command(command)
            exit_status = stdout.channel.recv_exit_status()
            return exit_status, stdout.read().decode(), stderr.read().decode()

    def listdir_attr(self, path: str):
        """Return list of SFTPAttributes or raise exception (frontend/data will handle)."""
        if not self.sftp:
            raise RuntimeError("SFTP not available")
        return self.sftp.listdir_attr(path)

    def file_exists(self, path: str) -> bool:
        """Return True if path exists (file or dir)."""
        if not self.sftp:
            raise RuntimeError("SFTP not available")
        try:
            self.sftp.stat(path)
            return True
        except IOError:
            return False

    def is_remote_dir(self, path: str) -> bool:
        """Return True if path exists and is a directory."""
        if not self.sftp:
            raise RuntimeError("SFTP not available")
        try:
            attr = self.sftp.stat(path)
            return stat.S_ISDIR(attr.st_mode)
        except (IOError, FileNotFoundError, PermissionError):
            return False

    # --------------------------
    # Recursive download / delete
    # These functions can work with either the persistent sftp or a provided one.
    # --------------------------
    def download_recursive(self, sftp, remote_dir: str, local_dir: str):
        """
        Recursively download a remote directory using provided sftp client.
        sftp should be an SFTPClient (could be self.sftp or one opened from a temporary SSHClient).
        """
        os.makedirs(local_dir, exist_ok=True)
        try:
            entries = sftp.listdir_attr(remote_dir)
        except IOError as exc:
            # remote dir not accessible or doesn't exist
            raise exc

        for entry in entries:
            remote_path = f"{remote_dir}/{entry.filename}"
            local_path = os.path.join(local_dir, entry.filename)
            # detect directory via mode
            if stat.S_ISDIR(entry.st_mode):
                self.download_recursive(sftp, remote_path, local_path)
            else:
                sftp.get(remote_path, local_path)

    def rmdir_recursive(self, sftp, remote_path: str):
        """
        Recursively remove remote directory using provided sftp client.
        """
        try:
            entries = sftp.listdir_attr(remote_path)
        except IOError:
            # path doesn't exist or not accessible
            return

        for entry in entries:
            path = f"{remote_path}/{entry.filename}"
            if stat.S_ISDIR(entry.st_mode):
                self.rmdir_recursive(sftp, path)
            else:
                try:
                    sftp.remove(path)
                except IOError:
                    pass
        # finally remove the directory itself
        try:
            sftp.rmdir(remote_path)
        except IOError:
            pass
