from typing import Optional

from qtpy.QtCore import (
    Qt,
    Signal,
    QSize
)
from qtpy.QtGui import (
    QColor,
    QBrush,
    QFontMetrics
)
from qtpy.QtWidgets import (
    QWidget,
    QSizePolicy,
    QHBoxLayout,
    QLabel,
    QMenu
)

from tumblehead.ui import (
    card,
    style
)

class ElidedLabel(QLabel):
    def __init__(
        self,
        text: str = '',
        mode: Qt.TextElideMode = Qt.ElideRight,
        parent: QWidget = None
        ):
        super().__init__(text, parent)

        # Settings
        self.setObjectName('ElidedLabel')

        # Members
        self._mode = mode
    
    def _elide_text(self):
        metrics = QFontMetrics(self.font())
        return metrics.elidedText(
            self.text(), self._mode, self.width()
        )
    
    def set_elide_mode(self, mode):
        self._mode = mode
        self.update()

    def paintEvent(self, event):

        # Handle the case where elide mode is none
        if self._mode == Qt.ElideNone:
            return super().paintEvent(event)

        # Draw the elided text
        super().setText(self._elide_text())
        super().paintEvent(event)

EXPAND_ICON = '▼'
COLLAPSE_ICON = '▲'

class Dropdown(card.Card):
    selection_changed = Signal(object, str)

    def __init__(
        self: 'Dropdown',
        items: list[str],
        selected: str,
        color: QColor = style.COLOR_DEFAULT,
        font_size: int = 10,
        font_color: QColor = style.COLOR_TEXT,
        size: card.Size = card.Size(),
        parent = None
        ):
        super().__init__(
            color = color,
            size = size,
            focusable = True,
            interaction = card.ClickInteraction(self),
            parent = parent
        )

        # Settings
        self.setObjectName('Card::Dropdown')

        # Members
        self._items = items
        self._selected = selected
        self._font_size = font_size
        self._font_color = font_color
        self._stored = None

        # Create the content
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(5, 0, 5, 0)
        content_layout.setSpacing(0)

        # Create the selection label
        self.selection_label = ElidedLabel()
        content_layout.addWidget(self.selection_label, 1)

        # Create the dropdown button
        self.dropdown_button = QLabel(EXPAND_ICON)
        content_layout.addWidget(self.dropdown_button)

        # Set the content
        self.set_content(content)

        # Connect the clicked signal
        self.clicked.connect(self._on_clicked)

        # Initial update
        self._update()
    
    def _update(self):
        
        # Update the selection label
        self.selection_label.setText(self._selected)
        self.selection_label.setStyleSheet(
            f'color: {self._font_color.name()}; '
        )
        font = self.selection_label.font()
        font.setPointSize(self._font_size)
        self.selection_label.setFont(font)

        # Update the card
        self.update()
    
    def get_items(self):
        return self._items
    
    def set_items(self, items):
        self._items = items
        self._update()
    
    def set_selected(self, selected):
        self._selected = selected
        self.selection_changed.emit(self, self._selected)
        self._update()
    
    def get_selected(self):
        return self._selected
    
    def set_font_size(self, font_size):
        self._font_size = font_size
        self._update()
    
    def get_font_size(self):
        return self._font_size
    
    def set_font_color(self, font_color):
        self._font_color = font_color
        self._update()
    
    def get_font_color(self):
        return self._font_color
    
    def _on_clicked(self, event):
        if event.button != Qt.LeftButton: return
        self.dropdown_button.setText(COLLAPSE_ICON)
        dropdown_menu = QMenu(self)
        for item in self._items:
            dropdown_menu.addAction(item)
        selected_action = dropdown_menu.exec_(
            self.mapToGlobal(self.rect().bottomLeft())
        )
        self.dropdown_button.setText(EXPAND_ICON)
        if selected_action is None: return
        self.set_selected(selected_action.text())
    
    def paint_pressed(self, painter, shape):
        painter.setBrush(QBrush(self.get_color().darker(150)))
        painter.setPen(Qt.NoPen)
        painter.drawPath(shape)
    
    def _on_link_selection_changed(self, _other, selected):
        self._selected = selected
        self._update()
    
    def link(self, other: 'Dropdown'):
        if self._stored is not None: return
        self._stored = other
        self.set_disabled(True)
        self.set_selected(other.get_selected())
        other.selection_changed.connect(self._on_link_selection_changed)
    
    def unlink(self):
        if self._stored is None: return
        other = self._stored
        self._stored = None
        other.selection_changed.disconnect(self._on_link_selection_changed)
        self.set_disabled(False)