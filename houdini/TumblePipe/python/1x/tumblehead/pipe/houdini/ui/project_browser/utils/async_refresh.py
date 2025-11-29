"""Async utilities for project browser refresh operations"""

from qtpy.QtCore import QThread, Signal, QObject
import traceback

from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments


class AsyncRefreshWorker(QThread):
    """Background worker for async refresh operations"""

    progress_updated = Signal(int, str)  # progress percentage, status message
    data_ready = Signal(object)  # refreshed data
    error_occurred = Signal(str)  # error message
    finished = Signal()

    def __init__(self, api, refresh_operations=None):
        super().__init__()
        self._api = api
        self._refresh_operations = refresh_operations or []
        self._cancelled = False

    def cancel(self):
        """Cancel the refresh operation"""
        self._cancelled = True

    def run(self):
        """Execute refresh operations in background"""
        try:
            total_operations = len(self._refresh_operations)

            for i, (operation_name, operation_func) in enumerate(self._refresh_operations):
                if self._cancelled:
                    return

                # Update progress
                progress = int((i / total_operations) * 100)
                self.progress_updated.emit(progress, f"Loading {operation_name}...")

                # Execute operation
                result = operation_func()

                # Emit result
                self.data_ready.emit((operation_name, result))

            # Complete
            self.progress_updated.emit(100, "Complete")

        except Exception as e:
            error_msg = f"Error during async refresh: {str(e)}\n{traceback.format_exc()}"
            self.error_occurred.emit(error_msg)
        finally:
            self.finished.emit()


class AsyncRefreshManager(QObject):
    """Manager for coordinating async refresh operations"""

    refresh_complete = Signal()
    refresh_error = Signal(str)
    refresh_progress = Signal(int, str)

    def __init__(self, api, parent=None):
        super().__init__(parent)
        self._api = api
        self._worker = None
        self._cached_data = {}

    def start_refresh(self, components_to_refresh=None):
        """Start async refresh of specified components"""
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()

        # Define refresh operations
        operations = []

        if not components_to_refresh or 'workspace' in components_to_refresh:
            operations.extend([
                ('asset_entities', lambda: self._api.config.list_entities(Uri.parse_unsafe('entity:/assets'), closure=True)),
                ('shot_entities', lambda: self._api.config.list_entities(Uri.parse_unsafe('entity:/shots'), closure=True)),
            ])

        if not components_to_refresh or 'departments' in components_to_refresh:
            operations.extend([
                ('asset_departments', lambda: [d.name for d in list_departments('assets')]),
                ('shot_departments', lambda: [d.name for d in list_departments('shots')]),
            ])

        # Create and start worker
        self._worker = AsyncRefreshWorker(self._api, operations)
        self._worker.progress_updated.connect(self.refresh_progress)
        self._worker.data_ready.connect(self._handle_data_ready)
        self._worker.error_occurred.connect(self.refresh_error)
        self._worker.finished.connect(self._handle_finished)
        self._worker.start()

    def cancel_refresh(self):
        """Cancel ongoing refresh operation"""
        if self._worker and self._worker.isRunning():
            self._worker.cancel()

    def _handle_data_ready(self, data):
        """Handle data received from worker"""
        operation_name, result = data
        self._cached_data[operation_name] = result

    def _handle_finished(self):
        """Handle worker completion"""
        self.refresh_complete.emit()

    def get_cached_data(self, operation_name):
        """Get cached data from async operations"""
        return self._cached_data.get(operation_name)

    def clear_cache(self):
        """Clear cached data"""
        self._cached_data.clear()


def create_enhanced_refresh_operations(api):
    """Create a list of refresh operations that can be run async"""
    operations = [
        ('workspace_config', lambda: {
            'asset_entities': api.config.list_entities(Uri.parse_unsafe('entity:/assets'), closure=True),
            'shot_entities': api.config.list_entities(Uri.parse_unsafe('entity:/shots'), closure=True),
        }),
        ('department_config', lambda: {
            'asset_departments': [d.name for d in list_departments('assets')],
            'shot_departments': [d.name for d in list_departments('shots')],
        }),
    ]
    return operations