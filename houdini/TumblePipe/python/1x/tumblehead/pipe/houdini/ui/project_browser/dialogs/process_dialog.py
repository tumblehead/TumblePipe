"""Process dialog for executing publish and other workflow tasks"""

from typing import Callable

from qtpy import QtWidgets
from qtpy.QtCore import Qt, Signal

from ..models.process_task import ProcessTask, ProcessTaskTreeModel, TaskStatus, TASK_ROLE, IS_ENTITY_ROLE
from ..utils.process_executor import ProcessExecutor


class ProcessDialog(QtWidgets.QDialog):
    """Dialog for executing process tasks with local/farm mode selection"""

    process_completed = Signal(dict)  # {completed: [...], failed: [...], skipped: [...]}

    def __init__(
        self,
        title: str,
        tasks: list[ProcessTask],
        current_department: str | None = None,
        pre_execute_callback: Callable[[], None] | None = None,
        parent=None
    ):
        super().__init__(parent)
        self._title = title
        self._executor = ProcessExecutor(self)
        self._is_executing = False
        self._pre_execute_callback = pre_execute_callback
        self._current_department = current_department

        # Setup model (tree model groups tasks by entity)
        self._model = ProcessTaskTreeModel()
        self._model.set_tasks(tasks)

        # Setup UI
        self.setWindowTitle(f"Process: {title}")
        self.resize(700, 500)
        self.setModal(True)

        self._create_ui()
        self._connect_signals()

        # Update mode visibility based on available task callbacks
        self._update_mode_visibility()

        # Apply initial mode filter (Local is default)
        if self._current_department:
            self._model.set_mode_filter(is_local=True, current_department=self._current_department)

        self._update_status()

    def _create_ui(self):
        """Create the dialog UI"""
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        self.setLayout(layout)

        # Task tree (grouped by entity)
        self._tree_view = QtWidgets.QTreeView()
        self._tree_view.setModel(self._model)
        self._tree_view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._tree_view.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._tree_view.setAlternatingRowColors(True)
        self._tree_view.setExpandsOnDoubleClick(True)
        self._tree_view.expandAll()  # Start with all entities expanded

        # Enable context menu for "Open Location"
        self._tree_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree_view.customContextMenuRequested.connect(self._show_context_menu)

        # Set column widths
        header = self._tree_view.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)  # Task
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)  # Department
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)  # Version
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.Fixed)  # Status
        self._tree_view.setColumnWidth(3, 60)  # Status column

        layout.addWidget(self._tree_view)

        # Execution mode section
        self._mode_group = QtWidgets.QGroupBox("Execution Mode")
        mode_layout = QtWidgets.QHBoxLayout()
        self._mode_group.setLayout(mode_layout)

        self._local_radio = QtWidgets.QRadioButton("Local")
        self._local_radio.setToolTip("Execute tasks locally in the current Houdini session")
        self._local_radio.setChecked(True)

        self._farm_radio = QtWidgets.QRadioButton("Farm")
        self._farm_radio.setToolTip("Submit tasks to the render farm")

        mode_layout.addWidget(self._local_radio)
        mode_layout.addWidget(self._farm_radio)
        mode_layout.addStretch()

        layout.addWidget(self._mode_group)

        # Status section
        status_frame = QtWidgets.QFrame()
        status_frame.setFrameStyle(QtWidgets.QFrame.StyledPanel)
        status_layout = QtWidgets.QHBoxLayout()
        status_layout.setContentsMargins(10, 5, 10, 5)
        status_frame.setLayout(status_layout)

        self._status_label = QtWidgets.QLabel("Ready")
        self._status_label.setStyleSheet("color: gray;")
        status_layout.addWidget(self._status_label)

        layout.addWidget(status_frame)

        # Button section
        button_layout = QtWidgets.QHBoxLayout()

        # Selection buttons
        self._select_all_button = QtWidgets.QPushButton("Select All")
        self._select_all_button.clicked.connect(self._select_all)
        button_layout.addWidget(self._select_all_button)

        self._select_none_button = QtWidgets.QPushButton("Select None")
        self._select_none_button.clicked.connect(self._select_none)
        button_layout.addWidget(self._select_none_button)

        button_layout.addStretch()

        # Execute/Cancel buttons
        self._execute_button = QtWidgets.QPushButton("Execute")
        self._execute_button.setDefault(True)
        self._execute_button.setStyleSheet("background-color: #4A90E2; color: white;")
        self._execute_button.clicked.connect(self._on_execute_clicked)
        button_layout.addWidget(self._execute_button)

        self._cancel_button = QtWidgets.QPushButton("Cancel")
        self._cancel_button.clicked.connect(self._on_cancel_clicked)
        button_layout.addWidget(self._cancel_button)

        layout.addLayout(button_layout)

    def _connect_signals(self):
        """Connect executor signals to UI updates"""
        self._executor.task_started.connect(self._on_task_started)
        self._executor.task_completed.connect(self._on_task_completed)
        self._executor.task_failed.connect(self._on_task_failed)
        self._executor.all_completed.connect(self._on_all_completed)

        # Update status when model changes (tree model uses itemChanged)
        self._model.itemChanged.connect(lambda _: self._update_status())

        # Connect mode radio buttons to filter tasks
        self._local_radio.toggled.connect(self._on_mode_changed)
        self._farm_radio.toggled.connect(self._on_mode_changed)

    def _select_all(self):
        """Select all tasks"""
        self._model.set_all_enabled(True)
        self._update_status()

    def _select_none(self):
        """Deselect all tasks"""
        self._model.set_all_enabled(False)
        self._update_status()

    def _on_mode_changed(self):
        """Update task enablement based on execution mode"""
        if self._current_department is None:
            return

        is_local = self._local_radio.isChecked()
        self._model.set_mode_filter(is_local, self._current_department)
        self._update_status()

    def _update_status(self):
        """Update status label based on current state"""
        if self._is_executing:
            return  # Don't update during execution

        enabled_count = self._model.get_enabled_count()
        total_count = len(self._model.get_tasks())

        if total_count == 0:
            self._status_label.setText("No tasks available")
            self._status_label.setStyleSheet("color: red; font-weight: bold;")
            self._execute_button.setEnabled(False)
        elif enabled_count == 0:
            self._status_label.setText("No tasks selected")
            self._status_label.setStyleSheet("color: orange; font-weight: bold;")
            self._execute_button.setEnabled(False)
        else:
            # Only show mode in status if mode group is visible
            if self._mode_group.isVisible():
                mode = "locally" if self._local_radio.isChecked() else "on farm"
                self._status_label.setText(f"Ready to execute {enabled_count} task(s) {mode}")
            else:
                self._status_label.setText(f"Ready to execute {enabled_count} task(s)")
            self._status_label.setStyleSheet("color: green; font-weight: bold;")
            self._execute_button.setEnabled(True)

    def _get_available_modes(self) -> tuple[bool, bool]:
        """Check which execution modes are available based on task callbacks.

        Returns: (has_local, has_farm)
        """
        tasks = self._model.get_tasks()
        has_local = any(t.execute_local is not None for t in tasks)
        has_farm = any(t.execute_farm is not None for t in tasks)
        return has_local, has_farm

    def _update_mode_visibility(self):
        """Show/hide mode radio buttons based on available task callbacks."""
        has_local, has_farm = self._get_available_modes()

        # Hide individual radio buttons if mode not available
        self._local_radio.setVisible(has_local)
        self._farm_radio.setVisible(has_farm)

        # Hide entire group if only one mode available (no choice needed)
        if has_local and not has_farm:
            self._mode_group.setVisible(False)
            self._local_radio.setChecked(True)
        elif has_farm and not has_local:
            self._mode_group.setVisible(False)
            self._farm_radio.setChecked(True)
        else:
            self._mode_group.setVisible(True)

    def _on_cancel_clicked(self):
        """Handle cancel button click"""
        if self._is_executing:
            # Cancel execution
            self._executor.cancel()
            self._status_label.setText("Cancelling...")
            self._status_label.setStyleSheet("color: orange; font-weight: bold;")
        else:
            # Close dialog
            self.reject()

    def _on_execute_clicked(self):
        """Handle execute button click"""
        enabled_tasks = self._model.get_enabled_tasks()
        if not enabled_tasks:
            return

        # Run pre-execute callback (e.g., save scene) before starting tasks
        if self._pre_execute_callback is not None:
            try:
                self._status_label.setText("Preparing...")
                self._status_label.setStyleSheet("color: #4A90E2; font-weight: bold;")
                QtWidgets.QApplication.processEvents()  # Allow UI to update
                self._pre_execute_callback()
            except Exception as e:
                QtWidgets.QMessageBox.critical(
                    self, "Pre-Execute Error",
                    f"Failed to prepare for execution:\n{str(e)}"
                )
                return

        # Set execution state
        self._is_executing = True
        self._set_ui_executing(True)

        # Reset task statuses
        self._model.reset_all_status()

        # Configure executor
        mode = 'local' if self._local_radio.isChecked() else 'farm'
        self._executor.set_tasks(self._model.get_tasks())
        self._executor.set_mode(mode)

        # Start execution
        self._status_label.setText("Executing...")
        self._status_label.setStyleSheet("color: #4A90E2; font-weight: bold;")
        self._executor.execute()

    def _set_ui_executing(self, executing: bool):
        """Enable/disable UI elements during execution"""
        self._tree_view.setEnabled(not executing)
        self._local_radio.setEnabled(not executing)
        self._farm_radio.setEnabled(not executing)
        self._select_all_button.setEnabled(not executing)
        self._select_none_button.setEnabled(not executing)
        self._execute_button.setEnabled(not executing)

        if executing:
            self._cancel_button.setText("Cancel")
        else:
            self._cancel_button.setText("Close")

    def _on_task_started(self, task_id: str):
        """Handle task started signal"""
        self._model.update_task_status(task_id, TaskStatus.RUNNING)

        # Find task and update status label
        for task in self._model.get_tasks():
            if task.id == task_id:
                self._status_label.setText(f"Running: {task.description}")
                break

        # Force UI repaint
        QtWidgets.QApplication.processEvents()

    def _on_task_completed(self, task_id: str):
        """Handle task completed signal"""
        self._model.update_task_status(task_id, TaskStatus.COMPLETED)

        # Force UI repaint
        QtWidgets.QApplication.processEvents()

    def _on_task_failed(self, task_id: str, error: str):
        """Handle task failed signal"""
        self._model.update_task_status(task_id, TaskStatus.FAILED, error)

        # Force UI repaint
        QtWidgets.QApplication.processEvents()

    def _on_all_completed(self):
        """Handle all tasks completed signal"""
        self._is_executing = False
        self._set_ui_executing(False)

        # Count results
        tasks = self._model.get_tasks()
        completed = [t for t in tasks if t.status == TaskStatus.COMPLETED]
        failed = [t for t in tasks if t.status == TaskStatus.FAILED]
        skipped = [t for t in tasks if t.status == TaskStatus.SKIPPED]

        # Update status
        if failed:
            self._status_label.setText(
                f"Completed: {len(completed)}, Failed: {len(failed)}, Skipped: {len(skipped)}"
            )
            self._status_label.setStyleSheet("color: orange; font-weight: bold;")

            # Show error report dialog
            self._show_error_report(failed)
        else:
            self._status_label.setText(
                f"All tasks completed ({len(completed)} succeeded, {len(skipped)} skipped)"
            )
            self._status_label.setStyleSheet("color: green; font-weight: bold;")

        # Emit results
        self.process_completed.emit({
            'completed': [t.id for t in completed],
            'failed': [(t.id, t.error_message) for t in failed],
            'skipped': [t.id for t in skipped],
        })

    def _show_error_report(self, failed_tasks: list):
        """Show a dialog with details of failed tasks"""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Error Report")
        dialog.resize(700, 500)

        layout = QtWidgets.QVBoxLayout(dialog)

        # Header
        header = QtWidgets.QLabel(f"{len(failed_tasks)} task(s) failed:")
        header.setStyleSheet("font-weight: bold; color: #ff6b6b;")
        layout.addWidget(header)

        # Error text (selectable, copyable)
        text_edit = QtWidgets.QPlainTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)

        report_lines = []
        for task in failed_tasks:
            report_lines.append("=" * 70)
            report_lines.append(f"TASK: {task.description}")
            report_lines.append(f"Entity: {task.uri}")
            report_lines.append(f"Department: {task.department}")
            report_lines.append(f"Type: {task.task_type}")
            report_lines.append("-" * 70)
            report_lines.append("ERROR:")
            report_lines.append(task.error_message or "No error message available")
            report_lines.append("")

        text_edit.setPlainText("\n".join(report_lines))
        layout.addWidget(text_edit)

        # Button layout
        button_layout = QtWidgets.QHBoxLayout()

        copy_btn = QtWidgets.QPushButton("Copy to Clipboard")
        copy_btn.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(text_edit.toPlainText())
        )
        button_layout.addWidget(copy_btn)

        button_layout.addStretch()

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        close_btn.setDefault(True)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

        dialog.exec_()

    def get_results(self) -> dict:
        """Get execution results"""
        tasks = self._model.get_tasks()
        return {
            'completed': [t for t in tasks if t.status == TaskStatus.COMPLETED],
            'failed': [t for t in tasks if t.status == TaskStatus.FAILED],
            'skipped': [t for t in tasks if t.status == TaskStatus.SKIPPED],
        }

    def _show_context_menu(self, position):
        """Show context menu for tree items"""
        index = self._tree_view.indexAt(position)
        if not index.isValid():
            return

        # Get the item at column 0 (where task data is stored)
        item = self._model.itemFromIndex(index.siblingAtColumn(0))
        if item is None:
            return

        # Check if this is a task item (not entity parent)
        is_entity = item.data(IS_ENTITY_ROLE)
        if is_entity:
            return  # Don't show menu for entity headers

        # Get the task
        task = item.data(TASK_ROLE)
        if task is None:
            return

        # Only show "Open Location" for completed tasks
        if task.status != TaskStatus.COMPLETED:
            return

        menu = QtWidgets.QMenu(self)
        open_location_action = menu.addAction("Open Location")

        selected_action = menu.exec_(self._tree_view.mapToGlobal(position))
        if selected_action == open_location_action:
            self._open_task_location(task)

    def _open_task_location(self, task: ProcessTask):
        """Open the output location for a completed task"""
        import hou
        from tumblehead.api import path_str
        from tumblehead.pipe.paths import latest_export_path, current_staged_path

        try:
            if task.task_type == 'export':
                # Open export directory (using 'default' variant)
                location_path = latest_export_path(task.uri, 'default', task.department)
            elif task.task_type == 'build':
                # Open staged (build) directory
                location_path = current_staged_path(task.uri)
            else:
                return

            if location_path is None:
                return

            # Open in file browser
            path = path_str(location_path)
            if location_path.is_dir():
                path += "/"
            hou.ui.showInFileBrowser(path)

        except Exception as e:
            print(f"Failed to open location: {e}")
