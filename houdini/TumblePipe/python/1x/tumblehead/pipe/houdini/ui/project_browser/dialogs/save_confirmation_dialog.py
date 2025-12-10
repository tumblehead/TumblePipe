"""
Dialog for confirming scene changes before saving.

Shows a preview of affected shots that will have their root .usda regenerated
and build jobs submitted.
"""

from qtpy.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QDialogButtonBox,
    QScrollArea,
    QWidget,
)

from tumblehead.util.uri import Uri


class SaveConfirmationDialog(QDialog):
    """
    Preview dialog showing affected shots before saving scene changes.

    Displays a summary of pending changes and lists all shots that will
    be rebuilt when the save is confirmed.
    """

    def __init__(
        self,
        scene_changes_count: int,
        shot_assignment_changes_count: int,
        affected_shots: list[Uri],
        parent=None
    ):
        """
        Args:
            scene_changes_count: Number of scenes with modified assets
            shot_assignment_changes_count: Number of shot scene assignments changed
            affected_shots: List of shot URIs that will be rebuilt
            parent: Parent widget
        """
        super().__init__(parent)
        self._scene_changes_count = scene_changes_count
        self._shot_assignment_changes_count = shot_assignment_changes_count
        self._affected_shots = affected_shots
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Save Scene Changes")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)

        layout = QVBoxLayout(self)

        # Summary header
        summary = QLabel("<b>Changes to save:</b>")
        layout.addWidget(summary)

        # Scene asset changes
        if self._scene_changes_count > 0:
            scene_label = QLabel(
                f"  • {self._scene_changes_count} scene(s) with modified assets"
            )
            layout.addWidget(scene_label)

        # Shot scene assignments
        if self._shot_assignment_changes_count > 0:
            shot_label = QLabel(
                f"  • {self._shot_assignment_changes_count} shot scene assignment(s) changed"
            )
            layout.addWidget(shot_label)

        # Affected shots section
        layout.addSpacing(10)
        affected_header = QLabel(
            f"<b>Shots to rebuild ({len(self._affected_shots)}):</b>"
        )
        layout.addWidget(affected_header)

        if self._affected_shots:
            # Scrollable shot list
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            scroll_area.setMaximumHeight(200)

            scroll_content = QWidget()
            scroll_layout = QVBoxLayout(scroll_content)
            scroll_layout.setContentsMargins(10, 5, 10, 5)
            scroll_layout.setSpacing(2)

            for shot_uri in self._affected_shots:
                # Format as readable path (e.g., "shots/010/010")
                shot_path = '/'.join(shot_uri.segments)
                item_label = QLabel(f"• {shot_path}")
                item_label.setStyleSheet("font-size: 11px;")
                scroll_layout.addWidget(item_label)

            scroll_layout.addStretch()
            scroll_area.setWidget(scroll_content)
            layout.addWidget(scroll_area)
        else:
            no_shots = QLabel("  No shots will be affected.")
            no_shots.setStyleSheet("color: #888;")
            layout.addWidget(no_shots)

        # Warning/info message
        layout.addSpacing(10)
        warning = QLabel(
            "<i>This will generate root .usda files and submit build jobs "
            "to update staged files for all affected shots.</i>"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #888;")
        layout.addWidget(warning)

        layout.addStretch()

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_affected_count(self) -> int:
        """Return the number of affected shots."""
        return len(self._affected_shots)
