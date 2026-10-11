import os

def load_private_file(filename=".private"):
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
    with open(filename, "w") as f:
        f.write(f"HOST={host}\n")
        f.write(f"USERNAME={username}\n")
        f.write(f"PASSWORD={password}\n")
