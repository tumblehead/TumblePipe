from typing import Optional

from qtpy.QtCore import (
    Qt,
    Signal
)
from qtpy.QtGui import (
    QColor
)
from qtpy.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QLabel
)

from tumblehead.ui import (
    card,
    style
)

##############################################################################
# Button Card
##############################################################################

class Button(card.Card):
    triggered = Signal(object)

    def __init__(
        self: 'Button',
        text: Optional[str] = None,
        size: card.Size = card.Size(),
        radius: int = style.RADIUS_SIZE,
        border: Optional[card.Border] = None,
        color: QColor = style.COLOR_DEFAULT,
        font_size: int = 10,
        font_color: QColor = style.COLOR_TEXT,
        tooltip: Optional[str] = None,
        parent = None
        ):
        super().__init__(
            size = size,
            radius = radius,
            border = border,
            color = color,
            focusable = True,
            tooltip = tooltip,
            interaction = card.ClickInteraction(self),
            parent = parent
        )

        # Settings
        self.setObjectName('Card::Button')

        # Members
        self._text = text
        self._font_size = font_size
        self._font_color = font_color

        # Create the content
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(
            style.SPACING_SIZE,
            style.SPACING_SIZE,
            style.SPACING_SIZE,
            style.SPACING_SIZE
        )

        # Create the text label
        self._text_label = QLabel(self)
        self._text_label.setAlignment(Qt.AlignCenter)
        content_layout.addWidget(self._text_label)

        # Set the content
        self.set_content(content)

        # Connect the clicked signal
        self.clicked.connect(self._on_clicked)

        # Initial update
        self._update()
    
    def _update(self):

        # Update the text label
        self._text_label.setText('' if self._text is None else self._text)
        self._text_label.setStyleSheet(
            f'color: {self._font_color.name()}; '
        )
        font = self._text_label.font()
        font.setPointSize(self._font_size)
        self._text_label.setFont(font)

        # Update the card
        self.update()
    
    @property
    def font_color(self):
        return self._font_color
    
    def set_text(self, text):
        self._text = text
        self._update()
    
    def set_font_size(self, font_size):
        self._font_size = font_size
        self._update()
    
    def set_font_color(self, font_color):
        self._font_color = font_color
        self._update()
    
    def _on_clicked(self, event):
        if event.button != Qt.LeftButton: return
        self.triggered.emit(self)

##############################################################################
# Toggle Button Card
##############################################################################

class ToggleButton(Button):
    toggled = Signal(object, bool)

    def __init__(
        self: 'ToggleButton',
        text: Optional[str] = None,
        size: card.Size = card.Size(),
        radius: int = style.RADIUS_SIZE,
        border: Optional[card.Border] = None,
        color: QColor = style.COLOR_DEFAULT,
        toggle_color: QColor = style.COLOR_ACTIVE,
        font_size: int = 12,
        font_color: QColor = QColor(255, 255, 255),
        tooltip: Optional[str] = None,
        parent = None
        ):
        super().__init__(
            text = text,
            size = size,
            radius = radius,
            border = border,
            color = color,
            font_size = font_size,
            font_color = font_color,
            tooltip = tooltip,
            parent = parent
        )

        # Settings
        self.setObjectName('Card::ToggleButton')

        # Members
        self._toggle_color = toggle_color
        self._toggled = False
        self._stored = None

        # Connect the triggered signal
        self.triggered.connect(self._on_triggered)

    @property
    def background_color(self):
        if self._toggled: return self._toggle_color
        return super().background_color

    def set_toggled(self, toggled):
        self._toggled = toggled
        self.toggled.emit(self, toggled)
        self.update()
    
    def is_toggled(self):
        return self._toggled

    def _on_triggered(self, _button):
        self.set_toggled(not self._toggled)
    
    def _on_link_toggled(self, _other, toggled):
        self._toggled = toggled
        self.update()
    
    def link(self, other: 'ToggleButton'):
        if self._stored is not None: return
        self._stored = other
        self.set_disabled(True)
        self.set_toggled(other.is_toggled())
        other.toggled.connect(self._on_link_toggled)
    
    def unlink(self):
        if self._stored is None: return
        other = self._stored
        self._stored = None
        other.toggled.disconnect(self._on_link_toggled)
        self.set_disabled(False)