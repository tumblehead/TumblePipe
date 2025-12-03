from functools import partial
import datetime as dt

from qtpy.QtCore import Qt, Signal
from qtpy import QtWidgets
from hou import qt as hqt

from tumblehead.api import get_user_name
from tumblehead.util.io import load_json
import tumblehead.pipe.context as ctx

from ..constants import Location, FrameRangeMode
from ..helpers import (
    file_path_from_context,
    latest_export_path_from_context,
)
from ..utils.animations import SpinnerManager


class DetailsView(QtWidgets.QWidget):
    save_scene = Signal()
    refresh_scene = Signal()
    publish_scene = Signal()
    open_scene_info = Signal()
    open_location = Signal(object)
    set_frame_range = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Members
        self._context = None

        # Create spinner and animation manager
        self._spinner_manager = SpinnerManager(self)

        # Settings
        self.setMinimumHeight(0)

        # Create the outer layout
        outer_layout = QtWidgets.QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(10)
        self.setLayout(outer_layout)

        # Create the scroll area
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        scroll_widget.setLayout(layout)
        scroll_area.setWidget(scroll_widget)
        outer_layout.addWidget(scroll_area)

        # Create the top button layout
        top_button_layout = QtWidgets.QGridLayout()
        top_button_layout.setColumnStretch(0, 1)
        top_button_layout.setSpacing(0)
        layout.addLayout(top_button_layout)

        # Create the save scene button
        self.save_scene_button = QtWidgets.QPushButton("Save")
        self.save_scene_button.setIcon(hqt.Icon("DESKTOP_hip"))
        self.save_scene_button.clicked.connect(self._save)
        top_button_layout.addWidget(self.save_scene_button, 0, 0)

        # Create the open workspace location button
        self.open_workspace_location_button = QtWidgets.QPushButton()
        self.open_workspace_location_button.setIcon(hqt.Icon("BUTTONS_folder"))
        self.open_workspace_location_button.clicked.connect(
            partial(self._open_location, Location.Workspace)
        )
        top_button_layout.addWidget(self.open_workspace_location_button, 0, 1)

        # Create the refresh scene button
        self.refresh_scene_button = QtWidgets.QPushButton("Refresh")
        self.refresh_scene_button.setIcon(hqt.Icon("NETVIEW_reload_needsupdate"))
        self.refresh_scene_button.clicked.connect(self._refresh)
        top_button_layout.addWidget(self.refresh_scene_button, 1, 0)

        # Create the scene info button
        self.scene_info_button = QtWidgets.QPushButton()
        self.scene_info_button.setIcon(hqt.Icon("BUTTONS_list_info"))
        self.scene_info_button.clicked.connect(self._scene_info)
        top_button_layout.addWidget(self.scene_info_button, 1, 1)

        # Create the publish scene button
        self.publish_scene_button = QtWidgets.QPushButton("Publish")
        self.publish_scene_button.setIcon(hqt.Icon("NETVIEW_export_flag"))
        self.publish_scene_button.clicked.connect(self._publish)
        top_button_layout.addWidget(self.publish_scene_button, 2, 0)

        # Create the open export location button
        self.open_export_location_button = QtWidgets.QPushButton()
        self.open_export_location_button.setIcon(hqt.Icon("BUTTONS_folder"))
        self.open_export_location_button.clicked.connect(
            partial(self._open_location, Location.Export)
        )
        top_button_layout.addWidget(self.open_export_location_button, 2, 1)

        # Create the open texture location button
        self.open_texture_location_button = QtWidgets.QPushButton()
        self.open_texture_location_button.setIcon(hqt.Icon("BUTTONS_folder"))
        self.open_texture_location_button.clicked.connect(
            partial(self._open_location, Location.Texture)
        )
        top_button_layout.addWidget(
            QtWidgets.QLabel("Open texture location: "), 3, 0, alignment=Qt.AlignRight
        )
        top_button_layout.addWidget(self.open_texture_location_button, 3, 1)

        # Create the details grid layout
        details_layout = QtWidgets.QVBoxLayout()
        layout.addLayout(details_layout)

        # Sections layout
        sections_layout = QtWidgets.QHBoxLayout()
        sections_layout.setContentsMargins(5, 5, 5, 5)
        sections_layout.setSpacing(5)
        details_layout.addLayout(sections_layout)

        # Create the workspace section (simple single panel)
        self.workspace_section = QtWidgets.QFrame()
        self.workspace_section.setObjectName("workspace_section")
        self.workspace_section.setStyleSheet(
            "QFrame#workspace_section {"
            "   border: 1px solid black;"
            "   border-radius: 5px;"
            "   background-color: rgba(0, 0, 0, 0.1);"
            "}"
        )
        workspace_section_layout = QtWidgets.QVBoxLayout()
        workspace_section_layout.setContentsMargins(5, 5, 5, 5)
        workspace_section_layout.setSpacing(10)
        self.workspace_section.setLayout(workspace_section_layout)
        sections_layout.addWidget(self.workspace_section)

        # Create the workspace section headline
        workspace_section_headline = QtWidgets.QLabel("Current Workspace:")
        workspace_section_headline.setAlignment(Qt.AlignCenter)
        workspace_section_layout.addWidget(workspace_section_headline)

        # Create the workspace section details layout
        self.workspace_details_layout = QtWidgets.QGridLayout()
        self.workspace_details_layout.setColumnStretch(1, 1)
        self.workspace_details_layout.setSpacing(5)
        workspace_section_layout.addLayout(self.workspace_details_layout)

        # Create the workspace section details version entry
        self.workspace_version_label = QtWidgets.QLabel()
        self.workspace_details_layout.addWidget(QtWidgets.QLabel("Version:"), 0, 0)
        self.workspace_details_layout.addWidget(self.workspace_version_label, 0, 1)

        # Create the workspace section details timestamp entry
        self.workspace_timestamp_label = QtWidgets.QLabel()
        self.workspace_details_layout.addWidget(QtWidgets.QLabel("Time:"), 1, 0)
        self.workspace_details_layout.addWidget(self.workspace_timestamp_label, 1, 1)

        # Create the workspace section details user entry
        self.workspace_user_label = QtWidgets.QLabel()
        self.workspace_details_layout.addWidget(QtWidgets.QLabel("User:"), 2, 0)
        self.workspace_details_layout.addWidget(self.workspace_user_label, 2, 1)

        # Create the export section (simple single panel)
        self.export_section = QtWidgets.QFrame()
        self.export_section.setObjectName("export_section")
        self.export_section.setStyleSheet(
            "QFrame#export_section {"
            "   border: 1px solid black;"
            "   border-radius: 5px;"
            "   background-color: rgba(0, 0, 0, 0.1);"
            "}"
        )
        export_section_layout = QtWidgets.QVBoxLayout()
        export_section_layout.setContentsMargins(5, 5, 5, 5)
        export_section_layout.setSpacing(10)
        self.export_section.setLayout(export_section_layout)
        sections_layout.addWidget(self.export_section)

        # Create the export section headline
        export_section_headline = QtWidgets.QLabel("Latest Export:")
        export_section_headline.setAlignment(Qt.AlignCenter)
        export_section_layout.addWidget(export_section_headline)

        # Create the export section details layout
        self.export_details_layout = QtWidgets.QGridLayout()
        self.export_details_layout.setColumnStretch(1, 1)
        self.export_details_layout.setSpacing(5)
        export_section_layout.addLayout(self.export_details_layout)

        # Create the export section details version entry
        self.export_version_label = QtWidgets.QLabel()
        self.export_details_layout.addWidget(QtWidgets.QLabel("Version:"), 0, 0)
        self.export_details_layout.addWidget(self.export_version_label, 0, 1)

        # Create the export section details timestamp entry
        self.export_timestamp_label = QtWidgets.QLabel()
        self.export_details_layout.addWidget(QtWidgets.QLabel("Time:"), 1, 0)
        self.export_details_layout.addWidget(self.export_timestamp_label, 1, 1)

        # Create the export section details user entry
        self.export_user_label = QtWidgets.QLabel()
        self.export_details_layout.addWidget(QtWidgets.QLabel("User:"), 2, 0)
        self.export_details_layout.addWidget(self.export_user_label, 2, 1)

        # Create a spacer
        layout.addStretch()

        # Create the frame range layout
        frame_range_section = QtWidgets.QFrame()
        frame_range_section.setObjectName("frame_range_section")
        frame_range_section.setStyleSheet(
            "QFrame#frame_range_section {"
            "   border: 1px solid black;"
            "   border-radius: 5px;"
            "   background-color: rgba(0, 0, 0, 0.1);"
            "}"
        )
        self.frame_range_layout = QtWidgets.QHBoxLayout()
        self.frame_range_layout.setContentsMargins(5, 5, 5, 5)
        self.frame_range_layout.setSpacing(5)
        frame_range_section.setLayout(self.frame_range_layout)
        outer_frame_range_layout = QtWidgets.QHBoxLayout()
        outer_frame_range_layout.setContentsMargins(5, 5, 5, 5)
        outer_frame_range_layout.setSpacing(5)
        outer_frame_range_layout.addWidget(frame_range_section)
        layout.addLayout(outer_frame_range_layout)

        # Create the frame range label
        self.frame_range_label = QtWidgets.QLabel("Frame Range:")
        self.frame_range_layout.addWidget(self.frame_range_label)

        # Add a spacer
        self.frame_range_layout.addStretch()

        # Create the padded frame range button
        self.padded_frame_range_button = QtWidgets.QPushButton("Padded")
        self.padded_frame_range_button.clicked.connect(
            partial(self._set_frame_range, FrameRangeMode.Padded)
        )
        self.frame_range_layout.addWidget(self.padded_frame_range_button)

        # Create the full frame range button
        self.full_frame_range_button = QtWidgets.QPushButton("Full")
        self.full_frame_range_button.clicked.connect(
            partial(self._set_frame_range, FrameRangeMode.Full)
        )
        self.frame_range_layout.addWidget(self.full_frame_range_button)

        # Initial update
        self.refresh()

    def set_context(self, context):
        self._context = context
        self.refresh()

    def refresh(self):
        def _set_workspace_details():
            # Get the workspace details
            file_path = file_path_from_context(self._context)
            if file_path is None:
                self.workspace_version_label.setText("-")
                self.workspace_timestamp_label.setText("-")
                self.workspace_user_label.setText("-")
                return
            version_name = file_path.stem.split("_")[-1]
            timestamp = dt.datetime.fromtimestamp(file_path.stat().st_mtime)
            user_name = get_user_name()

            # Set the workspace details
            self.workspace_version_label.setText(version_name)
            self.workspace_timestamp_label.setText(
                timestamp.strftime("%Y/%m/%d (%H:%M)")
            )
            self.workspace_user_label.setText(user_name)

        def _set_export_details():
            def _find_output(context, context_data):
                if context is None:
                    return dict()
                if context_data is None:
                    return dict()
                result = ctx.find_output(
                    context_data,
                    entity=str(context.entity_uri),
                )
                return result if result is not None else dict()

            def _get_context_path(context):
                export_path = latest_export_path_from_context(context)
                if export_path is None:
                    return None
                context_path = export_path / "context.json"
                if not context_path.exists():
                    return None
                return context_path

            # Get the export details
            context_path = _get_context_path(self._context)
            if context_path is None:
                # Set the export details to N/A
                self.export_version_label.setText("N/A")
                self.export_timestamp_label.setText("N/A")
                self.export_user_label.setText("N/A")

            else:
                # Load the context data
                context_data = load_json(context_path)
                export_info = _find_output(self._context, context_data)
                timestamp = (
                    dt.datetime.fromisoformat(export_info["timestamp"])
                    if "timestamp" in export_info
                    else dt.datetime.fromtimestamp(context_path.stat().st_mtime)
                )
                version_name = (
                    export_info["version"] if "version" in export_info else ""
                )
                user_name = export_info["user"] if "user" in export_info else ""

                # Set the export details
                self.export_version_label.setText(version_name)
                self.export_timestamp_label.setText(
                    timestamp.strftime("%Y/%m/%d (%H:%M)")
                )
                self.export_user_label.setText(user_name)

        if self._context is None:
            # Disable the buttons
            self.save_scene_button.setEnabled(False)
            self.refresh_scene_button.setEnabled(False)
            self.publish_scene_button.setEnabled(False)
            self.scene_info_button.setEnabled(False)
            self.open_workspace_location_button.setEnabled(False)
            self.open_export_location_button.setEnabled(False)
            self.open_texture_location_button.setEnabled(False)

            # Clear the workspace details
            self.workspace_version_label.setText("")
            self.workspace_timestamp_label.setText("")
            self.workspace_user_label.setText("")

            # Clear the export details
            self.export_version_label.setText("")
            self.export_timestamp_label.setText("")
            self.export_user_label.setText("")
        else:
            # Enable the buttons
            self.save_scene_button.setEnabled(True)
            self.refresh_scene_button.setEnabled(True)
            self.publish_scene_button.setEnabled(True)
            self.scene_info_button.setEnabled(True)
            self.open_workspace_location_button.setEnabled(True)
            self.open_export_location_button.setEnabled(True)
            self.open_texture_location_button.setEnabled(True)

            # Set the details
            _set_workspace_details()
            _set_export_details()

    def _save(self):
        if self._context is None:
            return
        # Show spinner immediately when save button is clicked
        self.show_workfile_spinner("Saving...")
        self.save_scene.emit()

    def _refresh(self):
        if self._context is None:
            return
        self.refresh_scene.emit()

    def _publish(self):
        if self._context is None:
            return
        # Show spinners immediately when publish button is clicked
        self.show_workfile_spinner("Saving...")
        self.show_export_spinner("Publishing...")
        self.publish_scene.emit()

    def _scene_info(self):
        if self._context is None:
            return
        self.open_scene_info.emit()

    def _open_location(self, location):
        if self._context is None:
            return
        self.open_location.emit(location)

    def _set_frame_range(self, frame_range):
        if self._context is None:
            return
        self.set_frame_range.emit(frame_range)

    def show_workfile_spinner(self, message="Saving..."):
        """Show loading spinner by changing border to blue"""

        # Store original style and switch to blue border
        self._workspace_original_style = self.workspace_section.styleSheet()
        loading_style = self._workspace_original_style.replace(
            "border: 1px solid black",
            "border: 3px solid #4A90E2"
        )
        self.workspace_section.setStyleSheet(loading_style)
        self.workspace_section.update()

    def hide_workfile_spinner(self):
        """Hide loading spinner by restoring original border"""

        if hasattr(self, '_workspace_original_style'):
            self.workspace_section.setStyleSheet(self._workspace_original_style)
            self.workspace_section.update()

    def show_export_spinner(self, message="Publishing..."):
        """Show loading spinner by changing border to blue"""

        # Store original style and switch to blue border
        self._export_original_style = self.export_section.styleSheet()
        loading_style = self._export_original_style.replace(
            "border: 1px solid black",
            "border: 3px solid #4A90E2"
        )
        self.export_section.setStyleSheet(loading_style)
        self.export_section.update()

    def hide_export_spinner(self):
        """Hide loading spinner by restoring original border"""

        if hasattr(self, '_export_original_style'):
            self.export_section.setStyleSheet(self._export_original_style)
            self.export_section.update()

    def flash_workfile_success(self):
        """Flash workspace section green by temporarily changing background"""

        original_style = self.workspace_section.styleSheet()
        green_style = original_style.replace(
            "background-color: rgba(0, 0, 0, 0.1)",
            "background-color: rgba(76, 175, 80, 0.4)"
        )
        self.workspace_section.setStyleSheet(green_style)
        self.workspace_section.update()

        # Return to normal after brief flash
        from qtpy.QtCore import QTimer
        QTimer.singleShot(600, lambda: [
            self.workspace_section.setStyleSheet(original_style),
            self.workspace_section.update()
        ])

    def flash_export_success(self):
        """Flash export section green by temporarily changing background"""

        original_style = self.export_section.styleSheet()
        green_style = original_style.replace(
            "background-color: rgba(0, 0, 0, 0.1)",
            "background-color: rgba(76, 175, 80, 0.4)"
        )
        self.export_section.setStyleSheet(green_style)
        self.export_section.update()

        # Return to normal after brief flash
        from qtpy.QtCore import QTimer
        QTimer.singleShot(600, lambda: [
            self.export_section.setStyleSheet(original_style),
            self.export_section.update()
        ])