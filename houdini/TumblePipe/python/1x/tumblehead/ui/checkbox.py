from typing import Optional

from qtpy.QtCore import Qt
from qtpy.QtGui import QColor

from tumblehead.ui import (
    style,
    card,
    button
)

##############################################################################
# Checkbox Card
##############################################################################

class Checkbox(button.ToggleButton):
    def __init__(
        self: 'Checkbox',
        checked: bool = False,
        size: card.Size = card.Size(),
        color: QColor = style.COLOR_DEFAULT,
        font_size: int = 12,
        font_color: QColor = style.COLOR_TEXT,
        tooltip: Optional[str] = None,
        parent = None
        ):
        super().__init__(
            size = size,
            border = card.Border(
                width = 2,
                color = style.COLOR_LOWER
            ),
            color = color,
            font_size = font_size,
            font_color = font_color,
            tooltip = tooltip,
            parent = parent
        )

        # Settings
        self.setObjectName('Card::Checkbox')

        # Set the initial state
        self.set_toggled(checked)
    
    def paint_background(self, painter, shape):

        # Paint the background
        super().paint_background(painter, shape)

        # Draw the check mark
        if not self._toggled: return
        painter.setBrush(self.font_color)
        painter.drawText(
            shape.boundingRect().toRect(),
            Qt.AlignCenter,
            '✓'
        )