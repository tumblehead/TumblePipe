"""Dialog showing affected entities before a destructive operation."""

from qtpy.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QDialogButtonBox,
    QScrollArea,
    QWidget,
)
from qtpy.QtCore import Qt

from tumblehead.util.uri import Uri


class AffectedEntitiesDialog(QDialog):
    """
    Reusable dialog for confirming operations that affect multiple entities.

    Shows a scrollable list of affected entities with a confirmation message.
    Similar to SchemaMigrationDialog but generalized for any entity type.
    """

    def __init__(
        self,
        title: str,
        header: str,
        affected_entities: list[Uri],
        action_description: str,
        confirm_button_text: str = "Confirm",
        parent=None
    ):
        """
        Args:
            title: Window title (e.g., "Delete Scene")
            header: Header text (e.g., "Delete scene 'forest'?")
            affected_entities: List of URIs that will be affected
            action_description: What will happen to affected entities
                (e.g., "Their scene assignments will be cleared.")
            confirm_button_text: Text for the confirm button
        """
        super().__init__(parent)
        self._affected = affected_entities
        self._title = title
        self._header = header
        self._action_description = action_description
        self._confirm_text = confirm_button_text
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle(self._title)
        self.setMinimumWidth(450)
        self.setMinimumHeight(250)

        layout = QVBoxLayout(self)

        # Header
        header_label = QLabel(self._header)
        header_label.setWordWrap(True)
        header_label.setStyleSheet("font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(header_label)

        if self._affected:
            # Count label
            count_label = QLabel(
                f"The following {len(self._affected)} entity/entities will be affected:"
            )
            layout.addWidget(count_label)

            # Scrollable entity list
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            scroll_area.setMaximumHeight(200)

            scroll_content = QWidget()
            scroll_layout = QVBoxLayout(scroll_content)
            scroll_layout.setContentsMargins(10, 5, 10, 5)
            scroll_layout.setSpacing(2)

            for uri in self._affected:
                item_label = QLabel(f"• {uri}")
                item_label.setStyleSheet("font-size: 11px;")
                scroll_layout.addWidget(item_label)

            scroll_layout.addStretch()
            scroll_area.setWidget(scroll_content)
            layout.addWidget(scroll_area)

            # Action description
            action_label = QLabel(self._action_description)
            action_label.setStyleSheet("color: #c44; margin-top: 10px;")
            action_label.setWordWrap(True)
            layout.addWidget(action_label)
        else:
            # No affected entities
            no_affected = QLabel("No entities will be affected.")
            no_affected.setStyleSheet("color: #888;")
            layout.addWidget(no_affected)

        layout.addStretch()

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(self._confirm_text)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_affected_count(self) -> int:
        return len(self._affected)
