from typing import Optional
import math

from qtpy.QtCore import (
    Signal,
    QPropertyAnimation,
    Property,
    QRect,
    Qt
)
from qtpy.QtGui import (
    QConicalGradient,
    QColor,
    QBrush,
    QPen
)
from qtpy.QtWidgets import (
    QWidget
)

from tumblehead.ui import (
    card,
    style
)

def _add_angle(angle: int, offset: int) -> int:
    return (angle + offset) % 360

class Spinner(card.Card):
    change = Signal()

    def __init__(
        self,
        size: float = 1.0,
        width: float = 0.1,
        length: float = 0.8,
        speed: int = 1000,
        color: QColor = style.COLOR_ACTIVE,
        parent: Optional[QWidget] = None
        ):
        super().__init__(parent = parent)

        # Settings
        self.setObjectName('Spinner')

        # Members
        self._size = size
        self._width = width
        self._length = length
        self._speed = speed
        self._color = color
        self._angle = 0
        self._running = False
        self._animation = QPropertyAnimation(self, b'angle', self)

        # Setup animation
        self._animation.setStartValue(0)
        self._animation.setEndValue(360)
        self._animation.setDuration(speed)
        self._animation.setLoopCount(-1)

        # Connect signals
        self.change.connect(self.update)
    
    @Property(int)
    def angle(self):
        return self._angle

    @angle.setter
    def angle(self, angle):
        self._angle = angle
        self.change.emit()
    
    def is_running(self):
        return self._running

    def start(self):
        if self._running: return
        self._animation.start()
        self._running = True
    
    def stop(self):
        if not self._running: return
        self._animation.stop()
        self._running = False
    
    def set_running(self, running):
        if running: self.start()
        else: self.stop()
    
    def paint_background(self, painter, shape):
        if not self._running: return

        # Define the drawing rect
        rect = shape.boundingRect()
        size = min(rect.width(), rect.height())
        width = int(size * self._width)
        diameter = int((size - width) * self._size)
        x = rect.x() + ((rect.width() - diameter) // 2) + 1
        y = rect.y() + ((rect.height() - diameter) // 2) + 1
        drawing_rect = QRect(x, y, diameter - 1, diameter - 1)

        # Define the gradient
        circumference = math.pi * diameter
        cap_offset = (circumference / width) // 2
        gradient = QConicalGradient(
            drawing_rect.center(),
            -_add_angle(self.angle, cap_offset)
        )
        gradient.setColorAt(0.0, self._color)
        gradient.setColorAt(self._length, style.COLOR_NONE)

        # Draw the spinner
        pen = QPen(QBrush(gradient), width)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(
            drawing_rect,
            -self.angle * 16,
            int(360 * self._length) * 16
        )