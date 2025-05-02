from dataclasses import dataclass
from typing import Optional

from qtpy.QtCore import Signal
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QWidget,
    QLabel,
    QGridLayout
)

from tumblehead.ui import (
    style,
    card,
    checkbox
)

##############################################################################
# Menu Card
##############################################################################


@dataclass
class MenuItem:
    check: Optional[bool] = None
    text: Optional[str] = None
    icon: Optional[str] = None

class Menu(card.ModalCard):
    triggered = Signal(object, object)

    def __init__(
        self: 'Menu',
        items: list[MenuItem],
        color: QColor = style.COLOR_DEFAULT,
        font_size: int = 10,
        font_color: QColor = style.COLOR_TEXT,
        parent = None
        ):
        super().__init__(
            color = color,
            size = card.Size(),
            parent = parent
        )

        # Settings
        self.setObjectName('Card::Menu')

        # Members
        self._items = items
        self._color = color
        self._font_size = font_size
        self._font_color = font_color

        # Initial update
        self._update()

    def _update(self):

        # Create the content widget
        content = QWidget()
        content_layout = QGridLayout(content)
        content_layout.setContentsMargins(
            style.SPACING_SIZE,
            style.SPACING_SIZE,
            style.SPACING_SIZE,
            style.SPACING_SIZE
        )

        # Add the items
        for index, item in self._items:

            # Add the checkbox
            if item.check is not None:
                check = checkbox.Checkbox(
                    checked = item.check,
                    color = self._color,
                    font_size = self._font_size,
                    font_color = self._font_color,
                    parent = self
                )
                content_layout.addWidget(check, index, 0, 1, 1)

            # Add the text
            if item.text is not None:
                label = QLabel(
                    text = item.text,
                    parent = self
                )
                content_layout.addWidget(label, index, 1, 1, 1)
            
            # Add the icon
            if item.icon is not None:
                pass

        # Add the items
        self.set_content(content)