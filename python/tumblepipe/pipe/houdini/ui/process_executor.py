"""Process executor: the Qt task-runner and its validation-session state.

Runs a list of ProcessTasks sequentially (local or farm), with layer_split
deduplication and per-run validation-choice memory. This is the pure execution
engine — task *collection* and *building* live in ``task_collection`` /
``task_factory``, and the node → dialog entry point in ``dialog_launcher``.
"""

from contextlib import contextmanager
import traceback
import sys

from qtpy.QtCore import QObject, Signal, QTimer

from .process_task import ProcessTask, TaskStatus


class _SilentStream:
    def __init__(self, original):
        self._original = original

    def write(self, text):
        return len(text) if text else 0

    def flush(self):
        pass

    def __getattr__(self, name):
        return getattr(self._original, name)


@contextmanager
def _silence_output():
    # Wrapper must delegate fileno/isatty/buffer to the real stream — Houdini's
    # native progress dialog introspects sys.stdout and gets stuck if those
    # attributes are missing (as with a bare StringIO).
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    try:
        sys.stdout = _SilentStream(old_stdout)
        sys.stderr = _SilentStream(old_stderr)
        yield
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


class ValidationSession:
    """Tracks validation state across multiple validation tasks in a single execution.

    When a user clicks "Remember choice" in the validation dialog, the choice
    is stored here and applied to subsequent validations in the same session.
    """

    # Constants matching ValidationConfirmDialog
    CONTINUE = 1
    CANCEL = 2

    def __init__(self):
        self.remembered_choice: int | None = None

    def reset(self):
        """Reset session state for new execution."""
        self.remembered_choice = None

    def has_remembered_choice(self) -> bool:
        return self.remembered_choice is not None

    def get_remembered_choice(self) -> int | None:
        return self.remembered_choice

    def set_remembered_choice(self, choice: int):
        self.remembered_choice = choice


# Module-level session instance
_validation_session = ValidationSession()


class ProcessExecutor(QObject):
    """Executes process tasks sequentially with progress signals"""

    task_started = Signal(str)           # task_id
    task_completed = Signal(str)         # task_id
    task_failed = Signal(str, str)       # task_id, error_message
    all_completed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tasks: list[ProcessTask] = []
        self._mode: str = 'local'
        self._is_running: bool = False
        self._current_index: int = 0
        self._executed_split_paths: set[str] = set()  # Track executed layer_split nodes

    def set_tasks(self, tasks: list[ProcessTask]):
        """Set the list of tasks to execute"""
        self._tasks = tasks

    def set_mode(self, mode: str):
        """Set execution mode: 'local' or 'farm'"""
        self._mode = mode

    def is_running(self) -> bool:
        """Check if executor is currently running"""
        return self._is_running

    def cancel(self):
        """Cancel execution (will stop after current task)"""
        self._is_running = False

    def _check_dependencies(self, task: ProcessTask) -> tuple[bool, str | None]:
        """Check if all dependencies completed successfully.

        Returns:
            (can_run, skip_reason) - can_run=True if deps satisfied, else skip_reason explains why
        """
        if not task.depends_on:
            return True, None

        for dep_id in task.depends_on:
            dep_task = next((t for t in self._tasks if t.id == dep_id), None)
            if dep_task is None:
                continue  # Dependency not in task list, ignore
            if dep_task.status == TaskStatus.FAILED:
                return False, f"Dependency '{dep_task.description}' failed"
            if dep_task.status == TaskStatus.SKIPPED and dep_task.enabled:
                # Only block if dependency was skipped due to its own failed dependency
                # (not because user disabled it)
                return False, f"Dependency '{dep_task.description}' was skipped"
            if dep_task.status == TaskStatus.PENDING:
                return False, f"Dependency '{dep_task.description}' not yet executed"
        return True, None

    def execute(self):
        """Start executing enabled tasks"""
        self._is_running = True
        self._current_index = 0
        self._executed_split_paths.clear()  # Reset deduplication tracking
        _validation_session.reset()  # Reset validation choices for new run
        # Use QTimer to allow UI updates between tasks
        QTimer.singleShot(0, self._execute_next_task)

    def _execute_next_task(self):
        """Execute the next enabled task in the queue"""
        if not self._is_running:
            self.all_completed.emit()
            return

        # Skip disabled tasks
        while self._current_index < len(self._tasks):
            task = self._tasks[self._current_index]
            if task.enabled:
                break
            task.status = TaskStatus.SKIPPED
            self._current_index += 1

        # Check if all tasks are done
        if self._current_index >= len(self._tasks):
            self._is_running = False
            self.all_completed.emit()
            return

        task = self._tasks[self._current_index]

        # Check dependencies before running
        can_run, skip_reason = self._check_dependencies(task)
        if not can_run:
            task.status = TaskStatus.SKIPPED
            task.error_message = skip_reason
            self._current_index += 1
            QTimer.singleShot(0, self._execute_next_task)
            return

        task.status = TaskStatus.RUNNING
        self.task_started.emit(task.id)

        try:
            if task.children:
                # Parent task with children - execute each enabled child
                self._execute_grouped_task(task)
            else:
                # Regular task - execute directly
                self._execute_single_task(task)

            task.status = TaskStatus.COMPLETED
            self.task_completed.emit(task.id)

        except Exception as e:
            # Check if this is a user-initiated cancellation (no error dialog needed)
            from .validation_dialog import ValidationCancelled
            if isinstance(e, ValidationCancelled):
                task.status = TaskStatus.SKIPPED
                # Don't emit task_failed - this was intentional
            else:
                task.status = TaskStatus.FAILED
                # Capture full traceback for debugging
                error_msg = traceback.format_exc()
                task.error_message = error_msg
                self.task_failed.emit(task.id, task.error_message)

        self._current_index += 1
        # Continue with next task after a short delay to allow UI update
        QTimer.singleShot(100, self._execute_next_task)

    def _execute_single_task(self, task: ProcessTask):
        """Execute a single task (no children)"""
        if self._mode == 'local':
            if task.execute_local is not None:
                with _silence_output():
                    task.execute_local()
            else:
                raise RuntimeError("No local executor defined for task")
        else:  # farm
            # Farm mode - let output go to farm logs (no capture)
            if task.execute_farm is not None:
                task.execute_farm()
            else:
                raise RuntimeError("No farm executor defined for task")

    def _execute_grouped_task(self, parent_task: ProcessTask):
        """Execute a grouped task by running its enabled children.

        Handles deduplication for layer_split nodes to avoid executing
        the same node multiple times when shared across variants.
        """
        if not parent_task.children:
            return

        for child in parent_task.children:
            if not child.enabled:
                child.status = TaskStatus.SKIPPED
                continue

            # Deduplication for layer_split nodes
            if child.task_type == 'export_shared' and child.node_path:
                if child.node_path in self._executed_split_paths:
                    # Already executed this layer_split, skip
                    child.status = TaskStatus.SKIPPED
                    continue
                self._executed_split_paths.add(child.node_path)

            child.status = TaskStatus.RUNNING
            self.task_started.emit(child.id)

            try:
                if self._mode == 'local':
                    if child.execute_local is not None:
                        with _silence_output():
                            child.execute_local()
                    else:
                        raise RuntimeError(f"No local executor defined for child task: {child.description}")
                else:  # farm
                    if child.execute_farm is not None:
                        child.execute_farm()
                    else:
                        raise RuntimeError(f"No farm executor defined for child task: {child.description}")

                child.status = TaskStatus.COMPLETED
                self.task_completed.emit(child.id)

            except Exception as e:
                # Check if this is a user-initiated cancellation
                from .validation_dialog import ValidationCancelled
                if isinstance(e, ValidationCancelled):
                    child.status = TaskStatus.SKIPPED
                    raise  # Re-raise to skip parent task too
                else:
                    child.status = TaskStatus.FAILED
                    child.error_message = traceback.format_exc()
                    self.task_failed.emit(child.id, child.error_message)
                    # Re-raise to fail the parent task
                    raise

