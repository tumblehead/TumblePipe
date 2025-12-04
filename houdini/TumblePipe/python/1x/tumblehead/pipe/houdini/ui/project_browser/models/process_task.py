from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from qtpy.QtCore import Qt, QAbstractTableModel, QModelIndex
from qtpy.QtGui import QBrush, QColor, QStandardItemModel, QStandardItem

from tumblehead.util.uri import Uri


# Custom data roles for tree items
TASK_ROLE = Qt.UserRole + 1      # Store ProcessTask on task items
ENTITY_URI_ROLE = Qt.UserRole + 2  # Store entity URI on entity items
IS_ENTITY_ROLE = Qt.UserRole + 3   # Boolean: True if entity item, False if task item


class TaskStatus(Enum):
    """Status of a process task"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ProcessTask:
    """Represents a single task in a process workflow"""
    id: str                                         # Unique task ID
    uri: Uri                                        # Entity URI being processed
    department: str                                 # Department name
    task_type: str                                  # "export" | "build" | "render"
    description: str                                # Human-readable description
    enabled: bool = True                            # Checkbox state (user can disable)
    status: TaskStatus = TaskStatus.PENDING         # Current status
    error_message: str | None = None                # Error message if failed
    execute_local: Callable[[], None] | None = None # Function to execute locally
    execute_farm: Callable[[], None] | None = None  # Function to execute on farm
    current_version: str | None = None              # Current export version (if known)


class ProcessTaskTableModel(QAbstractTableModel):
    """Model for displaying process tasks in a table with checkboxes"""

    # Column indices
    COLUMN_ENABLED = 0
    COLUMN_TASK = 1
    COLUMN_DEPARTMENT = 2
    COLUMN_VERSION = 3
    COLUMN_STATUS = 4

    # Column headers
    HEADERS = ['', 'Task', 'Department', 'Version', 'Status']

    # Status display mapping
    STATUS_DISPLAY = {
        TaskStatus.PENDING: '\u23F3',    # Hourglass
        TaskStatus.RUNNING: '\u25B6',    # Play symbol
        TaskStatus.COMPLETED: '\u2714',  # Check mark
        TaskStatus.FAILED: '\u2716',     # X mark
        TaskStatus.SKIPPED: '\u23ED',    # Skip symbol
    }

    STATUS_COLORS = {
        TaskStatus.PENDING: '#919191',   # Gray
        TaskStatus.RUNNING: '#4A90E2',   # Blue
        TaskStatus.COMPLETED: '#6bff6b', # Green
        TaskStatus.FAILED: '#ff6b6b',    # Red
        TaskStatus.SKIPPED: '#919191',   # Gray
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tasks: list[ProcessTask] = []

    def set_tasks(self, tasks: list[ProcessTask]):
        """Set the list of tasks to display"""
        self.beginResetModel()
        self._tasks = tasks
        self.endResetModel()

    def get_tasks(self) -> list[ProcessTask]:
        """Get all tasks"""
        return self._tasks

    def get_enabled_tasks(self) -> list[ProcessTask]:
        """Get only enabled tasks"""
        return [t for t in self._tasks if t.enabled]

    def get_enabled_count(self) -> int:
        """Get count of enabled tasks"""
        return len([t for t in self._tasks if t.enabled])

    def set_all_enabled(self, enabled: bool):
        """Enable or disable all tasks"""
        for task in self._tasks:
            task.enabled = enabled
        if self._tasks:
            self.dataChanged.emit(
                self.index(0, self.COLUMN_ENABLED),
                self.index(len(self._tasks) - 1, self.COLUMN_ENABLED)
            )

    def update_task_status(self, task_id: str, status: TaskStatus, error: str | None = None):
        """Update status of a specific task by ID"""
        for i, task in enumerate(self._tasks):
            if task.id == task_id:
                task.status = status
                task.error_message = error
                self.dataChanged.emit(
                    self.index(i, self.COLUMN_STATUS),
                    self.index(i, self.COLUMN_STATUS)
                )
                break

    def reset_all_status(self):
        """Reset all tasks to pending status"""
        for task in self._tasks:
            task.status = TaskStatus.PENDING
            task.error_message = None
        if self._tasks:
            self.dataChanged.emit(
                self.index(0, self.COLUMN_STATUS),
                self.index(len(self._tasks) - 1, self.COLUMN_STATUS)
            )

    # Qt model interface methods

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def rowCount(self, parent=QModelIndex()):
        return len(self._tasks)

    def headerData(self, section, orientation, role):
        if orientation != Qt.Horizontal:
            return None
        if role != Qt.DisplayRole:
            return None
        if 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags

        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable

        # Only the checkbox column is user-checkable
        if index.column() == self.COLUMN_ENABLED:
            flags |= Qt.ItemIsUserCheckable

        return flags

    def data(self, index, role):
        if not index.isValid() or index.row() >= len(self._tasks):
            return None

        task = self._tasks[index.row()]
        column = index.column()

        # Checkbox column
        if column == self.COLUMN_ENABLED:
            if role == Qt.CheckStateRole:
                return Qt.Checked if task.enabled else Qt.Unchecked
            return None

        # Display role
        if role == Qt.DisplayRole:
            if column == self.COLUMN_TASK:
                return task.description
            elif column == self.COLUMN_DEPARTMENT:
                return task.department
            elif column == self.COLUMN_VERSION:
                return task.current_version or '-'
            elif column == self.COLUMN_STATUS:
                return self.STATUS_DISPLAY.get(task.status, '')

        # Tooltip role
        elif role == Qt.ToolTipRole:
            if column == self.COLUMN_TASK:
                return str(task.uri)
            elif column == self.COLUMN_STATUS:
                if task.status == TaskStatus.FAILED and task.error_message:
                    return f"Error: {task.error_message}"
                return task.status.value.capitalize()

        # Text alignment
        elif role == Qt.TextAlignmentRole:
            if column == self.COLUMN_STATUS:
                return Qt.AlignCenter | Qt.AlignVCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        # Background color
        elif role == Qt.BackgroundRole:
            return QBrush(QColor("#3a3a3a"))  # Normal background

        # Foreground color
        elif role == Qt.ForegroundRole:
            if column == self.COLUMN_STATUS:
                color = self.STATUS_COLORS.get(task.status, '#919191')
                return QBrush(QColor(color))
            # Gray out disabled tasks
            if not task.enabled:
                return QBrush(QColor("#666666"))

        return None

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid():
            return False

        if index.row() >= len(self._tasks):
            return False

        task = self._tasks[index.row()]

        # Handle checkbox toggle
        if index.column() == self.COLUMN_ENABLED and role == Qt.CheckStateRole:
            task.enabled = (value == Qt.Checked)
            self.dataChanged.emit(
                self.index(index.row(), 0),
                self.index(index.row(), self.columnCount() - 1)
            )
            return True

        return False


class ProcessTaskTreeModel(QStandardItemModel):
    """Tree model for process tasks grouped by entity URI"""

    # Column indices
    COLUMN_TASK = 0
    COLUMN_DEPARTMENT = 1
    COLUMN_VERSION = 2
    COLUMN_STATUS = 3

    # Column headers
    HEADERS = ['Task', 'Department', 'Version', 'Status']

    # Status display mapping
    STATUS_DISPLAY = {
        TaskStatus.PENDING: '\u23F3',    # Hourglass
        TaskStatus.RUNNING: '\u25B6',    # Play symbol
        TaskStatus.COMPLETED: '\u2714',  # Check mark
        TaskStatus.FAILED: '\u2716',     # X mark
        TaskStatus.SKIPPED: '\u23ED',    # Skip symbol
    }

    STATUS_COLORS = {
        TaskStatus.PENDING: '#919191',   # Gray
        TaskStatus.RUNNING: '#4A90E2',   # Blue
        TaskStatus.COMPLETED: '#6bff6b', # Green
        TaskStatus.FAILED: '#ff6b6b',    # Red
        TaskStatus.SKIPPED: '#919191',   # Gray
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tasks: list[ProcessTask] = []
        self._entity_items: dict[str, QStandardItem] = {}  # uri_str -> entity item
        self._task_items: dict[str, QStandardItem] = {}    # task_id -> task item
        self._updating_checks = False  # Prevent recursive checkbox updates

        self.setHorizontalHeaderLabels(self.HEADERS)
        self.itemChanged.connect(self._on_item_changed)

    def set_tasks(self, tasks: list[ProcessTask]):
        """Build tree from flat task list, grouped by entity URI"""
        self._updating_checks = True
        self.clear()
        self.setHorizontalHeaderLabels(self.HEADERS)
        self._tasks = tasks
        self._entity_items.clear()
        self._task_items.clear()

        # Group tasks by entity URI
        tasks_by_entity: dict[str, list[ProcessTask]] = {}
        for task in tasks:
            uri_str = str(task.uri)
            if uri_str not in tasks_by_entity:
                tasks_by_entity[uri_str] = []
            tasks_by_entity[uri_str].append(task)

        # Build tree
        root = self.invisibleRootItem()
        for uri_str, entity_tasks in tasks_by_entity.items():
            # Create entity parent item
            entity_name = self._uri_name(entity_tasks[0].uri)
            entity_item = QStandardItem(entity_name)
            entity_item.setCheckable(True)
            entity_item.setCheckState(Qt.Checked)
            entity_item.setEditable(False)
            entity_item.setData(uri_str, ENTITY_URI_ROLE)
            entity_item.setData(True, IS_ENTITY_ROLE)

            # Create placeholder columns for entity row
            entity_cols = [entity_item]
            for _ in range(len(self.HEADERS) - 1):
                placeholder = QStandardItem('')
                placeholder.setEditable(False)
                entity_cols.append(placeholder)

            root.appendRow(entity_cols)
            self._entity_items[uri_str] = entity_item

            # Add task children
            for task in entity_tasks:
                task_row = self._create_task_row(task)
                entity_item.appendRow(task_row)
                self._task_items[task.id] = task_row[0]

        self._updating_checks = False

    def _uri_name(self, uri: Uri) -> str:
        """Extract display name from URI"""
        if uri is None:
            return "Unknown"
        _, parts = uri.parts()
        if parts:
            return parts[-1]
        return str(uri)

    def _create_task_row(self, task: ProcessTask) -> list[QStandardItem]:
        """Create a row of items for a task"""
        # Task name column (with checkbox)
        task_item = QStandardItem(task.description)
        task_item.setCheckable(True)
        task_item.setCheckState(Qt.Checked if task.enabled else Qt.Unchecked)
        task_item.setEditable(False)
        task_item.setData(task, TASK_ROLE)
        task_item.setData(False, IS_ENTITY_ROLE)

        # Department column
        dept_item = QStandardItem(task.department)
        dept_item.setEditable(False)

        # Version column
        version_item = QStandardItem(task.current_version or '-')
        version_item.setEditable(False)

        # Status column
        status_item = QStandardItem(self.STATUS_DISPLAY.get(task.status, ''))
        status_item.setEditable(False)
        status_item.setForeground(QBrush(QColor(self.STATUS_COLORS.get(task.status, '#919191'))))

        return [task_item, dept_item, version_item, status_item]

    def _on_item_changed(self, item: QStandardItem):
        """Handle checkbox changes with parent/child sync"""
        if self._updating_checks:
            return

        self._updating_checks = True
        try:
            is_entity = item.data(IS_ENTITY_ROLE)

            if is_entity:
                # Entity checkbox changed - update all children
                check_state = item.checkState()
                for row in range(item.rowCount()):
                    child = item.child(row, 0)
                    if child:
                        child.setCheckState(check_state)
                        task = child.data(TASK_ROLE)
                        if task:
                            task.enabled = (check_state == Qt.Checked)
            else:
                # Task checkbox changed - update task and parent
                task = item.data(TASK_ROLE)
                if task:
                    task.enabled = (item.checkState() == Qt.Checked)

                # Update parent's tri-state
                parent = item.parent()
                if parent:
                    self._update_parent_check_state(parent)
        finally:
            self._updating_checks = False

    def _update_parent_check_state(self, parent: QStandardItem):
        """Update parent checkbox to reflect children states (tri-state)"""
        checked_count = 0
        total_count = parent.rowCount()

        for row in range(total_count):
            child = parent.child(row, 0)
            if child and child.checkState() == Qt.Checked:
                checked_count += 1

        if checked_count == 0:
            parent.setCheckState(Qt.Unchecked)
        elif checked_count == total_count:
            parent.setCheckState(Qt.Checked)
        else:
            parent.setCheckState(Qt.PartiallyChecked)

    def get_tasks(self) -> list[ProcessTask]:
        """Get all tasks in tree order"""
        return self._tasks

    def get_enabled_tasks(self) -> list[ProcessTask]:
        """Get only enabled tasks in tree order"""
        return [t for t in self._tasks if t.enabled]

    def get_enabled_count(self) -> int:
        """Get count of enabled tasks"""
        return len([t for t in self._tasks if t.enabled])

    def set_all_enabled(self, enabled: bool):
        """Enable or disable all tasks"""
        self._updating_checks = True
        try:
            check_state = Qt.Checked if enabled else Qt.Unchecked
            for task in self._tasks:
                task.enabled = enabled
            # Update all entity items
            for entity_item in self._entity_items.values():
                entity_item.setCheckState(check_state)
                for row in range(entity_item.rowCount()):
                    child = entity_item.child(row, 0)
                    if child:
                        child.setCheckState(check_state)
        finally:
            self._updating_checks = False

    def update_task_status(self, task_id: str, status: TaskStatus, error: str | None = None):
        """Update status of a specific task by ID"""
        for task in self._tasks:
            if task.id == task_id:
                task.status = status
                task.error_message = error
                break

        # Update the status item in the tree
        task_item = self._task_items.get(task_id)
        if task_item:
            parent = task_item.parent()
            if parent:
                row = task_item.row()
                status_item = parent.child(row, self.COLUMN_STATUS)
                if status_item:
                    status_item.setText(self.STATUS_DISPLAY.get(status, ''))
                    status_item.setForeground(QBrush(QColor(self.STATUS_COLORS.get(status, '#919191'))))

    def reset_all_status(self):
        """Reset all tasks to pending status"""
        for task in self._tasks:
            task.status = TaskStatus.PENDING
            task.error_message = None

        # Update all status items in the tree
        for task_id, task_item in self._task_items.items():
            parent = task_item.parent()
            if parent:
                row = task_item.row()
                status_item = parent.child(row, self.COLUMN_STATUS)
                if status_item:
                    status_item.setText(self.STATUS_DISPLAY.get(TaskStatus.PENDING, ''))
                    status_item.setForeground(QBrush(QColor(self.STATUS_COLORS.get(TaskStatus.PENDING, '#919191'))))

    def set_mode_filter(self, is_local: bool, current_department: str):
        """
        Enable/disable tasks based on execution mode.

        Local mode: Only current department exports + build tasks are enabled.
        Farm mode: All tasks (including downstream departments) are enabled.
        """
        self._updating_checks = True
        try:
            for task in self._tasks:
                if is_local:
                    # Local: Only current department exports + build allowed
                    if task.task_type == 'export' and task.department != current_department:
                        task.enabled = False
                    else:
                        # Enable current dept exports and build tasks
                        task.enabled = True
                else:
                    # Farm: All tasks enabled
                    task.enabled = True

                # Update UI checkbox for this task
                task_item = self._task_items.get(task.id)
                if task_item:
                    task_item.setCheckState(Qt.Checked if task.enabled else Qt.Unchecked)

            # Update parent checkboxes to reflect new states
            for entity_item in self._entity_items.values():
                self._update_parent_check_state(entity_item)
        finally:
            self._updating_checks = False
