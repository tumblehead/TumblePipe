"""
Dialog for showing validation failures and asking user to continue or cancel export.
"""

from qtpy.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QCheckBox,
    QPushButton,
    QGroupBox,
)


class ValidationCancelled(Exception):
    """Raised when user cancels validation via the dialog.

    This exception is caught separately from other errors so that
    the task is marked as SKIPPED rather than FAILED, and no error
    dialogs are shown to the user.
    """
    pass


class ValidationConfirmDialog(QDialog):
    """
    Dialog shown when validation fails, asking user whether to:
    - Continue with export anyway
    - Cancel the export

    Also provides a "remember choice" option for subsequent validations.
    """

    CONTINUE = 1
    CANCEL = 2

    def __init__(
        self,
        validation_message: str,
        department: str,
        entity_name: str,
        parent=None
    ):
        """
        Args:
            validation_message: Formatted validation error/warning messages
            department: Department name (e.g., "render", "model")
            entity_name: Name of the entity being validated
            parent: Parent widget
        """
        super().__init__(parent)
        self._user_choice = self.CANCEL
        self._remember_choice = False

        self.setWindowTitle(f"Validation Issues - {department}")
        self.setModal(True)
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)

        self._setup_ui(validation_message, department, entity_name)

    def _setup_ui(self, message: str, department: str, entity_name: str):
        layout = QVBoxLayout(self)

        # Header
        header = QLabel(f"Validation failed for {entity_name} ({department})")
        header.setStyleSheet("font-weight: bold; color: #ff6b6b; font-size: 14px;")
        layout.addWidget(header)

        # Validation issues (scrollable, selectable for copy)
        issues_box = QGroupBox("Validation Issues")
        issues_layout = QVBoxLayout(issues_box)

        text_edit = QPlainTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(message)
        text_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        issues_layout.addWidget(text_edit)

        layout.addWidget(issues_box)

        # Remember choice checkbox
        self._remember_checkbox = QCheckBox(
            "Apply this choice to remaining validations"
        )
        layout.addWidget(self._remember_checkbox)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        continue_btn = QPushButton("Continue Anyway")
        continue_btn.setToolTip("Proceed with export despite validation errors")
        continue_btn.clicked.connect(self._on_continue)

        cancel_btn = QPushButton("Cancel Export")
        cancel_btn.setToolTip("Stop the export process")
        cancel_btn.setDefault(True)
        cancel_btn.clicked.connect(self._on_cancel)

        button_layout.addWidget(continue_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

    def _on_continue(self):
        self._user_choice = self.CONTINUE
        self._remember_choice = self._remember_checkbox.isChecked()
        self.accept()

    def _on_cancel(self):
        self._user_choice = self.CANCEL
        self._remember_choice = self._remember_checkbox.isChecked()
        self.reject()

    @property
    def user_choice(self) -> int:
        """Return the user's choice (CONTINUE or CANCEL)."""
        return self._user_choice

    @property
    def remember_choice(self) -> bool:
        """Return whether the user wants to remember this choice."""
        return self._remember_choice
