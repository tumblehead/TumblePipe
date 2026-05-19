"""
Dialog for showing validation failures and asking user to continue or cancel export.
"""

from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QCheckBox,
    QPushButton,
    QGroupBox,
)

from tumblepipe.pipe.houdini.validators.base import (
    ValidationResult,
    ValidationSeverity,
)


# Toggle: collapse groups of this size or larger by default in the dialog.
# Smaller groups stay expanded so the user sees specifics immediately.
_COLLAPSE_THRESHOLD = 3


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
        validation_result: ValidationResult | None = None,
        department: str = "",
        entity_name: str = "",
        parent=None,
        validation_message: str | None = None,
        read_only: bool = False,
    ):
        """
        Args:
            validation_result: Structured validation result (preferred). When
                provided, issues are rendered as a collapsible tree grouped by
                (severity, message).
            department: Department name (e.g., "render", "model")
            entity_name: Name of the entity being validated
            parent: Parent widget
            validation_message: Pre-formatted plain-text fallback for callers
                that don't have a ValidationResult. Used only if
                ``validation_result`` is None.
            read_only: If True, hides the Continue/Cancel buttons and the
                "remember choice" checkbox and shows a single Close button.
                Use for review-only contexts (e.g. the export_layer node's
                Validate button, which doesn't gate an export).
        """
        super().__init__(parent)
        self._user_choice = self.CANCEL
        self._remember_choice = False
        self._read_only = read_only

        self.setWindowTitle(f"Validation Issues - {department}")
        self.setModal(True)
        self.setMinimumWidth(700)
        self.setMinimumHeight(450)

        self._setup_ui(validation_result, validation_message, department, entity_name)

    def _setup_ui(
        self,
        result: ValidationResult | None,
        fallback_message: str | None,
        department: str,
        entity_name: str,
    ):
        layout = QVBoxLayout(self)

        # Header
        header = QLabel(f"Validation failed for {entity_name} ({department})")
        header.setStyleSheet("font-weight: bold; color: #ff6b6b; font-size: 14px;")
        layout.addWidget(header)

        # Validation issues — grouped tree
        issues_box = QGroupBox("Validation Issues")
        issues_layout = QVBoxLayout(issues_box)

        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setRootIsDecorated(True)
        tree.setUniformRowHeights(True)
        tree.setAlternatingRowColors(False)
        tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self._populate_tree(tree, result, fallback_message)
        issues_layout.addWidget(tree)

        layout.addWidget(issues_box)

        # Buttons + checkbox layout depends on mode
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        if self._read_only:
            self._remember_checkbox = None
            close_btn = QPushButton("Close")
            close_btn.setDefault(True)
            close_btn.clicked.connect(self.accept)
            button_layout.addWidget(close_btn)
        else:
            # Remember choice checkbox above buttons
            self._remember_checkbox = QCheckBox(
                "Apply this choice to remaining validations"
            )
            layout.addWidget(self._remember_checkbox)

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

    def _populate_tree(
        self,
        tree: QTreeWidget,
        result: ValidationResult | None,
        fallback_message: str | None,
    ):
        if result is None:
            # No structured result — show the pre-formatted text as a flat
            # list, splitting on newlines, so we still render something useful.
            text = fallback_message or ""
            for line in text.splitlines():
                if not line.strip():
                    continue
                QTreeWidgetItem(tree, [line])
            return

        for severity, message, prim_paths, suggestion in result.grouped_issues():
            prefix = "ERROR" if severity == ValidationSeverity.ERROR else "WARNING"
            bulb = " \U0001f4a1" if suggestion else ""

            if len(prim_paths) <= 1:
                # Single occurrence — render inline; no expansion needed
                path = prim_paths[0] if prim_paths else None
                label = f"[{prefix}] {path}: {message}{bulb}" if path else f"[{prefix}] {message}{bulb}"
                item = QTreeWidgetItem(tree, [label])
                _style_severity(item, severity)
                if suggestion:
                    item.setToolTip(0, f"Suggestion: {suggestion}")
                continue

            # Multiple occurrences — summary parent + children for each path
            summary = f"[{prefix}] {message}  —  {len(prim_paths)} prims{bulb}"
            parent = QTreeWidgetItem(tree, [summary])
            _style_severity(parent, severity)
            if suggestion:
                parent.setToolTip(0, f"Suggestion: {suggestion}")
            for path in prim_paths:
                child = QTreeWidgetItem(parent, [path])
                child.setForeground(0, QColor("#aaaaaa"))
            parent.setExpanded(len(prim_paths) < _COLLAPSE_THRESHOLD)

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


def _style_severity(item: QTreeWidgetItem, severity: ValidationSeverity):
    if severity == ValidationSeverity.ERROR:
        item.setForeground(0, QColor("#ff6b6b"))
    else:
        item.setForeground(0, QColor("#e8c170"))
