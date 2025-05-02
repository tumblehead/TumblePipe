from dataclasses import dataclass

from qtpy.QtCore import (
    Qt,
    Signal,
    QMimeData
)
from qtpy.QtGui import (
    QColor,
    QPixmap,
    QDrag
)
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

@dataclass
class ReorderEvent:
    from_index: int
    to_index: int

class ReorderOverlay(card.Card):
    def __init__(
        self: 'ReorderOverlay',
        parent = None
        ):
        super().__init__(
            blocking = False,
            focusable = False,
            parent = parent
        )

        # Settings
        self.setObjectName('Card::StackView::Item::ReorderOverlay')
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )

        # Members
        self._focused = False
        self._dropable = False
        self._dragging = False
    
    def set_focused(self, focused: bool):
        self._focused = focused
        self.update()
    
    def set_dropable(self, dropable: bool):
        self._dropable = dropable
        self.update()

    def set_dragging(self, dragging: bool):
        self._dragging = dragging
        self.update()
    
    def paint(self, painter, shape):

        super().paint(painter, shape)

        # Draw the overlay background
        if self._dragging:
            painter.setBrush(style.COLOR_LOWER)
            painter.drawPath(shape)

        # Draw the overlay dropable state
        if self._dropable and self._focused:
            self.paint_selected(painter, shape)

class ReorderHandle(card.Card):
    def __init__(
        self: 'ReorderHandle',
        color: QColor,
        size: card.Size = card.Size(),
        parent = None
        ):
        super().__init__(
            color = color,
            size = size,
            focusable = True,
            interaction = card.DragInteraction(self),
            parent = parent
        )

        # Settings
        self.setObjectName('Card::StackView::Item::ReorderHandle')

        # Create the content
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Add stretch
        content_layout.addStretch()

        # Add the reorder label
        reorder_label = QLabel('☰')
        reorder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content_layout.addWidget(reorder_label)

        # Add stretch
        content_layout.addStretch()

        # Create the overlay
        self._overlay = ReorderOverlay()

        # Set the content and overlay
        self.set_content(content)
        self.set_overlay(self._overlay)
    
    def set_focused(self, focused: bool):
        self._overlay.set_focused(focused)
    
    def set_dropable(self, dropable: bool):
        self._overlay.set_dropable(dropable)
    
    def set_dragging(self, dragging: bool):
        self._overlay.set_dragging(dragging)

class ReorderItem(card.Card):
    def __init__(
        self: 'ReorderItem',
        item: card.Card = None,
        parent = None
        ):
        super().__init__(parent = parent)

        # Settings
        self.setObjectName('Card::StackView::Item::ReorderItem')

        # Create the content
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Add the item
        content_layout.addWidget(item)

        # Create the overlay
        self._overlay = ReorderOverlay()

        # Set the content and overlay
        self.set_content(content)
        self.set_overlay(self._overlay)
    
    def set_focused(self, focused: bool):
        self._overlay.set_focused(focused)
    
    def set_dropable(self, dropable: bool):
        self._overlay.set_dropable(dropable)
    
    def set_dragging(self, dragging: bool):
        self._overlay.set_dragging(dragging)

@dataclass
class ReorderEvent:
    from_index: int
    to_index: int

class StackViewItem(card.Card):
    reorder = Signal(object)

    def __init__(
        self,
        index: int,
        item: card.Card,
        orderable: bool = False,
        parent = None
        ):
        super().__init__(
            interaction = card.DropInteraction(self),
            parent = parent
        )

        # Settings
        self.setObjectName('Card::StackView::Item')

        # Members
        self._item = item
        self._index = index

        # Create the content layout
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)

        # Add reorder handle if orderable
        self._handle = None
        if orderable:
            self._handle = ReorderHandle(
                color = item.get_color()
            )
            self._handle.dragged.connect(self._on_dragged)
            content_layout.addWidget(self._handle)

        # Create the item content
        self._item = ReorderItem(item)
        content_layout.addWidget(self._item, 1)

        # Set the content
        self.set_content(content)

        # Connect the drop signals
        self.dropped.connect(self._on_dropped)
    
    def set_focused(self, focused: bool):
        self._item.set_focused(focused)
        if self._handle is None: return
        self._handle.set_focused(focused)
    
    def set_dropable(self, dropable: bool):
        self._item.set_dropable(dropable)
        if self._handle is None: return
        self._handle.set_dropable(dropable)

    def set_dragging(self, dragging: bool):
        self._item.set_dragging(dragging)
        if self._handle is None: return
        self._handle.set_dragging(dragging)

    def _on_dragged(self, event):
        event.source = self
        self.dragged.emit(event)
    
    def _on_dropped(self, event):
        if not isinstance(event.source, StackViewItem): return
        match event.tag:
            case card.DropEventTag.Enter:
                self.set_focused(True)
            case card.DropEventTag.Leave:
                self.set_focused(False)
            case card.DropEventTag.Drop:
                self.set_focused(False)
                from_index = event.source._index
                to_index = self._index
                if from_index == to_index: return
                self.reorder.emit(ReorderEvent(
                    from_index = from_index,
                    to_index = to_index
                ))
            case card.DropEventTag.Move: pass

class StackView(card.Card):
    reorder = Signal(object)

    def __init__(
        self: 'StackView',
        items: list[card.Card] = [],
        orderable: bool = False,
        parent = None
        ):
        super().__init__(parent = parent)

        # Settings
        self.setObjectName('Card::StackView')

        # Members
        self._items = items
        self._orderable = orderable
        self._stack = list()

        # Initial update
        self._update()

    def _update(self):

        # Create the content widget
        content = QWidget()
        content_layout = QVBoxLayout(content)
        
        # Add the items
        self._stack.clear()
        for index, item in enumerate(self._items):
            stack_item = StackViewItem(index, item, self._orderable)
            stack_item.dragged.connect(self._on_item_dragged)
            stack_item.reorder.connect(self._on_item_reorder)
            content_layout.addWidget(stack_item)
            self._stack.append(stack_item)
        
        # Add a spacer item
        content_layout.addStretch()

        # Set the content
        self.set_content(content)

        # Refresh
        self.update()
    
    def set_items(self, items):

        # Disconnect the clicked signal
        for item in self._items:
            try:
                item.clicked.disconnect(self.clicked.emit)
                item.dragged.disconnect(self.dragged.emit)
            except: pass
        
        # Clear the items
        for item in self._items:
            item.deleteLater()
        self._items.clear()

        # Update the items
        self._items = list(items)
        self._update()
        
        # Connect the clicked signal
        for item in items:
            item.clicked.connect(self.clicked.emit)
            item.dragged.connect(self.dragged.emit)
    
    def get_items(self):
        return self._items

    def _on_item_dragged(self, event):

        def _set_dropable(dropable: bool):
            for stack_item in self._stack:
                stack_item.set_dropable(dropable)

        # Setup dragging
        drag = QDrag(event.source)
        mime = QMimeData()
        drag.setMimeData(mime)

        # Draw the pixmap
        pixmap = QPixmap(event.source.size())
        pixmap.fill(Qt.transparent)
        event.source.render(pixmap)
        drag.setPixmap(pixmap)

        # Find the offset
        offset = event.source.mapFromGlobal(event.location)
        drag.setHotSpot(offset)

        # Start the drag
        _set_dropable(True)
        event.source.set_dragging(True)
        drag.exec_(Qt.MoveAction)
        event.source.set_dragging(False)
        _set_dropable(False)
    
    def _on_item_reorder(self, event):
        self.reorder.emit(event)