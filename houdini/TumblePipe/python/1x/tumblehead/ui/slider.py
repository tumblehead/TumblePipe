from typing import Optional

from qtpy.QtCore import (
    Qt,
    Signal,
    Property,
    QRect
)
from qtpy.QtGui import (
    QColor,
    QBrush
)

from tumblehead.ui import (
    card,
    style
)

class Slider(card.Card):
    value_changed = Signal(object, int)

    def __init__(
        self: 'Slider',
        value: int,
        min_value: int,
        max_value: int,
        orientation: Qt.Orientation = Qt.Horizontal,
        direction: Qt.LayoutDirection = Qt.LeftToRight,
        slider_color: QColor = style.COLOR_ACTIVE,
        background_color: QColor = style.COLOR_LOW,
        text_color: QColor = style.COLOR_TEXT,
        size: card.Size = card.Size(),
        parent = None
        ):
        super().__init__(
            color = background_color,
            size = size,
            focusable = True,
            interaction = card.DragInteraction(self),
            parent = parent
        )

        # Settings
        self.setObjectName('Card::Slider')

        # Members
        self._value = value
        self._min_value = min_value
        self._max_value = max_value
        self._orientation = orientation
        self._direction = direction
        self._slider_color = slider_color
        self._text_color = text_color
        self._stored = None

        # Handle the drag signal
        self.dragged.connect(self._on_dragged)
    
    def set_value(self, value):
        self._value = value
        self.value_changed.emit(self, value)
        self.update()
    
    def get_value(self):
        return self._value
    
    @Property(QBrush)
    def foreground(self):
        value = self._slider_color.value()
        color = (
            self._slider_color
            if self.is_enabled else
            QColor(value, value, value)
        )
        style = (
            Qt.SolidPattern
            if self.is_enabled else
            Qt.Dense4Pattern
        )
        return QBrush(color, style)

    def _slider_rect(self):
        result = self.rect()
        match (self._orientation, self._direction):
            case (Qt.Horizontal, Qt.LeftToRight):
                result.setRight((self._value / self._max_value) * result.width())
            case (Qt.Horizontal, Qt.RightToLeft):
                result.setLeft(result.width() - (self._value / self._max_value) * result.width())
            case (Qt.Vertical, Qt.LeftToRight):
                result.setTop(result.height() - (self._value / self._max_value) * result.height())
            case (Qt.Vertical, Qt.RightToLeft):
                result.setBottom((self._value / self._max_value) * result.height())
        return result

    def _location_to_value(self, global_location):
        rect = self.rect()
        width = rect.width()
        height = rect.height()
        location = global_location - self.mapToGlobal(rect.topLeft())
        x = (location.x() / width) * self._max_value
        y = (location.y() / height) * self._max_value
        match (self._orientation, self._direction):
            case (Qt.Horizontal, Qt.LeftToRight): result = x
            case (Qt.Horizontal, Qt.RightToLeft): result = self._max_value - x
            case (Qt.Vertical, Qt.LeftToRight): result = self._max_value - y
            case (Qt.Vertical, Qt.RightToLeft): result = y
        return max(self._min_value, min(self._max_value, result))

    def paint_background(self, painter, shape):
        
        # Paint the background
        super().paint_background(painter, shape)

        # Paint the slider
        slider_rect = self._slider_rect()
        painter.setBrush(self.foreground)
        painter.setPen(Qt.NoPen)
        painter.drawRect(slider_rect)

        # Prepare bounding rect
        rect = self.rect()
        width = rect.width()
        height = rect.height()
        if self._orientation == Qt.Vertical:
            rect = QRect(
                rect.x(),
                rect.y(),
                height,
                width
            )

        # Paint the percentage
        fraction = self._value / self._max_value
        percentage_text = f'{int(fraction * 100)} %'
        painter.setPen(self._text_color)
        match self._orientation:
            case Qt.Horizontal: pass
            case Qt.Vertical:
                painter.rotate(-90)
                painter.translate(-height, 0)
        painter.drawText(
            rect,
            Qt.AlignCenter,
            percentage_text
        )
    
    def paint_focus(self, painter, _shape):
        slider_rect = self._slider_rect()
        painter.setBrush(QBrush(self._slider_color.lighter(150)))
        painter.setPen(Qt.NoPen)
        painter.drawRect(slider_rect)

    def _on_dragged(self, event):
        if event.button != Qt.LeftButton: return
        self._value = self._location_to_value(event.location)
        self.value_changed.emit(self, self._value)
        self.update()
    
    def _on_link_value_changed(self, _other, value):
        self._value = value
        self.update()
    
    def link(self, other: 'Slider'):
        if self._stored is not None: return
        self._stored = other
        self.set_disabled(True)
        self.set_value(other.get_value())
        other.value_changed.connect(self._on_link_value_changed)
    
    def unlink(self):
        if self._stored is None: return
        other = self._stored
        self._stored = None
        other.value_changed.disconnect(self._on_link_value_changed)
        self.set_disabled(False)