from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QFont, QColor
from qtpy.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSizePolicy,
    QLabel
)

from tumblehead.ui import (
    card,
    style
)

from importlib import reload
reload(card)
reload(style)

EXPAND_ICON = '▶'
COLLAPSE_ICON = '▼'

class Header(card.Card):
    toggled = Signal(bool)

    def __init__(
        self,
        title,
        color = style.COLOR_NONE,
        collapsible = False,
        collapsed = False,
        parent = None
        ):
        super().__init__(
            color = color,
            interaction = (
                card.ClickInteraction(self)
                if collapsible else None
            ),
            parent = parent
        )

        # Members
        self.title = title
        self.collapsible = collapsible
        self.collapsed = collapsed

        # Set size policy
        self.setObjectName('Card::Section::Header')
        self.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed
        )

        # Create the content
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)

        # Create the header icon
        self.icon = QLabel(self)
        self.icon.setText(
            (EXPAND_ICON if collapsed else COLLAPSE_ICON)
            if collapsible else '⎯'
        )
        self.icon.setStyleSheet(f'color: {style.COLOR_MID.name()};')
        content_layout.addWidget(self.icon)

        # Create the header title
        font = QFont()
        font.setBold(True)
        self.title_label = QLabel(title, self)
        self.title_label.setFont(font)
        content_layout.addWidget(self.title_label)

        # Add a line separator
        line = QLabel(self)
        line.setFixedHeight(1)
        line.setStyleSheet(f'background-color: {style.COLOR_MID.name()};')
        content_layout.addWidget(line, 1)

        # Set the content
        self.set_content(content)

        # Handle mouse click
        self.clicked.connect(lambda *_args: self.toggle())

    def is_collapsed(self):
        if not self.collapsible: return False
        return self.collapsed

    def get_title(self):
        return self.title
    
    def set_title(self, title):
        self.title = title
        self.title_label.setText(title)

    def set_collapsed(self, collapsed):
        if not self.collapsible: return
        self.collapsed = collapsed
        self.icon.setText(EXPAND_ICON if self.collapsed else COLLAPSE_ICON)
        self.toggled.emit(self.collapsed)

    def toggle(self):
        self.set_collapsed(not self.collapsed)

class Section(card.Card):
    def __init__(
        self,
        title,
        collapsible = False,
        collapsed = False,
        parent = None
        ):
        super().__init__(
            parent = parent
        )

        # Settings
        self.setObjectName('Card::Section')

        # Create the layout
        content = QWidget()
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(0, 0, 0, 0)

        # Create the header
        self._header = Header(
            title,
            collapsible = collapsible,
            collapsed = collapsed
        )
        self._header.toggled.connect(self._toggled)
        self._layout.addWidget(self._header)

        # Create the section content
        self._content = QWidget()
        self._content.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._layout.addWidget(self._content)

        # Set the content
        super().set_content(content)
    
    def is_collapsed(self):
        return self._header.is_collapsed()

    def set_title(self, title):
        self._header.set_title(title)
    
    def get_title(self):
        return self._header.get_title()
    
    def set_content(self, content):
        self._layout.replaceWidget(self._content, content)
        self._content.deleteLater()
        self._content = content
        self._content.setVisible(not self.is_collapsed())
    
    def get_content(self):
        return self._content
    
    def set_collapsed(self, collapsed):
        self._header.set_collapsed(collapsed)
    
    def toggle(self):
        self._header.toggle()
    
    def _toggled(self, collapsed):
        self._content.setVisible(not collapsed)