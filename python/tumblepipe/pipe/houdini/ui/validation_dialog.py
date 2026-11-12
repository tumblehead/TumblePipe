"""
Dialog for showing validation failures and asking user to continue or cancel export.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
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

from . import validation_preflight


# Toggle: collapse groups of this size or larger by default in the dialog.
# Smaller groups stay expanded so the user sees specifics immediately.
_COLLAPSE_THRESHOLD = 3

# Whether "Apply this choice to remaining validations" starts ticked (#36).
REMEMBER_BY_DEFAULT = True


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
        # A result with warnings only passed: say so (the rig validation
        # shows its dialog for warnings too)
        if result is not None and result.passed:
            header = QLabel(f"Validation warnings for {entity_name} ({department})")
            header.setStyleSheet("font-weight: bold; color: #f0c05a; font-size: 14px;")
        else:
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
            # Ticked by default: a Multi publish asked once per shot, behind
            # a modal each time (#36). Untick it to answer shot by shot.
            self._remember_checkbox.setChecked(REMEMBER_BY_DEFAULT)
            self._remember_checkbox.setToolTip(
                "Continue Anyway or Cancel Export also answers every later "
                "validation of this run. Untick to be asked for each one."
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

        _add_issue_items(tree, result)

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


def _add_issue_items(parent, result: ValidationResult):
    """Add ``result``'s issues under ``parent`` (a tree or an item),
    grouped by (severity, message): one row per group, its prims below."""
    for severity, message, prim_paths, suggestion in result.grouped_issues():
        prefix = "ERROR" if severity == ValidationSeverity.ERROR else "WARNING"
        bulb = " \U0001f4a1" if suggestion else ""

        if len(prim_paths) <= 1:
            # Single occurrence — render inline; no expansion needed
            path = prim_paths[0] if prim_paths else None
            label = f"[{prefix}] {path}: {message}{bulb}" if path else f"[{prefix}] {message}{bulb}"
            item = QTreeWidgetItem(parent, [label])
            _style_severity(item, severity)
            if suggestion:
                item.setToolTip(0, f"Suggestion: {suggestion}")
            continue

        # Multiple occurrences — summary parent + children for each path
        summary = f"[{prefix}] {message}  —  {len(prim_paths)} prims{bulb}"
        group = QTreeWidgetItem(parent, [summary])
        _style_severity(group, severity)
        if suggestion:
            group.setToolTip(0, f"Suggestion: {suggestion}")
        for path in prim_paths:
            child = QTreeWidgetItem(group, [path])
            child.setForeground(0, QColor("#aaaaaa"))
        group.setExpanded(len(prim_paths) < _COLLAPSE_THRESHOLD)


def _entity_label(entity: str) -> str:
    """A shot or asset's name for the dialog, from its URI string."""
    from tumblepipe.util.uri import Uri
    try:
        return Uri.parse(entity).display_name()
    except Exception:
        return entity


def _entities_phrase(entities: list[str]) -> str:
    """"2 shots", "1 asset", "3 shots and assets"."""
    shots = all(entity.startswith('entity:/shots/') for entity in entities)
    assets = all(entity.startswith('entity:/assets/') for entity in entities)
    count = len(entities)
    if shots:
        noun = 'shot' if count == 1 else 'shots'
    elif assets:
        noun = 'asset' if count == 1 else 'assets'
    else:
        noun = 'shots and assets'
    return f"{count} {noun}"


class PreflightValidationDialog(QDialog):
    """The up-front validation dialog of a publish run (#56).

    Lists every issue the run's validations found before anything
    exported, grouped by shot (or asset) and department. Each shot has a
    checkbox: Continue Anyway runs the ticked shots and skips the
    unticked ones' exports, Cancel Export runs nothing.

    ``grouped`` is ``validation_preflight.group_findings``' result.
    """

    CONTINUE = validation_preflight.CONTINUE
    CANCEL = validation_preflight.CANCEL

    def __init__(self, grouped: list, parent=None):
        super().__init__(parent)
        issue_count = validation_preflight.issue_count

        self._user_choice = self.CANCEL
        self._entity_items: dict[str, QTreeWidgetItem] = {}

        self.setWindowTitle("Validation Issues")
        self.setModal(True)
        self.setMinimumWidth(760)
        self.setMinimumHeight(480)

        layout = QVBoxLayout(self)
        count = issue_count(grouped)
        self._header = QLabel(
            f"Validation found {count} issue{'s' if count != 1 else ''} in "
            f"{_entities_phrase([entity for entity, _ in grouped])}, "
            "before anything exported"
        )
        self._header.setStyleSheet("font-weight: bold; color: #ff6b6b; font-size: 14px;")
        layout.addWidget(self._header)
        note = QLabel(
            "Untick a shot to skip its exports. Continue Anyway runs the "
            "ticked ones; an issue listed here does not ask again during "
            "the run, only one the run itself brings in."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        issues_box = QGroupBox("Validation Issues")
        issues_layout = QVBoxLayout(issues_box)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self._populate(grouped)
        issues_layout.addWidget(self._tree)
        layout.addWidget(issues_box)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self._continue_btn = QPushButton("Continue Anyway")
        self._continue_btn.setToolTip(
            "Export the ticked shots despite these issues")
        self._continue_btn.clicked.connect(self._on_continue)
        self._cancel_btn = QPushButton("Cancel Export")
        self._cancel_btn.setToolTip("Export nothing")
        self._cancel_btn.setDefault(True)
        self._cancel_btn.clicked.connect(self._on_cancel)
        button_layout.addWidget(self._continue_btn)
        button_layout.addWidget(self._cancel_btn)
        layout.addLayout(button_layout)

    def _populate(self, grouped: list):
        for entity, departments in grouped:
            count = sum(len(issues) for _, issues in departments)
            entity_item = QTreeWidgetItem(
                self._tree,
                [f"{_entity_label(entity)}  —  {count} issue{'s' if count != 1 else ''}"],
            )
            entity_item.setFlags(entity_item.flags() | Qt.ItemIsUserCheckable)
            entity_item.setCheckState(0, Qt.Checked)
            entity_item.setToolTip(0, "Untick to skip this shot's exports")
            self._entity_items[entity] = entity_item
            for department, issues in departments:
                errors = any(i.severity == ValidationSeverity.ERROR for i in issues)
                department_item = QTreeWidgetItem(entity_item, [department])
                _style_severity(
                    department_item,
                    ValidationSeverity.ERROR if errors else ValidationSeverity.WARNING,
                )
                _add_issue_items(
                    department_item,
                    ValidationResult(passed=not errors, issues=list(issues)),
                )
                department_item.setExpanded(True)
            entity_item.setExpanded(True)

    def _on_continue(self):
        self._user_choice = self.CONTINUE
        self.accept()

    def _on_cancel(self):
        self._user_choice = self.CANCEL
        self.reject()

    @property
    def user_choice(self) -> str:
        """CONTINUE or CANCEL (``validation_preflight``'s values)."""
        return self._user_choice

    def kept_entities(self) -> list[str]:
        """The entity URI strings still ticked."""
        return [
            entity for entity, item in self._entity_items.items()
            if item.checkState(0) == Qt.Checked
        ]

    def set_entity_kept(self, entity: str, kept: bool):
        """Tick or untick a shot, as the artist would."""
        self._entity_items[entity].setCheckState(
            0, Qt.Checked if kept else Qt.Unchecked)


def _style_severity(item: QTreeWidgetItem, severity: ValidationSeverity):
    if severity == ValidationSeverity.ERROR:
        item.setForeground(0, QColor("#ff6b6b"))
    else:
        item.setForeground(0, QColor("#e8c170"))
