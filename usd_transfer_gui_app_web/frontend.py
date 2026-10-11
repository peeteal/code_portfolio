import os
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QCheckBox, QGroupBox,
    QFileDialog, QMessageBox, QComboBox, QProgressBar, QTextEdit, QFrame, QPlainTextEdit, QDialog
)
from PyQt5.QtCore import Qt

from backend.backend import SSHBackend
from backend.data_manager_qt import DataManager
from backend.workers_qt import FolderLoaderWorker, CommandRunnerWorker
from backend.utils import load_private_file, save_private_file

class TerminalWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("USD Transfer Logs")
        self.resize(800, 400)

        layout = QVBoxLayout(self)
        self.output = QPlainTextEdit(self)
        self.output.setReadOnly(True)
        
        # Terminal-like look
        self.output.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;   /* VSCode dark background */
                color: #d4d4d4;             /* default text color */
                font-family: Consolas, monospace;
                font-size: 12pt;
                border: none;
                padding: 6px;
            }
        """)
        layout.addWidget(self.output)

        # Redirect stdout and stderr
        # sys.stdout = self
        # sys.stderr = self
        
    def write(self, message):
        self.output.appendPlainText(message.strip())
    
    def log_message(self, message: str, is_error: bool = False):
        if is_error:
            colored = f'<span style="color:#f44747;">[ERROR] {message}</span>'
        else:
            colored = f'<span style="color:#d4d4d4;">{message}</span>'
        self.output.appendHtml(colored)

    def flush(self):
        pass

class SSHApp(QWidget):
    
    def __init__(self):
        super().__init__()

        # backend & data manager
        self.backend = SSHBackend()
        self.data = DataManager(self.backend)
        # worker refs
        self.folder_worker = None
        self.command_worker = None

        main_layout = QVBoxLayout()
        

        # UI state
        self.setWindowTitle("USD Transfer")
        self.setGeometry(200, 200, 700, 520)

        
        self.terminal_window = TerminalWindow(self)
        self.show_terminal_button = QPushButton("Show Terminal")
        self.show_terminal_button.clicked.connect(self.toggle_terminal)
        main_layout.addWidget(self.show_terminal_button)

        # Server credentials UI
        self.fill_button = QPushButton("Load User")
        self.fill_button.clicked.connect(self.load_user)
        main_layout.addWidget(self.fill_button)

        main_layout.addWidget(QLabel("Host:"))
        self.host_input = QLineEdit()
        main_layout.addWidget(self.host_input)

        main_layout.addWidget(QLabel("Username:"))
        self.username_input = QLineEdit()
        main_layout.addWidget(self.username_input)

        main_layout.addWidget(QLabel("Password:"))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        main_layout.addWidget(self.password_input)

        # connect / disconnect
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.toggle_connection)
        main_layout.addWidget(self.connect_button)

        # operator group (disabled until connected)
        self.operator_group = QGroupBox()
        self.operator_layout = QVBoxLayout()

        # reload button
        self.load_button = QPushButton("Reload Remote Folders")
        self.load_button.clicked.connect(self.start_folder_load)
        self.operator_layout.addWidget(self.load_button)

        # filter group
        filter_group = QGroupBox("Filter")
        filter_layout = QVBoxLayout()
        self.checkbox_lights = QCheckBox("Lights")
        filter_layout.addWidget(self.checkbox_lights)
        filter_group.setLayout(filter_layout)
        filter_group.setDisabled(True)
        self.operator_layout.addWidget(filter_group)

        # local dir chooser
        dir_layout = QHBoxLayout()
        self.local_dir_input = QLineEdit()
        self.local_dir_input.setPlaceholderText("Enter local export path...")
        browse_button = QPushButton("Browse Local Directories")
        browse_button.clicked.connect(self.browse_directory)
        dir_layout.addWidget(self.local_dir_input)
        dir_layout.addWidget(browse_button)
        self.operator_layout.addLayout(dir_layout)

        # Run button
        self.run_button = QPushButton("Run Command")
        self.run_button.setDisabled(True)
        self.run_button.clicked.connect(self.start_run_commands)
        self.operator_layout.addWidget(self.run_button)

        # Text area (commands)
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("Add commands (one per line). Use project sequence shot version and optional 'usd'/'usda' format.")
        self.text_edit.textChanged.connect(self.empty_commands_text_callback)
        self.operator_layout.addWidget(self.text_edit)

        # format combobox & add line button
        self.format_combo_label = QLabel("Convert to format:")
        self.operator_layout.addWidget(self.format_combo_label)
        self.format_combo = QComboBox()
        self.format_combo.addItems(["Original", "usd", "usda"])
        self.format_combo.setCurrentIndex(0)
        self.operator_layout.addWidget(self.format_combo)

        self.operator_layout.addWidget(QLabel("Available shots:"))
        # hierarchy comboboxes
        self.project_combo = QComboBox()
        self.sequence_combo = QComboBox()
        self.shot_combo = QComboBox()
        self.version_combo = QComboBox()
        self.project_combo.setToolTip("Project")
        self.sequence_combo.setToolTip("Sequence")
        self.shot_combo.setToolTip("Shot")
        self.version_combo.setToolTip("Version")
        self.operator_layout.addWidget(self.project_combo)
        self.operator_layout.addWidget(self.sequence_combo)
        self.operator_layout.addWidget(self.shot_combo)
        self.operator_layout.addWidget(self.version_combo)

        # connect combo callbacks (they only request data from DataManager)
        self.project_combo.currentTextChanged.connect(self.on_project_changed)
        self.sequence_combo.currentTextChanged.connect(self.on_sequence_changed)
        self.shot_combo.currentTextChanged.connect(self.on_shot_changed)

        # add command button
        self.add_command_button = QPushButton("Add Line")
        self.add_command_button.clicked.connect(self.add_command_line)
        self.operator_layout.addWidget(self.add_command_button)

        self.operator_group.setLayout(self.operator_layout)
        self.operator_group.setDisabled(True)
        main_layout.addWidget(self.operator_group)

        # progress overlay (single, created once)
        self.progress_frame = QFrame(self)
        self.progress_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(0, 0, 0, 160);
                border-radius: 8px;
            }
        """)
        self.progress_frame.setVisible(False)

        # overlay layout & widgets (centered)
        #from PyQt5.QtWidgets import QVBoxLayout, QLabel
        overlay_layout = QVBoxLayout(self.progress_frame)
        overlay_layout.setContentsMargins(10, 10, 10, 10)
        overlay_layout.setAlignment(Qt.AlignCenter | Qt.AlignCenter)
        
        
        self.progress_label = QLabel("Working...")
        self.progress_bar = QProgressBar(self.progress_frame)
        self.progress_bar.setGeometry(10, 10, 380, 30)
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setAlignment(Qt.AlignCenter | Qt.AlignCenter)
        overlay_layout.addWidget(self.progress_label)
        overlay_layout.addWidget(self.progress_bar)

        self.setLayout(main_layout)
        self.load_user()
    
    def toggle_terminal(self):
        if not hasattr(self, "terminal_window"):
            self.terminal_window = TerminalWindow(self)

        if self.terminal_window.isVisible():
            self.terminal_window.close()
            self.show_terminal_button.setText("Show Terminal")
        else:
            self.terminal_window.show()
            self.terminal_window.activateWindow()
            self.show_terminal_button.setText("Hide Terminal")
        
    # --- resize overlay to full window ---
    def resizeEvent(self, event):
        self.progress_frame.setGeometry(0, 0, self.width(), self.height())
        self.progress_frame.raise_()
        return super().resizeEvent(event)

    # --- user file load/save ---
    def load_user(self):
        creds = load_private_file()
        self.host_input.setText(creds.get("HOST", ""))
        self.username_input.setText(creds.get("USERNAME", ""))
        self.password_input.setText(creds.get("PASSWORD", ""))
        self.checkbox_lights.setChecked(True)

    def save_user(self):
        save_private_file(self.host_input.text().strip(),
                          self.username_input.text().strip(),
                          self.password_input.text().strip())

    # --- connect / disconnect ---
    def toggle_connection(self):
        if not self.backend.connected:
            try:
                host = self.host_input.text().strip()
                user = self.username_input.text().strip()
                pw = self.password_input.text().strip()
                self.backend.connect(host, user, pw)
                self.host_input.setDisabled(True)
                self.username_input.setDisabled(True)
                self.password_input.setDisabled(True)
                self.connect_button.setText("Disconnect")
                self.operator_group.setDisabled(False)
                QMessageBox.information(self, "Connected", "SSH connection established.")
                # after connect, automatically load project list
                self.start_folder_load()
            except Exception as e:
                QMessageBox.critical(self, "Connection Error", str(e))
        else:
            self.backend.disconnect()
            self.host_input.setDisabled(False)
            self.username_input.setDisabled(False)
            self.password_input.setDisabled(False)
            self.connect_button.setText("Connect")
            self.operator_group.setDisabled(True)
            QMessageBox.information(self, "Disconnected", "SSH connection closed.")

    # --- folder loader (starts worker thread) ---
    def start_folder_load(self):
        # prevent multiple workers
        if self.folder_worker and self.folder_worker.isRunning():
            return

        # reset data caches on data manager
        self.data = DataManager(self.backend)

        self.folder_worker = FolderLoaderWorker(self.data)
        self.folder_worker.progress.connect(self.on_folder_progress)
        self.folder_worker.finished.connect(self.on_folder_finished)
        self.folder_worker.error.connect(self.on_folder_error)
        

        # show overlay with total projects (we don't have total yet until worker starts;
        # worker will emit progress with total)
        self.show_progress_overlay("Loading projects...", 0)
        self.folder_worker.start()

    def on_folder_progress(self, current, total, project_name):
        # update overlay and keep UI responsive
        #self.terminal_window.log_message(f"Processing {current} / {total}: {project_name}", False)
        self.update_progress_overlay(current, total, f"Processing {project_name} ({current}/{total})")

    def on_folder_finished(self):
        # folder worker finished - populate UI combos from data manager
        self.populate_project_combo()
        self.hide_progress_overlay()
        QMessageBox.information(self, "Done", "Loaded remote folder structure.")

    def on_folder_error(self, msg):
        self.terminal_window.log_message(msg, True)
        #self.hide_progress_overlay()
        #QMessageBox.warning(self, "Folder Load Error", msg)

    def populate_project_combo(self):
        self.project_combo.clear()
        projects = self.data.projects
        if projects:
            self.project_combo.addItems(projects)

    # --- combo cascade callbacks (frontend only triggers data_manager calls) ---
    def on_project_changed(self, project):
        self.sequence_combo.clear()
        if not project:
            return
        sequences = self.data.sequences_per_project.get(project) or self.data.load_sequences(project)
        self.sequence_combo.addItems(sequences)

    def on_sequence_changed(self, sequence):
        self.shot_combo.clear()
        project = self.project_combo.currentText()
        if not project or not sequence:
            return
        shots = self.data.shots_per_sequence.get(project + sequence) or self.data.load_shots(project, sequence)
        self.shot_combo.addItems(shots)

    def on_shot_changed(self, shot):
        self.version_combo.clear()
        project = self.project_combo.currentText()
        sequence = self.sequence_combo.currentText()
        if not (project and sequence and shot):
            return
        versions = self.data.versions_per_shot.get(project + sequence + shot) or self.data.load_versions(project, sequence, shot)
        self.version_combo.addItems(versions)

    # --- progress overlay helpers ---
    def show_progress_overlay(self, label, maximum):
        self.progress_label.setText(label)
        self.progress_bar.setMaximum(maximum if maximum > 0 else 1)
        self.progress_bar.setValue(0)
        self.progress_frame.setVisible(True)
        QApplication.processEvents()

    def update_progress_overlay(self, current, total, label=None):
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
        if label:
            self.progress_label.setText(label)
        QApplication.processEvents()

    def hide_progress_overlay(self):
        self.progress_frame.setVisible(False)
        QApplication.processEvents()

    # --- add line to text area ---
    def add_command_line(self):
        proj = self.project_combo.currentText()
        seq = self.sequence_combo.currentText()
        shot = self.shot_combo.currentText()
        ver = self.version_combo.currentText()
        if not (proj and seq and shot and ver):
            QMessageBox.warning(self, "Missing", "Make sure project/sequence/shot/version are selected before adding a line.")
            return
        cmd = f"{proj} {seq} {shot} {ver}"
        if self.format_combo.currentText() != "Original":
            cmd = f"{cmd} {self.format_combo.currentText()}"
        self.text_edit.append(cmd)

    def empty_commands_text_callback(self):
        self.run_button.setDisabled(len(self.text_edit.toPlainText()) == 0)

    # --- Run commands via worker thread (no logic in UI) ---
    def start_run_commands(self):
        if self.command_worker and self.command_worker.isRunning():
            return

        if not self.backend.connected:
            QMessageBox.warning(self, "Input Error", "Please connect first.")
            return

        local_dir = self.local_dir_input.text().strip()
        if not local_dir:
            QMessageBox.warning(self, "Input Error", "Please select local directory.")
            return
        if not os.path.isdir(local_dir):
            QMessageBox.warning(self, "Input Error", "Local directory does not exist.")
            return

        commands = [ln.strip() for ln in self.text_edit.toPlainText().splitlines() if ln.strip()]
        if not commands:
            QMessageBox.warning(self, "Input Error", "No commands to run.")
            return

        # show overlay with total count
        self.show_progress_overlay("Executing commands...", len(commands))

        # start worker
        self.command_worker = CommandRunnerWorker(self.backend, commands, local_dir)
        self.command_worker.progress.connect(self.on_command_progress)
        self.command_worker.log.connect(self.on_command_log)
        self.command_worker.finished.connect(self.on_command_finished)
        self.command_worker.error.connect(self.on_command_error)
        self.command_worker.start()

    def on_command_progress(self, current, total):
        self.update_progress_overlay(current, total, f"Processed {current} / {total}")
        self.terminal_window.log_message(f"Processed {current} / {total}", False)

    def on_command_log(self, msg):
        self.terminal_window.log_message(msg, False)
        print(msg)

    def on_command_finished(self):
        self.hide_progress_overlay()
        self.terminal_window.log_message("Finished.\nAll commands processed successfully.", False)
        QMessageBox.information(self, "Finished", "All commands processed successfully.")

    def on_command_error(self, msg):
        self.hide_progress_overlay()
        self.terminal_window.log_message(msg, True)
        QMessageBox.warning(self, "Command Error", msg)

    # --- browse directory ---
    def browse_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Directory")
        if directory:
            self.local_dir_input.setText(directory)
