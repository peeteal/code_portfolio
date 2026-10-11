import os
import threading
import queue
from flask import Flask, render_template, request, jsonify, flash, stream_with_context, Response, redirect, url_for
from backend.backend import SSHBackend
from backend.data_manager_qt import DataManager
from backend.workers import CommandRunnerWorker
from backend.utils import load_private_file, save_private_file

app = Flask(__name__)
app.secret_key = "dev-secret"

backend = SSHBackend()
data = DataManager(backend)

# SSE listener management
clients = []

_clients_lock = threading.Lock()
_current_run_thread = None
_run_lock = threading.Lock()

def run_commands_worker(commands, local_dir):
    def worker_thread():
        try:
            # worker = CommandRunnerWorker(backend, commands, local_dir)

            # # Patch signals to broadcast directly
            # worker.log.connect(lambda msg: broadcast(f"LOG|{msg}"))
            # worker.progress.connect(lambda idx, tot: broadcast(f"PROGRESS|{idx}|{tot}|Processed {idx}/{tot}"))
            # worker.error.connect(lambda msg: broadcast(f"LOG|ERROR: {msg}"))
            # worker.finished.connect(lambda: broadcast("PROGRESS_DONE"))

            # # Run the QThread's run() method **directly** (blocking)
            # worker.run()
            worker = CommandRunnerWorker(
                backend,
                commands,
                local_dir,
                on_log=lambda msg: broadcast(f"LOG|{msg}"),
                on_progress=lambda i, tot: broadcast(f"PROGRESS|{i}|{tot}|Processed {i}/{tot}"),
                on_error=lambda msg: broadcast(f"LOG|ERROR: {msg}"),
                on_finished=lambda: broadcast("PROGRESS_DONE"),
            )
            worker.start()

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            broadcast(f"LOG|ERROR: {e}\n{tb}")
            broadcast("PROGRESS_DONE")

    t = threading.Thread(target=worker_thread, daemon=True)
    t.start()

@app.route("/", methods=["GET", "POST"])
def index():
    # import time
    # for i in range(10):
    #     broadcast(f"LOG|Test message {i}")
    #     time.sleep(1)
    selected_project = ""
    selected_sequence = ""
    selected_shot = ""
    selected_version = ""
    commands = ""

    if request.method == "POST":
        commands = request.form.get("commands", "")

        if "load_private" in request.form:
            creds = load_private_file()
            if not creds:
                flash(".private not found", "error")
            else:
                flash("Loaded .private", "success")

        elif "connect" in request.form:
            host = request.form.get("host", "").strip()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            try:
                backend.connect(host, username, password)
                data.load_projects("/upp/servers/vfxstore2/VFXstore/PROJECTS/")
                msg = "Connected successfully"
                broadcast(f"LOG|{msg}")
                flash("Connected successfully", "success")
            except Exception as e:
                flash(f"Connection error: {e}", "error")

        elif "disconnect" in request.form:
            try:
                backend.disconnect()
                data.projects = []
                data.sequences_per_project = {}
                data.shots_per_sequence = {}
                data.versions_per_shot = {}
                msg = "Disconnected"
                broadcast(f"LOG|{msg}")
                flash("Disconnected", "info")
            except Exception as e:
                flash(f"Error disconnecting: {e}", "error")

        elif "add_line" in request.form:
            selected_project = request.form.get("project", "")
            selected_sequence = request.form.get("sequence", "")
            selected_shot = request.form.get("shot", "")
            selected_version = request.form.get("version", "")
            fmt = request.form.get("format", "Original")

            if not (selected_project and selected_sequence and selected_shot and selected_version):
                flash("Please select project/sequence/shot/version before adding a line.", "error")
            else:
                new_cmd = f"{selected_project} {selected_sequence} {selected_shot} {selected_version}"
                if fmt and fmt != "Original":
                    new_cmd = f"{new_cmd} {fmt}"

                if commands.strip():
                    commands = commands.rstrip() + "\n" + new_cmd
                else:
                    commands = new_cmd
                flash(f"Added command: {new_cmd}", "success")
                broadcast(f"LOG|Added command: {new_cmd}")

        elif "save_private" in request.form:
            host = request.form.get("host", "").strip()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            try:
                save_private_file(host, username, password)
                flash("Saved .private", "success")
            except Exception as e:
                flash(f"Failed to save: {e}", "error")

        elif "run" in request.form:
            if not backend.connected:
                flash("Please connect before running commands.", "error")
            else:
                local_dir = request.form.get("local_dir", "").strip()
                if not local_dir:
                    flash("Please specify local directory (server-side path).", "error")
                elif not os.path.isdir(local_dir):
                    flash("Local directory does not exist on server.", "error")
                else:
                    cmd_list = [ln.strip() for ln in commands.splitlines() if ln.strip()]
                    if not cmd_list:
                        flash("No commands to run.", "error")
                    else:
                        global _current_run_thread
                        with _run_lock:
                            if _current_run_thread and _current_run_thread.is_alive():
                                flash("A run is already in progress.", "error")
                            else:
                                t = threading.Thread(target=run_commands_worker, args=(cmd_list, local_dir), daemon=True)
                                _current_run_thread = t
                                t.start()
                                flash(f"Started background run of {len(cmd_list)} command(s). Open the terminal to watch logs.", "info")
                
                        return redirect(url_for("index"))
                                                # connected=backend.connected,
                                                # selected_project=selected_project,
                                                # selected_sequence=selected_sequence,
                                                # selected_shot=selected_shot,
                                                # selected_version=selected_version)) #TODO: keep data after refresh
        elif "reload" in request.form:
            if not backend.connected:
                flash("Please connect before running commands.", "error")
            else:
                data.load_projects("/upp/servers/vfxstore2/VFXstore/PROJECTS/")
                flash("Reloaded project content.")
                broadcast("LOG|Reloaded project content.")

        selected_project = request.form.get("project", "")
        selected_sequence = request.form.get("sequence", "")
        selected_shot = request.form.get("shot", "")
        selected_version = request.form.get("version", "")
        #return redirect(url_for("index"))
        
    # Build page context
    projects = getattr(data, "projects", []) or []
    sequences = []
    shots = []
    versions = []
    try:
        if backend.connected:
            if selected_project:
                sequences = data.sequences_per_project.get(selected_project) or data.load_sequences(selected_project)
            if selected_project and selected_sequence:
                shots = data.shots_per_sequence.get(selected_project + selected_sequence) or data.load_shots(selected_project, selected_sequence)
            if selected_project and selected_sequence and selected_shot:
                versions = data.versions_per_shot.get(selected_project + selected_sequence + selected_shot) or data.load_versions(selected_project, selected_sequence, selected_shot)
    except Exception as e:
        flash(f"Error loading selections: {e}", "error")

    creds = {}
    if backend.connected:
        creds["HOST"] = getattr(backend, "host", "")
        creds["USERNAME"] = getattr(backend, "username", "")
        creds["PASSWORD"] = getattr(backend, "password", "")
    else:
        creds = load_private_file()

    return render_template(
        "index.html",
        connected=backend.connected,
        creds=creds,
        projects=projects,
        sequences=sequences,
        shots=shots,
        versions=versions,
        selected_project=selected_project,
        selected_sequence=selected_sequence,
        selected_shot=selected_shot,
        selected_version=selected_version,
        commands=commands
    )


@app.route("/api/sequences")
def api_sequences():
    project = request.args.get("project", "")
    if not backend.connected or not project:
        return jsonify([])
    try:
        seqs = data.load_sequences(project)
        return jsonify(seqs)
    except Exception:
        return jsonify([])


@app.route("/api/shots")
def api_shots():
    project = request.args.get("project", "")
    sequence = request.args.get("sequence", "")
    if not backend.connected or not (project and sequence):
        return jsonify([])
    try:
        shots = data.load_shots(project, sequence)
        return jsonify(shots)
    except Exception:
        return jsonify([])


@app.route("/api/versions")
def api_versions():
    project = request.args.get("project", "")
    sequence = request.args.get("sequence", "")
    shot = request.args.get("shot", "")
    if not backend.connected or not (project and sequence and shot):
        return jsonify([])
    try:
        versions = data.load_versions(project, sequence, shot)
        return jsonify(versions)
    except Exception:
        return jsonify([])
    
# @app.route("/api/reload_projects", methods=["POST"])
# def reload_projects():
#     """
#     Explicitly rebuild the cache by scanning the filesystem.
#     Returns the list of available projects.
#     """
#     data.reload_all()
#     return jsonify({"projects": data.get_projects()})


# @app.route("/api/projects", methods=["GET"])
# def get_projects():
#     return jsonify({"projects": data.get_projects()})


# @app.route("/api/sequences", methods=["GET"])
# def get_sequences():
#     project = request.args.get("project")
#     return jsonify({"sequences": data.get_sequences(project)})


# @app.route("/api/shots", methods=["GET"])
# def get_shots():
#     project = request.args.get("project")
#     sequence = request.args.get("sequence")
#     return jsonify({"shots": data.get_shots(project, sequence)})


# @app.route("/api/versions", methods=["GET"])
# def get_versions():
#     project = request.args.get("project")
#     sequence = request.args.get("sequence")
#     shot = request.args.get("shot")
#     return jsonify({"versions": data.get_versions(project, sequence, shot)})

def broadcast(msg: str):
    """Send a message to all connected clients, splitting multi-line messages."""
    lines = msg.splitlines() or [""]
    for q in clients:
        for line in lines:
            try:
                q.put_nowait(line)
            except queue.Full:
                print("Warning: active listener queue full")

def message_stream():
    """Generator for SSE, sending messages to a single client."""
    q = queue.Queue()
    clients.append(q)
    print(f"Number of clients: {len(clients)}")

    try:
        while True:
            try:
                # Wait for new message or timeout for keep-alive
                msg = q.get(timeout=5)
                # Split multi-line messages
                for line in msg.splitlines() or [""]:
                    yield f"data: {line}\n\n"
            except queue.Empty:
                # Send keep-alive to prevent client disconnect
                yield ": keep-alive\n\n"
    finally:
        print("Removing client.")
        clients.remove(q)

@app.route('/stream')
def stream():
    return Response(stream_with_context(message_stream()), mimetype='text/event-stream')


if __name__ == "__main__":
    app.run(debug=False, threaded=True)
    