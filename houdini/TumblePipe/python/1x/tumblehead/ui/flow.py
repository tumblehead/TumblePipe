from qtpy.QtCore import Qt, QRect, QSize, QPoint
from qtpy.QtWidgets import QLayout, QSizePolicy

class FlowLayout(QLayout):
    def __init__(self, parent=None):
        super().__init__(parent)

        # Members
        self.items = []
        self.dirty = False
        self.cached_size = QSize()

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self.items.append(item)
        self.dirty = True

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        if index < 0: return None
        if index >= len(self.items): return None
        return self.items[index]

    def takeAt(self, index):
        if index < 0: return None
        if index >= len(self.items): return None
        return self.items.pop(index)

    def expandingDirections(self):
        return Qt.Orientation(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, _width):
        width = self.geometry().width() - 2 * self.contentsMargins().top()
        _, height = self.doLayout(QRect(0, 0, width, 0), True)
        return height

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.doLayout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        width = self.geometry().width() - 2 * self.contentsMargins().top()
        minWidth, minHeight = self.doLayout(QRect(0, 0, width, 0), True)

        size = QSize(minWidth, minHeight)
        margin, _, _, _ = self.getContentsMargins()

        size += QSize(2 * margin, 2 * margin)
        return size

    def doLayout(self, rect, testOnly):

        def _get_spacing(widget):
            spacing = self.spacing()
            try:
                style = widget.style()
                horizontal = style.layoutSpacing(QSizePolicy.ControlType.PushButton, QSizePolicy.ControlType.PushButton, Qt.Orientation.Horizontal)
                vertical = style.layoutSpacing(QSizePolicy.ControlType.PushButton, QSizePolicy.ControlType.PushButton, Qt.Orientation.Vertical)
                return spacing + horizontal, spacing + vertical
            except: return spacing, spacing

        # Initialize the x and y positions
        global_position = rect.topLeft()
        x, y = 0, 0

        # Find the maximum item size
        maxSize = QSize()
        for item in self.items:
            maxSize = maxSize.expandedTo(item.sizeHint())

        # Layout the items
        lineHeight = maxSize.height()
        maxLineWidth = 0
        for item in self.items:

            # Get the widget
            wid = item.widget()
            itemSize = item.sizeHint()
            itemWidth = itemSize.width()

            # Find the spacing
            spaceX, spaceY = _get_spacing(wid)
            
            # Find the next position on the x-axis
            nextX = x + itemWidth + spaceX
            
            # Check if the item will fit on the current line
            if nextX - spaceX > rect.right():
                maxLineWidth = max(maxLineWidth, x - global_position.x())
                x = 0
                y += lineHeight + spaceY
                nextX = x + itemWidth + spaceX

            # Set the geometry of the item
            if not testOnly:
                item.setGeometry(QRect(
                    global_position + QPoint(x, y),
                    itemSize
                ))

            # Update the x position
            x = nextX

        # Return the height of the layout
        return maxLineWidth, y + lineHeight