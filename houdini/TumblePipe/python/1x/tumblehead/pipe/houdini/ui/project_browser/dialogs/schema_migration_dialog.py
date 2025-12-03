from qtpy.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QDialogButtonBox,
    QCheckBox,
    QGroupBox,
    QScrollArea,
    QWidget,
)
from qtpy.QtCore import Qt


class SchemaMigrationDialog(QDialog):
    """Dialog for confirming schema migration when fields are added/removed"""

    def __init__(self, migration, parent=None):
        super().__init__(parent)
        self._migration = migration
        self._should_migrate = True
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Schema Migration")
        self.setMinimumWidth(500)
        self.setMinimumHeight(300)

        layout = QVBoxLayout(self)

        header = QLabel(f"Schema changes detected for:\n{self._migration.schema_uri}")
        header.setWordWrap(True)
        header.setStyleSheet("font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(header)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)

        if self._migration.additions:
            add_group = QGroupBox("Fields to ADD (with defaults)")
            add_layout = QVBoxLayout(add_group)

            for field_name, (default, entities) in self._migration.additions.items():
                label = QLabel(f"  • {field_name} = {repr(default)}")
                add_layout.addWidget(label)

                entity_count = len(entities)
                entity_label = QLabel(f"      {entity_count} entities will be updated")
                entity_label.setStyleSheet("color: #919191; font-size: 11px;")
                add_layout.addWidget(entity_label)

            scroll_layout.addWidget(add_group)

        if self._migration.removals:
            rem_group = QGroupBox("Fields to REMOVE")
            rem_layout = QVBoxLayout(rem_group)

            for field_name, affected in self._migration.removals.items():
                label = QLabel(f"  • {field_name}")
                rem_layout.addWidget(label)

                entity_count = len(affected)
                entity_label = QLabel(f"      {entity_count} entities have data that will be removed")
                entity_label.setStyleSheet("color: #c44; font-size: 11px;")
                rem_layout.addWidget(entity_label)

            scroll_layout.addWidget(rem_group)

        scroll_layout.addStretch()
        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area)

        self._skip_check = QCheckBox("Skip migration (save schema only, don't update entities)")
        self._skip_check.toggled.connect(self._on_skip_toggled)
        layout.addWidget(self._skip_check)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Apply Migration")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_skip_toggled(self, checked):
        self._should_migrate = not checked

    def should_migrate(self) -> bool:
        return self._should_migrate
