"""Collapsible section widget for job submission with integrated table."""

from qtpy.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve
from qtpy.QtGui import QColor, QPen, QBrush, QPainter, QFont
from qtpy.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QTableView,
    QHeaderView,
    QSizePolicy,
    QAbstractItemView,
)

from tumblehead.pipe.houdini.ui.project_browser.models.job_schemas import (
    JobTypeSchema,
    load_column_visibility,
    save_column_visibility,
)
from tumblehead.pipe.houdini.ui.project_browser.models.job_submission_table import JobSubmissionTableModel
from tumblehead.pipe.houdini.ui.project_browser.widgets.job_submission_delegate import JobSubmissionDelegate
from tumblehead.pipe.houdini.ui.project_browser.widgets.table import CellSelectionTableView
from tumblehead.pipe.houdini.ui.project_browser.widgets.column_visibility_dialog import ColumnVisibilityDialog


class SectionHeader(QFrame):
    """Custom header with checkbox, title, settings button, and collapse button."""

    toggled = Signal(bool)         # Emitted when checkbox is toggled
    collapsed = Signal(bool)       # Emitted when collapse button is clicked
    settings_clicked = Signal()    # Emitted when settings button is clicked

    # Layout constants
    HEIGHT = 28
    CHECKBOX_SIZE = 14
    COLLAPSE_SIZE = 14
    SETTINGS_SIZE = 14
    PADDING = 8

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        self._enabled = False   # Start disabled
        self._collapsed = True  # Start collapsed

        # Hover tracking
        self._hover_checkbox = False
        self._hover_collapse = False
        self._hover_settings = False

        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        if self._enabled != value:
            self._enabled = value
            self.toggled.emit(value)
            # Auto-expand when enabled, auto-collapse when disabled
            if value and self._collapsed:
                self.is_collapsed = False
            elif not value and not self._collapsed:
                self.is_collapsed = True
            self.update()

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    @is_collapsed.setter
    def is_collapsed(self, value: bool):
        if self._collapsed != value:
            self._collapsed = value
            self.collapsed.emit(value)
            self.update()

    def _checkbox_rect(self):
        """Get checkbox rectangle."""
        x = self.PADDING
        y = (self.HEIGHT - self.CHECKBOX_SIZE) // 2
        from qtpy.QtCore import QRect
        return QRect(x, y, self.CHECKBOX_SIZE, self.CHECKBOX_SIZE)

    def _collapse_rect(self):
        """Get collapse button rectangle."""
        x = self.width() - self.PADDING - self.COLLAPSE_SIZE
        y = (self.HEIGHT - self.COLLAPSE_SIZE) // 2
        from qtpy.QtCore import QRect
        return QRect(x, y, self.COLLAPSE_SIZE, self.COLLAPSE_SIZE)

    def _settings_rect(self):
        """Get settings button rectangle (between title and collapse)."""
        collapse_rect = self._collapse_rect()
        x = collapse_rect.left() - self.PADDING - self.SETTINGS_SIZE
        y = (self.HEIGHT - self.SETTINGS_SIZE) // 2
        from qtpy.QtCore import QRect
        return QRect(x, y, self.SETTINGS_SIZE, self.SETTINGS_SIZE)

    def mousePressEvent(self, event):
        """Handle clicks on checkbox, settings, and collapse button."""
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)

        pos = event.pos()
        if self._checkbox_rect().contains(pos):
            self.enabled = not self._enabled
            return
        if self._settings_rect().contains(pos):
            self.settings_clicked.emit()
            return
        if self._collapse_rect().contains(pos):
            self.is_collapsed = not self._collapsed
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Track hover state."""
        pos = event.pos()
        new_hover_checkbox = self._checkbox_rect().contains(pos)
        new_hover_collapse = self._collapse_rect().contains(pos)
        new_hover_settings = self._settings_rect().contains(pos)

        if (new_hover_checkbox != self._hover_checkbox or
            new_hover_collapse != self._hover_collapse or
            new_hover_settings != self._hover_settings):
            self._hover_checkbox = new_hover_checkbox
            self._hover_collapse = new_hover_collapse
            self._hover_settings = new_hover_settings
            self.update()

        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        """Clear hover state."""
        self._hover_checkbox = False
        self._hover_collapse = False
        self._hover_settings = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        """Custom paint."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Colors
        bg_color = QColor("#353535")
        border_color = QColor("#1a1a1a")
        text_color = QColor("#ffffff")
        disabled_text = QColor("#888888")
        checkbox_on = QColor("#5e4a8a")
        checkbox_off = QColor("#444444")

        # Background
        painter.fillRect(self.rect(), bg_color)

        # Border
        painter.setPen(QPen(border_color, 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        # Checkbox
        cb_rect = self._checkbox_rect()
        cb_color = checkbox_on if self._enabled else checkbox_off
        if self._hover_checkbox:
            cb_color = cb_color.lighter(120)
        painter.fillRect(cb_rect, cb_color)
        painter.setPen(QPen(border_color, 1))
        painter.drawRect(cb_rect.adjusted(0, 0, -1, -1))

        # Check mark
        if self._enabled:
            painter.setPen(QPen(QColor("#ffffff"), 2))
            margin = 3
            painter.drawLine(cb_rect.left() + margin, cb_rect.center().y(),
                           cb_rect.center().x(), cb_rect.bottom() - margin)
            painter.drawLine(cb_rect.center().x(), cb_rect.bottom() - margin,
                           cb_rect.right() - margin, cb_rect.top() + margin)

        # Title (adjusted width to account for settings button)
        title_x = cb_rect.right() + self.PADDING
        settings_rect = self._settings_rect()
        title_width = settings_rect.left() - title_x - self.PADDING
        from qtpy.QtCore import QRect
        title_rect = QRect(title_x, 0, title_width, self.HEIGHT)

        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(text_color if self._enabled else disabled_text)
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, self._title)

        # Settings button (gear icon)
        settings_color = text_color if self._enabled else disabled_text
        if self._hover_settings:
            settings_color = settings_color.lighter(130)
        painter.setPen(settings_color)
        font.setBold(False)
        font.setPixelSize(12)
        painter.setFont(font)
        painter.drawText(settings_rect, Qt.AlignCenter, "⚙")

        # Collapse button (triangle)
        collapse_rect = self._collapse_rect()
        painter.setPen(Qt.NoPen)
        arrow_color = text_color if self._enabled else disabled_text
        if self._hover_collapse:
            arrow_color = arrow_color.lighter(130)
        painter.setBrush(QBrush(arrow_color))

        center = collapse_rect.center()
        size = 5
        from qtpy.QtCore import QPoint

        if self._collapsed:
            # Right-pointing triangle
            points = [
                QPoint(center.x() - size // 2, center.y() - size),
                QPoint(center.x() + size // 2 + 1, center.y()),
                QPoint(center.x() - size // 2, center.y() + size),
            ]
        else:
            # Down-pointing triangle
            points = [
                QPoint(center.x() - size, center.y() - size // 2),
                QPoint(center.x() + size, center.y() - size // 2),
                QPoint(center.x(), center.y() + size // 2 + 1),
            ]

        painter.drawPolygon(points)
        painter.end()


class JobSectionWidget(QWidget):
    """
    Collapsible section widget containing a job configuration table.

    Each section has:
    - Header with checkbox to enable/disable, settings button, and collapse button
    - Table view with schema-defined columns
    - Self-contained model and delegate
    - Column visibility management with user preferences
    """

    enabled_changed = Signal(bool)    # Section enabled state changed
    collapsed_changed = Signal(bool)  # Section collapsed state changed

    def __init__(self, title: str, schema: JobTypeSchema, parent=None):
        super().__init__(parent)
        self._title = title
        self._schema = schema

        # Load hidden columns from user preferences
        self._hidden_columns: set[str] = load_column_visibility(schema.job_type)

        self._setup_ui()

        # Apply initial column visibility
        self._apply_column_visibility()

    def _setup_ui(self):
        """Create the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        self._header = SectionHeader(self._title)
        self._header.toggled.connect(self._on_toggled)
        self._header.collapsed.connect(self._on_collapsed)
        self._header.settings_clicked.connect(self._on_settings_clicked)
        layout.addWidget(self._header)

        # Table container (for collapse animation)
        self._table_container = QWidget()
        table_layout = QVBoxLayout(self._table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(0)

        # Table view
        self._table = CellSelectionTableView()
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self._table.setShowGrid(False)

        # Disable internal scrolling - let parent scroll area handle it
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Size policy to fit content
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        # Model
        self._model = JobSubmissionTableModel()
        self._model.set_schema(self._schema)
        self._table.setModel(self._model)

        # Delegate
        self._delegate = JobSubmissionDelegate(self._table)
        self._table.setItemDelegate(self._delegate)

        # Set column widths from schema
        self._apply_column_widths()

        table_layout.addWidget(self._table)
        layout.addWidget(self._table_container)

        # Connect model changes to resize table
        self._model.rowsInserted.connect(self._update_table_height)
        self._model.rowsRemoved.connect(self._update_table_height)
        self._model.modelReset.connect(self._update_table_height)

        # Start collapsed (table hidden)
        self._table_container.setVisible(False)

    def _apply_column_widths(self):
        """Apply column widths from schema."""
        # Entity column
        self._table.setColumnWidth(0, 120)

        # Schema columns
        for i, col_def in enumerate(self._schema.columns):
            self._table.setColumnWidth(i + 1, col_def.width)

    def _update_table_height(self):
        """Update table height to fit content (for combined scrolling)."""
        row_count = self._model.rowCount()
        if row_count == 0:
            # Minimum height when empty
            self._table.setFixedHeight(50)
            return

        # Calculate height: header + rows
        header_height = self._table.horizontalHeader().height()
        row_height = self._table.verticalHeader().defaultSectionSize()
        total_height = header_height + (row_count * row_height) + 2  # +2 for borders

        self._table.setFixedHeight(total_height)

    def _on_toggled(self, enabled: bool):
        """Handle enable/disable toggle."""
        self.enabled_changed.emit(enabled)

    def _on_collapsed(self, collapsed: bool):
        """Handle collapse/expand."""
        self._table_container.setVisible(not collapsed)
        self.collapsed_changed.emit(collapsed)

    def _on_settings_clicked(self):
        """Handle settings button click - open column visibility dialog."""
        dialog = ColumnVisibilityDialog(self._schema, self._hidden_columns, self)
        if dialog.exec_():
            # Get new hidden columns from dialog
            self._hidden_columns = dialog.get_hidden_columns()
            # Apply visibility to table
            self._apply_column_visibility()
            # Save to user preferences
            save_column_visibility(self._schema.job_type, self._hidden_columns)

    def _apply_column_visibility(self):
        """Apply column visibility settings to the table view."""
        # Entity column (index 0) is always visible
        for i, col_def in enumerate(self._schema.columns):
            col_index = i + 1  # +1 for entity column
            is_hidden = col_def.key in self._hidden_columns
            self._table.setColumnHidden(col_index, is_hidden)

    # =========================================================================
    # Public API
    # =========================================================================

    @property
    def title(self) -> str:
        """Get section title."""
        return self._title

    @property
    def schema(self) -> JobTypeSchema:
        """Get the schema."""
        return self._schema

    @property
    def model(self) -> JobSubmissionTableModel:
        """Get the table model."""
        return self._model

    @property
    def table(self) -> QTableView:
        """Get the table view."""
        return self._table

    @property
    def enabled(self) -> bool:
        """Check if section is enabled."""
        return self._header.enabled

    @enabled.setter
    def enabled(self, value: bool):
        """Set section enabled state."""
        self._header.enabled = value

    @property
    def collapsed(self) -> bool:
        """Check if section is collapsed."""
        return self._header.is_collapsed

    @collapsed.setter
    def collapsed(self, value: bool):
        """Set section collapsed state."""
        self._header.is_collapsed = value

    def set_entities(self, entities: list[tuple[str, str, str]]):
        """
        Set entities to display in the table.
        Each tuple is (uri, display_name, context).
        """
        self._model.clear_rows()
        self._model.add_entities(entities)

    def clear_entities(self):
        """Clear all entities from the table."""
        self._model.clear_rows()

    def get_configs(self) -> list[dict]:
        """
        Get job configuration for each entity.
        Returns list of dicts with entity info and settings.
        """
        configs = []
        for row in range(self._model.rowCount()):
            uri_index = self._model.index(row, 0)
            entity_uri = uri_index.data(JobSubmissionTableModel.ROLE_ENTITY_URI)
            entity_name = uri_index.data(Qt.DisplayRole)
            entity_context = uri_index.data(JobSubmissionTableModel.ROLE_ENTITY_CONTEXT)

            settings = {}
            for col_def in self._schema.columns:
                col_index = self._model._get_column_index(col_def.key)
                if col_index >= 0:
                    index = self._model.index(row, col_index)
                    settings[col_def.key] = index.data(JobSubmissionTableModel.ROLE_RAW_VALUE)

            configs.append({
                'entity': {
                    'uri': entity_uri,
                    'name': entity_name,
                    'context': entity_context,
                },
                'settings': settings,
            })

        return configs

    def refresh_entity_choices(self):
        """Refresh per-entity choices (e.g., variants) from cache."""
        # Re-fetch choices for all rows
        for row in self._model._rows:
            self._model._fetch_entity_choices(row)
            # Re-init cells to update defaults for per_entity_choices columns
            self._model._init_row_cells(row)

        # Emit change to refresh view
        if self._model.rowCount() > 0:
            top_left = self._model.index(0, 0)
            bottom_right = self._model.index(
                self._model.rowCount() - 1,
                self._model.columnCount() - 1
            )
            self._model.dataChanged.emit(top_left, bottom_right)
