"""Main ProjectBrowser component"""

from pathlib import Path

from qtpy.QtCore import Qt, QTimer
from qtpy import QtWidgets
import hou
from hou import qt as hqt

from tumblehead.api import is_dev, path_str, default_client
from tumblehead.config.timeline import BlockRange, get_frame_range
from tumblehead.config.groups import get_group
from tumblehead.util.uri import Uri
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini.ui.util import (
    center_all_network_editors,
    vulkan_all_scene_viewers,
)
from tumblehead.pipe.houdini import util
from tumblehead.pipe.houdini.lops import (
    build_shot,
    import_shot,
    export_asset_layer,
    export_shot_layer,
    export_render_layer,
    import_assets,
    import_asset_layer,
    import_shot_layer,
    import_render_layer,
)
from tumblehead.pipe.houdini.sops import export_rig, import_rigs
from tumblehead.pipe.houdini.cops import build_comp
from tumblehead.pipe.paths import get_workfile_context, Context
from tumblehead.util.io import store_json
from tumblehead.naming import random_name

from .constants import AUTO_SETTINGS_DEFAULT, Section, Action, Location, FrameRangeMode
from .helpers import (
    next_file_path,
    save_context,
    save_entity_context,
    load_module,
    file_path_from_context,
    latest_export_path_from_context,
    get_entity_type,
)
from .views import WorkspaceBrowser, DepartmentBrowser, DetailsView, VersionView, SettingsView
from .utils.async_refresh import AsyncRefreshManager

api = default_client()


class ProjectBrowser(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # Members
        self._context = None
        self._selected_entity = None  # Uri | None - the selected entity
        self._selected_department = None
        self._auto_settings = AUTO_SETTINGS_DEFAULT.copy()

        # Initialize async refresh manager
        self._async_refresh_manager = AsyncRefreshManager(api, self)

        # Settings
        self.setObjectName("ProjectBrowser")
        self.setMinimumHeight(0)

        # Set the grid layout
        layout = QtWidgets.QGridLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        self.setLayout(layout)

        # Equally stretch the columns
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 1)

        # Create the workspace header with label and refresh button
        workspace_header_widget = QtWidgets.QWidget()
        workspace_header_layout = QtWidgets.QHBoxLayout()
        workspace_header_layout.setContentsMargins(0, 0, 0, 0)
        workspace_header_layout.setSpacing(5)
        workspace_header_widget.setLayout(workspace_header_layout)

        # Create the workspace label
        workspace_label = QtWidgets.QLabel("Workspace DEV" if is_dev() else "Workspace")
        workspace_label.setAlignment(Qt.AlignCenter)
        workspace_header_layout.addWidget(workspace_label)

        # Create the global refresh button
        self._global_refresh_button = QtWidgets.QPushButton()
        self._global_refresh_button.setIcon(hqt.Icon("NETVIEW_reload_needsupdate"))
        self._global_refresh_button.setToolTip("Refresh all project browser data")
        self._global_refresh_button.setMaximumWidth(30)
        self._global_refresh_button.clicked.connect(self._global_refresh)
        workspace_header_layout.addWidget(self._global_refresh_button)

        # Create the database editor button
        self._database_editor_button = QtWidgets.QPushButton()
        self._database_editor_button.setIcon(hqt.Icon("SOP_file"))
        self._database_editor_button.setToolTip("Open Database Editor")
        self._database_editor_button.setMaximumWidth(30)
        self._database_editor_button.clicked.connect(self._open_database_editor)
        workspace_header_layout.addWidget(self._database_editor_button)

        # Create the rebuild nodes button
        self._rebuild_nodes_button = QtWidgets.QPushButton()
        self._rebuild_nodes_button.setIcon(hqt.Icon("SHELF_push"))
        self._rebuild_nodes_button.setToolTip("Rebuild all TH nodes in scene")
        self._rebuild_nodes_button.setMaximumWidth(30)
        self._rebuild_nodes_button.clicked.connect(self._rebuild_selected_nodes)
        workspace_header_layout.addWidget(self._rebuild_nodes_button)

        # Database editor window reference
        self._database_window = None

        layout.addWidget(workspace_header_widget, 0, 0)

        # Create the workspace browser
        self._workspace_browser = WorkspaceBrowser(api)
        layout.addWidget(self._workspace_browser, 1, 0)

        # Create the department label
        department_label = QtWidgets.QLabel("Department")
        department_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(department_label, 0, 1)

        # Create the department browser
        self._department_browser = DepartmentBrowser(api)
        layout.addWidget(self._department_browser, 1, 1)

        # Create the tabbed view
        self._tabbed_view = QtWidgets.QTabWidget()
        self._tabbed_view.setStyleSheet("QTabWidget::pane { border: 0; }")
        layout.addWidget(self._tabbed_view, 0, 2, 2, 1)

        # Create the details view
        self._details_view = DetailsView()
        self._tabbed_view.addTab(self._details_view, "Details")

        # Create the version view
        self._version_view = VersionView()
        self._tabbed_view.addTab(self._version_view, "Versions")

        # Create the settings view
        self._settings_view = SettingsView()
        self._tabbed_view.addTab(self._settings_view, "Settings")

        # Connect the signals
        self._workspace_browser.selection_changed.connect(self._workspace_changed)
        self._workspace_browser.open_location.connect(self._workspace_open_path)
        self._workspace_browser.create_entry.connect(self._create_batch_entry)
        self._workspace_browser.create_batch_entry.connect(self._create_batch_entry)
        self._workspace_browser.remove_entry.connect(self._remove_entry)
        self._workspace_browser.create_group.connect(self._create_group)
        self._workspace_browser.edit_group.connect(self._edit_group)
        self._workspace_browser.delete_group.connect(self._delete_group)
        self._department_browser.selection_changed.connect(self._department_changed)
        self._department_browser.open_location.connect(self._department_open_location)
        self._department_browser.reload_scene.connect(self._department_reload_scene)
        self._department_browser.new_from_current.connect(
            self._department_new_from_current
        )
        self._department_browser.new_from_template.connect(
            self._department_new_from_template
        )
        self._details_view.save_scene.connect(self._save_scene)
        self._details_view.refresh_scene.connect(self._refresh_scene)
        self._details_view.publish_scene.connect(self._publish_scene_clicked)
        self._details_view.open_scene_info.connect(self._open_scene_info)
        self._details_view.open_location.connect(self._open_location)
        self._details_view.set_frame_range.connect(self._set_frame_range)
        self._version_view.open_location.connect(self._open_workspace_location)
        self._version_view.open_version.connect(self._open_version)
        self._version_view.revive_version.connect(self._revive_version)
        self._settings_view.auto_refresh_changed.connect(self._on_auto_refresh_changed)

        # Setup auto-refresh timer (60 seconds)
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._global_refresh)
        self._auto_refresh_timer.start(60000)  # 60000 ms = 60 seconds

    def refresh(self):
        self._details_view.refresh()
        self._version_view.refresh()
        self._department_browser.refresh()
        self._workspace_browser.refresh()

    def _global_refresh(self):
        """Enhanced global refresh with state preservation and user feedback"""
        # Check if async refresh is already running
        if hasattr(self, '_refresh_in_progress') and self._refresh_in_progress:
            return

        try:
            # Mark refresh as in progress
            self._refresh_in_progress = True

            # Show visual feedback on refresh button
            self._show_refresh_spinner()

            # Detect current workfile context if not already set or sync with current Houdini file
            current_context = self._context
            try:
                # Get current Houdini file path and derive context from it
                current_hip_file = hou.hipFile.path()
                if current_hip_file and not hou.hipFile.isNewFile():
                    current_file_path = Path(current_hip_file)
                    detected_context = get_workfile_context(current_file_path)
                    if detected_context is not None:
                        current_context = detected_context
                        # Update internal context if it was None or different
                        if self._context != detected_context:
                            self._context = detected_context
            except Exception:
                # If context detection fails, use existing context
                pass

            # Preserve current state (using detected context if available)
            # Store both the context and individual selections for robustness
            self._preserved_context = current_context
            self._preserved_entity = self._selected_entity
            self._preserved_department = self._selected_department
            self._preserved_tab_index = self._tabbed_view.currentIndex()

            # Store entity-based selection (more reliable than index-based)
            self._preserved_workspace_selection = self._workspace_browser.get_selection()

            # Also preserve department name separately for additional robustness
            self._preserved_department_name = None
            if current_context is not None:
                self._preserved_department_name = current_context.department_name
            elif self._selected_department is not None:
                self._preserved_department_name = self._selected_department[0]  # department_name from tuple

            # Disable the refresh button to prevent multiple refreshes
            self._global_refresh_button.setEnabled(False)

            # Connect async refresh manager signals
            self._async_refresh_manager.refresh_progress.connect(self._handle_refresh_progress)
            self._async_refresh_manager.refresh_complete.connect(self._handle_refresh_complete)
            self._async_refresh_manager.refresh_error.connect(self._handle_refresh_error)

            # Start async refresh
            self._async_refresh_manager.start_refresh(['workspace', 'departments'])

        except Exception as e:
            self._handle_refresh_error(f"Error starting refresh: {str(e)}")

    def _handle_refresh_progress(self, progress, message):
        """Handle progress updates from async refresh"""
        # No longer need to update progress dialog - visual feedback is on button
        pass

    def _handle_refresh_complete(self):
        """Handle completion of async refresh"""
        try:
            # Refresh UI components with preserved state and individual error handling
            try:
                self._workspace_browser.refresh()
            except Exception as e:
                raise RuntimeError(f"Failed to refresh workspace browser: {e}")

            try:
                self._department_browser.refresh()
            except Exception as e:
                raise RuntimeError(f"Failed to refresh department browser: {e}")

            try:
                self._version_view.refresh()
            except Exception as e:
                raise RuntimeError(f"Failed to refresh version view: {e}")

            try:
                self._details_view.refresh()
            except Exception as e:
                raise RuntimeError(f"Failed to refresh details view: {e}")

            # Restore state (most critical step)
            self._restore_preserved_state()

            # Flash success
            self._flash_refresh_success()

        except Exception as e:
            self._handle_refresh_error(f"Error completing refresh: {str(e)}")
        finally:
            self._cleanup_refresh()

    def _handle_refresh_error(self, error_message):
        """Handle errors during refresh"""
        hou.ui.displayMessage(
            f"Error during refresh: {error_message}\n\nPartial refresh may have occurred.",
            severity=hou.severityType.Warning
        )
        self._cleanup_refresh()

    def _restore_preserved_state(self):
        """Restore the preserved UI state with comprehensive error handling"""

        try:
            # Step 1: Restore workspace selection (most critical)
            if hasattr(self, '_preserved_workspace_selection') and self._preserved_workspace_selection:
                try:
                    self._workspace_browser.select(self._preserved_workspace_selection)
                    # Verify selection was actually restored
                    current_selection = self._workspace_browser.get_selection()
                    assert current_selection == self._preserved_workspace_selection, f"Workspace selection restoration failed: expected {self._preserved_workspace_selection}, got {current_selection}"
                except Exception as e:
                    raise RuntimeError(f"Failed to restore workspace selection: {e}")

            # Step 2: Restore department and context information
            if hasattr(self, '_preserved_context') and self._preserved_context:
                try:
                    entity_uri = self._preserved_context.entity_uri
                    assert entity_uri is not None, f"Failed to get entity_uri from preserved context: {self._preserved_context}"

                    # Set entity in department browser
                    self._department_browser.set_entity(entity_uri)

                    # Select department by name
                    if self._preserved_context.department_name:
                        self._department_browser.select(self._preserved_context.department_name)

                    # Update detail and version views
                    self._details_view.set_context(self._preserved_context)
                    self._version_view.set_context(self._preserved_context)

                    # Select version if available
                    if self._preserved_context.version_name:
                        self._version_view.select(self._preserved_context.version_name)

                    # Update internal state
                    self._selected_entity = self._preserved_context.entity_uri
                    self._selected_department = (self._preserved_context.department_name, self._preserved_context.version_name)
                    self._context = self._preserved_context
                except Exception as e:
                    raise RuntimeError(f"Failed to restore context: {e}")


            # Step 4: Restore tab selection
            if hasattr(self, '_preserved_tab_index'):
                try:
                    # Validate tab index is within bounds
                    assert 0 <= self._preserved_tab_index < self._tabbed_view.count(), f"Invalid preserved tab index: {self._preserved_tab_index}"
                    self._tabbed_view.setCurrentIndex(self._preserved_tab_index)
                except Exception as e:
                    raise RuntimeError(f"Failed to restore tab selection: {e}")

        except Exception as e:
            raise RuntimeError(f"Critical error during state restoration: {e}")

    def _cleanup_refresh(self):
        """Clean up after refresh operation"""
        try:
            # Disconnect signals safely - each disconnect is wrapped to prevent cascade failures
            if hasattr(self, '_async_refresh_manager'):
                try:
                    self._async_refresh_manager.refresh_progress.disconnect(self._handle_refresh_progress)
                except (TypeError, RuntimeError):
                    pass  # Signal was not connected or already disconnected

                try:
                    self._async_refresh_manager.refresh_complete.disconnect(self._handle_refresh_complete)
                except (TypeError, RuntimeError):
                    pass  # Signal was not connected or already disconnected

                try:
                    self._async_refresh_manager.refresh_error.disconnect(self._handle_refresh_error)
                except (TypeError, RuntimeError):
                    pass  # Signal was not connected or already disconnected

            # Hide refresh spinner
            self._hide_refresh_spinner()

            # Re-enable refresh button
            if hasattr(self, '_global_refresh_button'):
                self._global_refresh_button.setEnabled(True)

            # Mark refresh as no longer in progress
            self._refresh_in_progress = False

            # Clear preserved state
            preserved_attrs = ['_preserved_context', '_preserved_workspace', '_preserved_department',
                              '_preserved_tab_index', '_preserved_workspace_selection', '_preserved_department_name']
            for attr in preserved_attrs:
                if hasattr(self, attr):
                    try:
                        delattr(self, attr)
                    except AttributeError:
                        pass  # Already deleted

        except Exception as e:
            raise RuntimeError(f"Error during refresh cleanup: {e}")

    def _show_refresh_spinner(self):
        """Show loading state on refresh button"""
        self._refresh_button_original_style = self._global_refresh_button.styleSheet()
        # Add blue border to indicate loading
        self._global_refresh_button.setStyleSheet(
            "QPushButton { border: 3px solid #4A90E2; border-radius: 3px; }"
        )

    def _hide_refresh_spinner(self):
        """Hide loading state on refresh button"""
        if hasattr(self, '_refresh_button_original_style'):
            self._global_refresh_button.setStyleSheet(self._refresh_button_original_style)

    def _flash_refresh_success(self):
        """Flash refresh button green to indicate success"""
        # Set green background
        self._global_refresh_button.setStyleSheet(
            "QPushButton { background-color: rgba(76, 175, 80, 0.6); border-radius: 3px; }"
        )

        # Return to normal after brief flash
        from qtpy.QtCore import QTimer
        QTimer.singleShot(600, lambda: self._hide_refresh_spinner())

    def _create_group(self, name_path):
        """Open group editor dialog to create a new group

        Args:
            name_path: Path list like ["groups"] or ["groups", "shots"]
        """
        from tumblehead.pipe.houdini.ui.project_browser.dialogs import GroupEditorDialog

        # Determine context from name_path if available
        context = None
        if len(name_path) >= 2:
            context = name_path[1]  # "shots" or "assets"

        dialog = GroupEditorDialog(api, group=None, context=context, parent=self)
        if dialog.exec_():
            # Refresh workspace browser after dialog closes
            self.refresh()

    def _edit_group(self, name_path):
        """Open group editor dialog for the specified group

        Args:
            name_path: Path list like ["groups", "shots", "group_name"]
        """
        from tumblehead.pipe.houdini.ui.project_browser.dialogs import GroupEditorDialog
        from tumblehead.config.groups import list_groups

        if len(name_path) < 3:
            return

        context = name_path[1]  # "shots" or "assets"
        group_name = name_path[2]

        # Find the group
        groups = list_groups(context)
        group = next((g for g in groups if g.name == group_name), None)
        if group is None:
            hou.ui.displayMessage(
                f"Group '{group_name}' not found.",
                severity=hou.severityType.Error
            )
            return

        dialog = GroupEditorDialog(api, group=group, parent=self)
        if dialog.exec_():
            # Refresh workspace browser after dialog closes
            self.refresh()

    def _delete_group(self, name_path):
        """Delete a group after user confirmation

        Args:
            name_path: Path list like ["groups", "shots", "group_name"]
        """
        from tumblehead.config.groups import remove_group

        if len(name_path) < 3:
            return

        context = name_path[1]  # "shots" or "assets"
        group_name = name_path[2]
        group_uri = Uri.parse_unsafe(f'groups:/{context}/{group_name}')

        # Confirm deletion
        result = QtWidgets.QMessageBox.question(
            self,
            "Delete Group",
            f"Are you sure you want to delete the group '{group_name}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if result != QtWidgets.QMessageBox.Yes:
            return

        # Delete the group
        try:
            remove_group(group_uri)
        except Exception as e:
            hou.ui.displayMessage(
                f"Error deleting group: {str(e)}",
                severity=hou.severityType.Error
            )
            return

        # Refresh workspace browser
        self.refresh()

    def _open_database_editor(self):
        """Open or focus the database editor window (non-modal)"""
        if self._database_window is None or not self._database_window.isVisible():
            from .windows import DatabaseWindow
            self._database_window = DatabaseWindow(api, parent=self)
            self._database_window.data_changed.connect(self._on_database_changed)
            self._database_window.window_closed.connect(self._on_database_window_closed)
            self._database_window.show()
        else:
            self._database_window.raise_()
            self._database_window.activateWindow()

    def _on_database_changed(self, uri):
        """Handle data changes from database editor"""
        if uri is None:
            return
        purpose = uri.purpose
        if purpose == 'entity':
            self._workspace_browser.refresh()
        elif purpose == 'departments':
            self._department_browser.refresh()
        elif purpose in ('config', 'schemas'):
            self.refresh()

    def _on_database_window_closed(self):
        """Handle database window closure"""
        self._database_window = None

    def _rebuild_selected_nodes(self):
        """Rebuild selected TH nodes in place, preserving settings and connections."""
        from tumblehead.pipe.houdini.tools.rebuild_nodes import rebuild_nodes
        rebuild_nodes()

    def _selection(self):
        if self._selected_entity is None:
            return None
        if self._selected_department is None:
            return None
        department_name, version_name = self._selected_department
        return Context(
            entity_uri=self._selected_entity,
            department_name=department_name,
            version_name=version_name,
        )

    def _select(self, context):
        self._selected_entity = context.entity_uri
        self._selected_department = (context.department_name, context.version_name)

    def _update_ui_from_context(self, context):
        """Update all UI components to reflect the given context

        This is the canonical method for updating all browser views after
        a context change (saving, creating new versions, etc.). It ensures
        consistent UI state across all components.

        Args:
            context: The Context to display
        """
        entity_uri = context.entity_uri

        # Update workspace browser (tree view) - this was often missing!
        self._workspace_browser.select(entity_uri)

        # Update department browser
        self._department_browser.set_entity(entity_uri)
        self._department_browser.select(context.department_name)

        # Update detail and version views
        self._details_view.set_context(context)
        self._version_view.set_context(context)
        self._version_view.select(context.version_name)


    def _workspace_changed(self, entity_uri):
        """Handle workspace selection changes with validation"""
        try:
            # Store Uri directly (workspace browser now emits Uri)
            self._selected_entity = entity_uri

            # Update department browser with new entity
            self._department_browser.set_entity(entity_uri)

            # If we have a current context, try to maintain department selection
            if self._context is not None:
                try:
                    # Compare URIs directly
                    if entity_uri == self._context.entity_uri and self._context.department_name:
                        self._department_browser.select(self._context.department_name)
                except Exception as e:
                    raise RuntimeError(f"Error maintaining department selection: {e}")

        except Exception as e:
            raise RuntimeError(f"Error in workspace change handler: {e}")
    
    def _workspace_open_path(self, selected_path):
        if len(selected_path) == 0:
            return
        uri = "/".join(selected_path[1:])
        match selected_path[0]:
            case "assets":
                location_path = api.storage.resolve(Uri.parse_unsafe(f"assets:/{uri}"))
            case "shots":
                location_path = api.storage.resolve(Uri.parse_unsafe(f"shots:/{uri}"))
        location_path.mkdir(parents=True, exist_ok=True)
        self._open_location_path(location_path)

    def _create_batch_entry(self, selected_path):
        """Open batch entity creation dialog"""
        if len(selected_path) == 0:
            return

        from .dialogs import BatchEntityDialog
        dialog = BatchEntityDialog(api, selected_path, parent=self)
        dialog.entities_created.connect(lambda _: self.refresh())
        dialog.exec_()

    def _remove_entry(self, selected_path):
        def _remove_entity(entity_uri: Uri):
            """Remove an entity after user confirmation."""
            # Display the full path (excluding entity type)
            display_name = '/'.join(entity_uri.segments[1:])

            # Prompt the user to confirm the removal
            result = QtWidgets.QMessageBox.question(
                self,
                "Remove Entity",
                f"Are you sure you want to remove: {display_name}?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if result != QtWidgets.QMessageBox.Yes:
                return

            # Remove the entity
            api.config.remove_entity(entity_uri)

            # Update the UI
            self.refresh()

        # Convert path to URI and remove
        if len(selected_path) < 2:
            return  # Need at least entity type + one segment

        entity_uri = Uri.parse_unsafe(f'entity:/{"/".join(selected_path)}')
        _remove_entity(entity_uri)
        
    def _department_changed(self, payload):
        """Handle department selection change with auto_save flag"""
        context, auto_save = payload
        # Don't update _selected_department immediately - let _open_scene handle it after save prompt
        new_department = (context.department_name, context.version_name)
        self._open_scene(auto_save=auto_save, new_department=new_department)
        # Note: No need to handle return value here - _open_scene handles all state updates internally

    def _on_auto_refresh_changed(self, enabled):
        """Handle auto refresh setting change"""
        # Update the auto settings to reflect the new refresh setting
        self._auto_settings[Section.Asset][Action.Refresh] = enabled
        self._auto_settings[Section.Shot][Action.Refresh] = enabled
        
    def _department_open_location(self, context):
        if self._selected_entity is None:
            return
        department_name = context.department_name
        entity_type = self._selected_entity.segments[0]
        entity_path = '/'.join(self._selected_entity.segments[1:])
        match entity_type:
            case "assets":
                location_path = api.storage.resolve(Uri.parse_unsafe(
                    f"assets:/{entity_path}/{department_name}"
                ))
            case "shots":
                location_path = api.storage.resolve(Uri.parse_unsafe(
                    f"shots:/{entity_path}/{department_name}"
                ))
        location_path.mkdir(parents=True, exist_ok=True)
        self._open_location_path(location_path)
        
    def _department_reload_scene(self, _context):
        if self._selected_entity is None:
            return
        self._open_scene(True)
        # Note: No need to handle return value - this is a reload, state remains the same
        
    def _department_new_from_current(self, context):
        """Create a new version from the current scene in the specified department

        Args:
            context: Context containing the department to create new version in
        """
        # Build the target context without modifying state yet
        if self._selected_entity is None:
            return

        department_name = context.department_name
        selected_context = Context(
            entity_uri=self._selected_entity,
            department_name=department_name,
            version_name=None  # Creating new version
        )

        # Maybe save changes (state not modified yet, so safe to cancel)
        success = self._save_changes()
        if not success:
            return

        # Save the current scene to new version
        file_path = next_file_path(selected_context)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        hou.hipFile.save(path_str(file_path))

        # Update context from saved file
        self._context = get_workfile_context(file_path)
        if self._context is None:
            self._context = selected_context
        save_context(file_path.parent, None, self._context)
        save_entity_context(file_path.parent, self._context)
        self._update_scene()

        # Update all UI components
        self._update_ui_from_context(self._context)
        
    def _department_new_from_template(self, context):
        """Create a new version from a template in the specified department

        Args:
            context: Context containing the department to create new version in
        """
        # Build the target context without modifying state yet
        if self._selected_entity is None:
            return

        department_name = context.department_name
        selected_context = Context(
            entity_uri=self._selected_entity,
            department_name=department_name,
            version_name=None  # Creating new version
        )

        # Maybe save changes (state not modified yet, so safe to cancel)
        success = self._save_changes()
        if not success:
            return

        # Create new scene from template
        file_path = next_file_path(selected_context)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        hou.hipFile.clear(suppress_save_prompt=True)
        hou.hipFile.save(path_str(file_path))

        # Update context from saved file
        self._context = get_workfile_context(file_path)
        if self._context is None:
            self._context = selected_context
        save_context(file_path.parent, None, self._context)
        save_entity_context(file_path.parent, self._context)
        self._initialize_scene()
        self._update_scene()

        # Update all UI components
        self._update_ui_from_context(self._context)
        

    def _open_scene_internal(self, selected_context, should_reload=False):
        """Internal scene opening logic shared by normal and auto-save variants"""
        # Get the file path
        file_path = (
            next_file_path(selected_context)
            if selected_context.version_name is None
            else file_path_from_context(selected_context)
        )

        # Set the update mode to manual
        with util.update_mode(hou.updateMode.Manual):
            # Load the file path if it exists, otherwise create it
            if file_path.exists():
                hou.hipFile.load(
                    path_str(file_path),
                    suppress_save_prompt=True,
                    ignore_load_warnings=True,
                )
                context = get_workfile_context(file_path)
                if context is None:
                    context = selected_context
                    save_entity_context(file_path.parent, context)
                self._context = context
                save_entity_context(file_path.parent, self._context)
            else:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                hou.hipFile.clear(suppress_save_prompt=True)
                hou.hipFile.save(path_str(file_path))
                self._context = selected_context
                save_context(file_path.parent, None, self._context)
                save_entity_context(file_path.parent, self._context)
                self._initialize_scene()

            # Find a build shot or import shot node
            build_shot_nodes = list(
                map(build_shot.BuildShot, ns.list_by_node_type("build_shot", "Lop"))
            )
            import_shot_nodes = list(
                map(import_shot.ImportShot, ns.list_by_node_type("import_shot", "Lop"))
            )
            if len(build_shot_nodes) > 0:
                build_shot_nodes[0].setDisplayFlag(True)
            elif len(import_shot_nodes) > 0:
                import_shot_nodes[0].native().setDisplayFlag(True)

            # Update the dependencies
            self._update_scene()

            # Update the details and versions view
            # Don't change workspace/department selection - keep showing selected entity
            # The workfile context might be a group, but UI should reflect user's selection
            self._details_view.set_context(self._context)
            self._version_view.set_context(self._context)
            self._version_view.select(self._context.version_name)

            # Center the network editor view
            center_all_network_editors()

            # Set the viewport to the vulkan renderer
            vulkan_all_scene_viewers()

    def _open_scene(self, should_reload=False, auto_save=False, new_department=None):
        """Open a scene file, returning success status

        Args:
            should_reload: Force reload even if same scene
            auto_save: Automatically save without prompting
            new_department: Optional tuple of (department_name, version_name) to switch to

        Returns:
            bool: True if scene was opened successfully, False if cancelled or failed
        """
        # Construct target context based on whether we're switching departments
        if new_department is not None:
            # Construct target context directly from entity + new_department
            if self._selected_entity is None:
                return False
            department_name, version_name = new_department
            selected_context = Context(
                entity_uri=self._selected_entity,
                department_name=department_name,
                version_name=version_name,
            )
        else:
            # Use existing selection logic for other cases (reload, etc.)
            selected_context = self._selection()
            if selected_context is None:
                return False

        # If we are opening the same scene, do nothing (return True since it's not an error)
        if self._context == selected_context and not should_reload:
            return True

        # Save changes based on auto_save flag
        success = self._save_changes(auto_save=auto_save)
        if not success:
            # User cancelled save prompt - don't update any state
            return False

        # Update the department state only after save prompt succeeds
        if new_department is not None:
            self._selected_department = new_department
            # Also confirm the visual selection in the department browser
            department_name, _ = new_department
            self._department_browser.confirm_selection(department_name)

        # Continue with scene opening
        self._open_scene_internal(selected_context, should_reload)
        return True

    def _save_changes(self, auto_save=False):
        # Check if current scene has unsaved changes
        if not hou.hipFile.hasUnsavedChanges():
            return True

        # Save the current scene
        if hou.hipFile.isNewFile():
            if auto_save:
                # For new files, we can't auto save, so just return True to continue
                return True
            else:
                # Ask the user if they want to save the current non-pipe scene
                message = "The current scene has unsaved changes.\nDo you want to save it?"
                result = QtWidgets.QMessageBox.question(
                    self,
                    "Save Scene",
                    message,
                    QtWidgets.QMessageBox.Yes
                    | QtWidgets.QMessageBox.No
                    | QtWidgets.QMessageBox.Cancel,
                )
                if result == QtWidgets.QMessageBox.Yes:
                    file_path = hou.ui.selectFile(
                        title="Choose the file path to save the scene",
                        start_directory=path_str(Path.home()),
                        file_type=hou.fileType.Hip,
                        chooser_mode=hou.fileChooserMode.Write,
                    )
                    if len(file_path) == 0:
                        return False
                    hou.hipFile.save(file_path)

                elif result == QtWidgets.QMessageBox.No:
                    return True

                elif result == QtWidgets.QMessageBox.Cancel:
                    return False
        else:
            if auto_save:
                # Auto save the pipeline scene without prompting
                file_path = next_file_path(self._context)
                hou.hipFile.save(path_str(file_path))

                # Update current context
                prev_context = self._context
                self._context = get_workfile_context(file_path)
                save_context(file_path.parent, prev_context, self._context)
                save_entity_context(file_path.parent, self._context)
            else:
                # Ask the user if they want to save the current pipeline scene
                message = "The current scene has unsaved changes.\nDo you want to save it?"
                result = QtWidgets.QMessageBox.question(
                    self,
                    "Save Scene",
                    message,
                    QtWidgets.QMessageBox.Yes
                    | QtWidgets.QMessageBox.No
                    | QtWidgets.QMessageBox.Cancel,
                )
                if result == QtWidgets.QMessageBox.Yes:
                    file_path = next_file_path(self._context)
                    hou.hipFile.save(path_str(file_path))

                    # Update current context
                    prev_context = self._context
                    self._context = get_workfile_context(file_path)
                    save_context(file_path.parent, prev_context, self._context)
                    save_entity_context(file_path.parent, self._context)

                elif result == QtWidgets.QMessageBox.No:
                    return True

                elif result == QtWidgets.QMessageBox.Cancel:
                    return False

        # Return success
        return True

    def _save_scene(self):
        # Check if we have a valid workspace and department
        if self._context is None:
            return
        prev_context = self._context

        try:
            # Save the file path
            file_path = next_file_path(self._context)
            hou.hipFile.save(path_str(file_path))

            # Set the new context
            self._context = get_workfile_context(file_path)
            save_context(file_path.parent, prev_context, self._context)
            save_entity_context(file_path.parent, self._context)

            # Update all UI components
            self._update_ui_from_context(self._context)

            # Hide spinner and flash success with proper sequencing
            from qtpy.QtCore import QTimer
            QTimer.singleShot(50, lambda: self._details_view.hide_workfile_spinner())
            QTimer.singleShot(100, lambda: self._details_view.flash_workfile_success())

        except Exception as e:
            # Hide spinner on error
            self._details_view.hide_workfile_spinner()
            hou.ui.displayMessage(f"Error saving scene: {str(e)}", severity=hou.severityType.Error)
        
    def _update_scene(self):
        # Refresh the scene based on entity type
        if self._context is None:
            return

        entity_type = get_entity_type(self._context.entity_uri)
        if entity_type == 'asset':
            if self._auto_settings[Section.Asset][Action.Refresh]:
                self._refresh_scene()
        elif entity_type == 'shot':
            if self._auto_settings[Section.Shot][Action.Refresh]:
                self._refresh_scene()
        elif entity_type == 'group':
            group_context = self._context.entity_uri.segments[0]
            if group_context == 'assets':
                if self._auto_settings[Section.Asset][Action.Refresh]:
                    self._refresh_scene()
            elif group_context == 'shots':
                if self._auto_settings[Section.Shot][Action.Refresh]:
                    self._refresh_scene()

        # Set the frame range
        self._set_frame_range(FrameRangeMode.Padded)

        # Set the render gallery path
        stage = hou.node("/stage")
        stage.parm("rendergallerysource").set("$HIP/galleries/rendergallery.db")

    def _refresh_scene(self):
        # Find shot build nodes
        build_shot_nodes = list(
            map(build_shot.BuildShot, ns.list_by_node_type("build_shot", "Lop"))
        )

        # Find import shot nodes
        import_shot_nodes = list(
            map(import_shot.ImportShot, ns.list_by_node_type("import_shot", "Lop"))
        )

        # Find import asset nodes
        import_assets_nodes = list(
            map(
                import_assets.ImportAssets, ns.list_by_node_type("import_assets", "Lop")
            )
        )

        # Find the import asset layer nodes
        import_asset_layer_nodes = list(
            map(
                import_asset_layer.ImportAssetLayer,
                ns.list_by_node_type("import_asset_layer", "Lop"),
            )
        )

        # Find the import shot layer nodes
        import_shot_layer_nodes = list(
            map(
                import_shot_layer.ImportShotLayer,
                ns.list_by_node_type("import_shot_layer", "Lop"),
            )
        )

        # Find the import render layer nodes
        import_render_layer_nodes = list(
            map(
                import_render_layer.ImportRenderLayer,
                ns.list_by_node_type("import_render_layer", "Lop"),
            )
        )

        # Find the import rigs nodes
        import_rig_nodes = list(
            map(import_rigs.ImportRigs, ns.list_by_node_type("import_rigs", "Sop"))
        )

        # Find the build comp nodes
        build_comp_nodes = list(
            map(build_comp.BuildComp, ns.list_by_node_type("build_comp", "Cop"))
        )

        # Import latest shot builds
        for build_shot_node in build_shot_nodes:
            if not build_shot_node.is_valid():
                continue
            build_shot_node.execute()

        # Import latest shot stages
        for import_shot_node in import_shot_nodes:
            if not import_shot_node.is_valid():
                continue
            import_shot_node.execute()

        # Import latest assets
        for import_assets_node in import_assets_nodes:
            if not import_assets_node.is_valid():
                continue
            import_assets_node.execute()

        # Import latest asset layers
        for import_node in import_asset_layer_nodes:
            if not import_node.is_valid():
                continue
            import_node.latest()
            import_node.execute()

        # Import latest shot layers
        for import_node in import_shot_layer_nodes:
            if not import_node.is_valid():
                continue
            import_node.latest()
            import_node.execute()

        # Import latest render layers
        for import_node in import_render_layer_nodes:
            if not import_node.is_valid():
                continue
            import_node.latest()
            import_node.execute()

        # Import latest rigs
        for import_node in import_rig_nodes:
            if not import_node.is_valid():
                continue
            import_node.execute()

        # Execute build comp nodes  
        for build_comp_node in build_comp_nodes:
            if not build_comp_node.is_valid():
                continue
            build_comp_node.execute()

    def _initialize_scene(self):
        # Check if we have a valid workspace and department
        if self._context is None:
            return

        # Prepare to initialize the scene
        scene_node = hou.node("/stage")

        # Initialize the scene based on entity type
        entity_type = get_entity_type(self._context.entity_uri)
        department_name = self._context.department_name

        # Create unique template module name from URI segments
        uri_name = '_'.join(self._context.entity_uri.segments[1:])

        if entity_type == 'asset':
            template_name = f"{uri_name}_{department_name}_template"
            template_path = api.storage.resolve(Uri.parse_unsafe(
                f"config:/templates/assets/{department_name}/template.py"
            ))
            template = load_module(template_path, template_name)
            template.create(scene_node, self._context.entity_uri, department_name)
        elif entity_type == 'shot':
            template_name = f"{uri_name}_{department_name}_template"
            template_path = api.storage.resolve(Uri.parse_unsafe(
                f"config:/templates/shots/{department_name}/template.py"
            ))
            template = load_module(template_path, template_name)
            template.create(scene_node, self._context.entity_uri, department_name)
        elif entity_type == 'group':
            group_context = self._context.entity_uri.segments[0]
            template_name = f"{uri_name}_{department_name}_template"
            template_path = api.storage.resolve(Uri.parse_unsafe(
                f"config:/templates/{group_context}/{department_name}/template.py"
            ))
            template = load_module(template_path, template_name)
            template.create(scene_node, self._context.entity_uri, department_name)

        # Layout the scene
        scene_node.layoutChildren()
        
    def _publish_scene(self, ignore_missing_export=False):
        # Check if we have a valid workspace and department
        if self._context is None:
            return

        entity_type = get_entity_type(self._context.entity_uri)
        department_name = self._context.department_name

        def _is_asset_export_correct(node):
            if entity_type != 'asset':
                return False
            if node.get_department_name() != department_name:
                return False
            if node.get_asset_uri() != self._context.entity_uri:
                return False
            return True

        def _is_rig_export_correct(node):
            if entity_type != 'asset':
                return False
            if department_name != "rig":
                return False
            if node.get_asset_uri() != self._context.entity_uri:
                return False
            return True

        def _is_shot_export_correct(node):
            if entity_type != 'shot':
                return False
            if node.get_department_name() != department_name:
                return False
            shot_uri = node.get_shot_uri()
            if shot_uri != self._context.entity_uri:
                return False
            return True

        def _is_render_layer_export_correct(node):
            if entity_type != 'shot':
                return False
            if node.get_department_name() != department_name:
                return False
            shot_uri = node.get_shot_uri()
            if shot_uri != self._context.entity_uri:
                return False
            return True

        # Find the export node with the correct workspace and department
        if entity_type == 'asset' and department_name == 'rig':
            # Find the export nodes
            rig_export_nodes = list(
                filter(
                    _is_rig_export_correct,
                    map(
                        export_rig.ExportRig,
                        ns.list_by_node_type("export_rig", "Sop"),
                    ),
                )
            )

            # Check if we have any export nodes, report if not
            if len(rig_export_nodes) == 0:
                if ignore_missing_export:
                    return
                hou.ui.displayMessage(
                    "No rig export nodes found for the current asset.",
                    severity=hou.severityType.Warning,
                )
                return

            # Check if there are more than one export nodes, report if so
            if len(rig_export_nodes) > 1:
                hou.ui.displayMessage(
                    "More than one rig export node found for the current asset.",
                    severity=hou.severityType.Warning,
                )
                return

            # Execute the export node
            rig_export_node = rig_export_nodes[0]
            rig_export_node.execute()

        elif entity_type == 'asset':
            # Find the export nodes
            asset_export_nodes = list(
                filter(
                    _is_asset_export_correct,
                    map(
                        export_asset_layer.ExportAssetLayer,
                        ns.list_by_node_type("export_asset_layer", "Lop"),
                    ),
                )
            )

            # Check if we have any export nodes, report if not
            if len(asset_export_nodes) == 0:
                if ignore_missing_export:
                    return
                hou.ui.displayMessage(
                    "No export nodes found for the current asset.",
                    severity=hou.severityType.Warning,
                )
                return

            # Check if there are more than one export nodes, report if so
            if len(asset_export_nodes) > 1:
                hou.ui.displayMessage(
                    "More than one export node found for the current asset.",
                    severity=hou.severityType.Warning,
                )
                return

            # Execute the export node
            asset_export_node = asset_export_nodes[0]
            asset_export_node.execute()

        elif entity_type == 'shot':
            # Find any build shot nodes
            build_shot_nodes = list(
                map(build_shot.BuildShot, ns.list_by_node_type("build_shot", "Lop"))
            )

            # Temporarily disable procedurals
            build_node_include_procedurals = {
                build_shot_node.path(): build_shot_node.get_include_procedurals()
                for build_shot_node in build_shot_nodes
            }
            for build_shot_node in build_shot_nodes:
                build_shot_node.set_include_procedurals(False)

            # Find the export nodes
            shot_export_nodes = list(
                filter(
                    _is_shot_export_correct,
                    map(
                        export_shot_layer.ExportShotLayer,
                        ns.list_by_node_type("export_shot_layer", "Lop"),
                    ),
                )
            )
            render_layer_export_nodes = list(
                filter(
                    _is_render_layer_export_correct,
                    map(
                        export_render_layer.ExportRenderLayer,
                        ns.list_by_node_type("export_render_layer", "Lop"),
                    ),
                )
            )

            # Check if we have any export nodes, report if not
            if len(shot_export_nodes) == 0:
                if ignore_missing_export:
                    return
                hou.ui.displayMessage(
                    "No export nodes found for the current shot.",
                    severity=hou.severityType.Warning,
                )
                return

            # Check if there are more than one export nodes, report if so
            if len(shot_export_nodes) > 1:
                hou.ui.displayMessage(
                    "More than one export node found for the current shot.",
                    severity=hou.severityType.Warning,
                )
                return

            # Execute the export node
            shot_export_node = shot_export_nodes[0]
            shot_export_node.execute()

            # Export the render layers
            for render_layer_export_node in render_layer_export_nodes:
                render_layer_export_node.execute()

            # Re-enable procedurals
            for build_shot_node in build_shot_nodes:
                include_procedurals = build_node_include_procedurals[
                    build_shot_node.path()
                ]
                build_shot_node.set_include_procedurals(include_procedurals)

        elif entity_type == 'group':
            # Get group members
            group = get_group(self._context.entity_uri)
            if group is None:
                hou.ui.displayMessage(
                    "Could not find group configuration.",
                    severity=hou.severityType.Warning,
                )
                return

            member_uris = set(group.members)

            # Filter function: export node's shot_uri must be a group member
            def _is_group_export_correct(node):
                if node.get_department_name() != department_name:
                    return False
                shot_uri = node.get_shot_uri()
                return shot_uri in member_uris

            # Find all export nodes for group members
            group_export_nodes = list(
                filter(
                    _is_group_export_correct,
                    map(
                        export_shot_layer.ExportShotLayer,
                        ns.list_by_node_type("export_shot_layer", "Lop"),
                    ),
                )
            )

            # Check if we have any export nodes
            if len(group_export_nodes) == 0:
                if ignore_missing_export:
                    return
                hou.ui.displayMessage(
                    "No export nodes found for the group members.",
                    severity=hou.severityType.Warning,
                )
                return

            # Execute all export nodes (one per member)
            for export_node in group_export_nodes:
                export_node.execute()

    def _publish_scene_clicked(self):
        from .dialogs import ProcessDialog
        from .utils.process_executor import collect_publish_tasks

        # Check if we have a valid context
        if self._context is None:
            hou.ui.displayMessage(
                "No context available for publishing.",
                severity=hou.severityType.Warning
            )
            return

        try:
            # Collect tasks for current context (read-only scan, no save yet)
            tasks = collect_publish_tasks(self._context)

            if len(tasks) == 0:
                hou.ui.displayMessage(
                    "No export nodes found for the current context.",
                    severity=hou.severityType.Warning
                )
                return

            # Show process dialog with save as pre-execute callback
            # Save will only happen when user clicks Execute
            dialog = ProcessDialog(
                title="Publish",
                tasks=tasks,
                current_department=self._context.department_name,
                pre_execute_callback=self._save_scene,
                parent=self
            )
            dialog.process_completed.connect(self._on_publish_completed)
            dialog.exec_()

        except Exception as e:
            import traceback
            print(f"Error during publish: {str(e)}")
            print("Full traceback:")
            traceback.print_exc()
            hou.ui.displayMessage(f"Error during publish: {str(e)}", severity=hou.severityType.Error)

    def _on_publish_completed(self, results: dict):
        """Handle completion of publish process dialog"""
        completed = results.get('completed', [])
        failed = results.get('failed', [])

        if completed:
            # Flash export success in details view
            self._details_view.flash_export_success()

        if failed:
            # Show error summary
            error_count = len(failed)
            hou.ui.displayMessage(
                f"{error_count} task(s) failed during publish. Check the process dialog for details.",
                severity=hou.severityType.Warning
            )
        
    def _open_scene_info(self):
        pass
        
    def _open_location(self, location):
        match location:
            case Location.Workspace:
                self._open_workspace_location(self._context)
            case Location.Export:
                self._open_export_location(self._context)
            case Location.Texture:
                self._open_texture_location(self._context)
        
    def _set_frame_range(self, mode):
        # Check if we have a valid workspace and department
        if self._context is None:
            return

        # Set the frame range based on entity type
        entity_type = get_entity_type(self._context.entity_uri)
        if entity_type == 'shot':
            frame_range = get_frame_range(self._context.entity_uri)
            if frame_range is not None:
                match mode:
                    case FrameRangeMode.Padded:
                        util.set_block_range(frame_range.full_range())
                    case FrameRangeMode.Full:
                        util.set_block_range(frame_range.play_range())
            else:
                util.set_block_range(BlockRange(1001, 1200))
        else:
            util.set_block_range(BlockRange(1001, 1200))

        # Set the frames per second
        hou.playbar.setRealTime(True)
        
    def _open_workspace_location(self, context):
        file_path = file_path_from_context(context)
        if file_path is None:
            return
        self._open_location_path(file_path)

    def _open_export_location(self, context):
        if context is None:
            return
        export_path = latest_export_path_from_context(context)
        if export_path is None:
            return
        self._open_location_path(export_path)

    def _open_texture_location(self, context):
        file_path = file_path_from_context(context)
        if file_path is None:
            return
        texture_path = file_path.parent.parent / "texture"
        texture_path.mkdir(parents=True, exist_ok=True)
        self._open_location_path(texture_path)

    def _open_location_path(self, file_path):
        hou.ui.showInFileBrowser(
            path_str(file_path) + "/" if file_path.is_dir() else path_str(file_path)
        )
        
    def _open_version(self, context):
        """Open a specific version of the current workfile

        Args:
            context: The version context to open
        """
        # Temporarily set state for _open_scene() to use
        self._select(context)

        # Attempt to open the scene
        success = self._open_scene()

        if not success:
            # Operation was cancelled - restore previous state
            if self._context is not None:
                self._select(self._context)
            return

        # Only update UI if scene was actually opened
        self._department_browser.overwrite(context)
        self._tabbed_view.setCurrentWidget(self._details_view)
        
    def _revive_version(self, context):
        """Revive an old version as the new latest version

        Args:
            context: The version context to revive
        """
        # Temporarily set state for _open_scene() to use
        self._select(context)

        # Attempt to open the scene
        success = self._open_scene()

        if not success:
            # Operation was cancelled - restore previous state
            if self._context is not None:
                self._select(self._context)
            return

        # Only continue if scene was actually opened
        self._save_scene()
        self._tabbed_view.setCurrentWidget(self._details_view)