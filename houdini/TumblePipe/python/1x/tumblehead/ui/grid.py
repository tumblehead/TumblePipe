from qtpy.QtWidgets import QWidget

from tumblehead.ui import flow, card

from importlib import reload
reload(flow)
reload(card)

class GridView(card.Card):
    def __init__(
        self: 'GridView',
        items: list[card.Card] = [],
        parent = None
        ):
        super().__init__(parent = parent)

        # Settings
        self.setObjectName('Card::GridView')

        # Members
        self._items = items

        # Initial update
        self._update()
    
    def _update(self):

        # Create the content widget
        content = QWidget()
        content_layout = flow.FlowLayout(content)
        
        # Add the items
        for item in self._items:
            content_layout.addWidget(item)
        
        # Set the content
        self.set_content(content)
        
        # Resize
        self.update()

    def get_item(self, index):
        if index < 0: return None
        if index >= len(self._items): return None
        return self._items[index]
    
    def set_items(self, items):

        # Disconnect the signals
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

        # Connect the signals
        for item in items:
            item.clicked.connect(self.clicked.emit)
            item.dragged.connect(self.dragged.emit)
    
    def get_items(self):
        return self._items