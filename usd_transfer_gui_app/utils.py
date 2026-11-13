import os

def load_private_file(filename=".private"):
    """
    Read KEY=VALUE pairs (e.g. HOST, USERNAME, PASSWORD) from a credentials file.

    Blank lines and lines starting with # are ignored, and keys are upper-cased.
    Returns a dict, which is empty if the file does not exist.
    """
    creds = {}
    if not os.path.exists(filename):
        return creds
    with open(filename, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                creds[key.strip().upper()] = value.strip()
    return creds


def save_private_file(host, username, password, filename=".private"):
    """Write host, username and password to the credentials file as plain-text KEY=VALUE lines."""
    with open(filename, "w") as f:
        f.write(f"HOST={host}\n")
        f.write(f"USERNAME={username}\n")
        f.write(f"PASSWORD={password}\n")
