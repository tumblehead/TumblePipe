from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from qtpy.QtCore import (
    Qt,
    Signal,
    Property
)
from qtpy.QtGui import (
    QPainter,
    QPainterPath,
    QPixmap,
    QCursor,
    QColor,
    QBrush,
    QPen
)
from qtpy.QtWidgets import (
    QWidget,
    QDialog,
    QStackedLayout,
    QHBoxLayout,
    QSizePolicy,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem
)

from tumblehead.ui import style

from importlib import reload
reload(style)

##############################################################################
# Card Sizing
##############################################################################

def dimension_size(count: int, spacing: int = 0) -> int:
    if count < 1: return 0
    if count == 1: return style.UNIT_SIZE
    return (style.UNIT_SIZE * count) + (spacing * (count - 1))

@dataclass(frozen = True)
class Dimension:
    value: int = 2
    expanding: bool = True

@dataclass(frozen = True)
class Size:
    width: Dimension = field(default_factory = Dimension)
    height: Dimension = field(default_factory = Dimension)
    spacing: int = 0

    def apply(self, card: QWidget):
        width = dimension_size(self.width.value, self.spacing)
        height = dimension_size(self.height.value, self.spacing)
        card.setMinimumSize(width, height)
        if not self.width.expanding: card.setMaximumWidth(width)
        if not self.height.expanding: card.setMaximumHeight(height)
        card.setSizePolicy(
            QSizePolicy.Expanding if self.width.expanding else QSizePolicy.Fixed,
            QSizePolicy.Expanding if self.height.expanding else QSizePolicy.Fixed
        )

##############################################################################
# Data Structures
##############################################################################

@dataclass
class Border:
    width: int
    color: QColor

class Interaction:
    def on_press(self, button):
        raise NotImplementedError()
    
    def on_release(self, button):
        raise NotImplementedError()
    
    def on_move(self):
        raise NotImplementedError()
    
    def on_enter(self):
        raise NotImplementedError()
    
    def on_leave(self):
        raise NotImplementedError()

    def on_drag_enter(self, source):
        raise NotImplementedError()
    
    def on_drag_leave(self):
        raise NotImplementedError()
    
    def on_drag_move(self):
        raise NotImplementedError()

    def on_drag_drop(self):
        raise NotImplementedError()

@dataclass
class ClickEvent:
    source: QWidget
    button: int
    location: object

class ClickInteraction(Interaction):
    def __init__(
        self: 'ClickInteraction',
        card: 'Card'
        ):

        # Members
        self.__card = card
        self.__active = False
        self.__pressed = {
            Qt.LeftButton: False,
            Qt.RightButton: False
        }
    
    def is_pressed(self, button):
        return self.__pressed[button]
    
    def on_press(self, button):
        if not self.__active: return
        if button not in self.__pressed: return
        self.__pressed[button] = True

    def on_release(self, button):
        if not self.__active: return
        if button not in self.__pressed: return
        if not self.__pressed[button]: return
        self.__pressed[button] = False
        self.__card.clicked.emit(ClickEvent(
            self.__card,
            button,
            QCursor.pos()
        ))

    def on_move(self): pass

    def on_enter(self):
        self.__card.setCursor(Qt.PointingHandCursor)
        self.__active = True
        self.__pressed = {
            Qt.LeftButton: False,
            Qt.RightButton: False
        }

    def on_leave(self):
        self.__card.setCursor(Qt.ArrowCursor)
        self.__active = False
        self.__pressed = {
            Qt.LeftButton: False,
            Qt.RightButton: False
        }
    
    def on_drag_enter(self, _source):
        pass

    def on_drag_leave(self):
        pass

    def on_drag_move(self):
        pass

    def on_drag_drop(self):
        pass

class DragEventTag(Enum):
    Start = 'start'
    Move = 'move'
    End = 'end'

@dataclass
class DragEvent:
    tag: DragEventTag
    source: QWidget
    button: int
    location: object

class DragInteraction(Interaction):
    def __init__(
        self: 'DragInteraction',
        card: 'Card'
        ):

        # Members
        self.__card = card
        self.__active = False
        self.__dragging = {
            Qt.LeftButton: False,
            Qt.RightButton: False
        }
    
    def is_dragging(self, button):
        return self.__dragging[button]
    
    def on_press(self, button):
        if not self.__active: return
        if button not in self.__dragging: return
        self.__dragging[button] = True
        self.__card.setCursor(Qt.ClosedHandCursor)
        self.__card.dragged.emit(DragEvent(
            DragEventTag.Start,
            self.__card,
            button,
            QCursor.pos()
        ))

    def on_release(self, button):
        if not self.__active: return
        if button not in self.__dragging: return
        if not self.__dragging[button]: return
        self.__dragging[button] = False
        self.__card.setCursor(Qt.OpenHandCursor)
        self.__card.dragged.emit(DragEvent(
            DragEventTag.End,
            self.__card,
            button,
            QCursor.pos()
        ))

    def on_move(self):
        if not self.__active: return
        if not self.__dragging[Qt.LeftButton]: return
        location = QCursor.pos()
        self.__card.dragged.emit(DragEvent(
            DragEventTag.Move,
            self.__card,
            Qt.LeftButton,
            location
        ))
        if not self.__dragging[Qt.RightButton]: return
        self.__card.dragged.emit(DragEvent(
            DragEventTag.Move,
            self.__card,
            Qt.RightButton,
            location
        ))

    def on_enter(self):
        self.__card.setCursor(Qt.OpenHandCursor)
        self.__active = True
        self.__dragging = {
            Qt.LeftButton: False,
            Qt.RightButton: False
        }

    def on_leave(self):
        self.__card.setCursor(Qt.ArrowCursor)
        self.__active = False
        self.__dragging = {
            Qt.LeftButton: False,
            Qt.RightButton: False
        }
    
    def on_drag_enter(self, _source):
        pass

    def on_drag_leave(self):
        pass

    def on_drag_move(self):
        pass

    def on_drag_drop(self):
        pass

class DropEventTag(Enum):
    Enter = 'enter'
    Move = 'move'
    Drop = 'drop'
    Leave = 'leave'

@dataclass
class DropEvent:
    tag: DragEventTag
    source: QWidget
    location: object

class DropInteraction(Interaction):
    def __init__(
        self: 'DropInteraction',
        card: 'Card'
        ):

        # Members
        self.__card = card
        self.__source = None
    
    def on_press(self, _button):
        pass

    def on_release(self, _button):
        pass

    def on_move(self):
        pass

    def on_enter(self):
        pass

    def on_leave(self):
        pass
    
    def on_drag_enter(self, source):
        self.__source = source
        self.__card.dropped.emit(DropEvent(
            DropEventTag.Enter,
            self.__source,
            QCursor.pos()
        ))

    def on_drag_leave(self):
        if self.__source is None: return
        self.__card.dropped.emit(DropEvent(
            DropEventTag.Leave,
            self.__source,
            QCursor.pos()
        ))
        self.__source = None

    def on_drag_move(self):
        if self.__source is None: return
        self.__card.dropped.emit(DropEvent(
            DropEventTag.Move,
            self.__source,
            QCursor.pos()
        ))

    def on_drag_drop(self):
        if self.__source is None: return
        self.__card.dropped.emit(DropEvent(
            DropEventTag.Drop,
            self.__source,
            QCursor.pos()
        ))
        self.__source = None

@dataclass
class FocusEvent:
    source: QWidget
    focused: bool

class Card(QWidget):
    focused = Signal(object)
    clicked = Signal(object)
    dragged = Signal(object)
    dropped = Signal(object)

    def __init__(
        self: 'Card',
        size: Size = Size(),
        radius: int = style.RADIUS_SIZE,
        border: Optional[Border] = None,
        color: QColor = style.COLOR_NONE,
        tooltip: Optional[str] = None,
        focusable: bool = False,
        selectable: bool = False,
        interaction: Optional[Interaction] = None,
        blocking: bool = True,
        enabled: bool = True,
        parent: Optional[QWidget] = None
        ):
        super().__init__(parent)

        # Settings
        self.setObjectName('Card')
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAcceptDrops(isinstance(interaction, DropInteraction))
        self.setWindowFlags(
            self.windowFlags() |
            Qt.NoDropShadowWindowHint
        )
        size.apply(self)

        # Members
        self.__size = size
        self.__radius = radius
        self.__border = border
        self.__color = color
        self.__tooltip = tooltip
        self.__focusable = focusable
        self.__selectable = selectable
        self.__interaction = interaction
        self.__blocking = blocking
        self.__enabled = enabled
        self.__focused = False
        self.__selected = False

        # Set the tooltip
        if self.__tooltip is not None:
            self.setToolTip(self.__tooltip)

        # Create the main layout
        self.__layout = QStackedLayout(self)
        self.__layout.setContentsMargins(0, 0, 0, 0)
        self.__layout.setStackingMode(QStackedLayout.StackAll)

        # Create the initial content
        self.__content = QWidget()
        self.__content.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.__layout.addWidget(self.__content)

        # Create the content overlay
        self.__overlay = QWidget()
        self.__overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.__layout.addWidget(self.__overlay)
    
    @Property(bool)
    def is_enabled(self):
        return self.__enabled
    
    @Property(bool)
    def is_disabled(self):
        return not self.__enabled
    
    @Property(bool)
    def is_blocking(self):
        return self.__blocking
    
    @Property(bool)
    def is_focused(self):
        return self.__focusable and self.__focused
    
    @Property(bool)
    def is_selected(self):
        return self.__selectable and self.__selected

    @Property(bool)
    def is_pressed(self):
        if not isinstance(self.__interaction, ClickInteraction): return False
        return self.__interaction.is_pressed(Qt.LeftButton)
    
    @Property(bool)
    def is_dragging(self):
        if not isinstance(self.__interaction, DragInteraction): return False
        return self.__interaction.is_dragging(Qt.LeftButton)
    
    @property
    def background_color(self):
        if self.is_disabled:
            value = self.__color.value()
            return QColor(value, value, value)
        if self.is_pressed:
            return self.__color.darker(150)
        return self.__color

    @property
    def background_style(self):
        if self.is_disabled: return Qt.Dense4Pattern
        return Qt.SolidPattern

    @property
    def background(self):
        color = self.background_color
        style = self.background_style
        return QBrush(color, style)
    
    @property
    def border_color(self):
        if self.__border is None: return None
        if self.is_disabled:
            value = self.__border.color.value()
            return QColor(value, value, value)
        return self.__border.color

    @property
    def border_style(self):
        if self.__border is None: return None
        if self.is_disabled:
            return Qt.Dense4Pattern
        return Qt.SolidLine

    @property
    def border(self):
        if self.__border is None: return None
        color = self.border_color
        style = self.border_style
        return QPen(color, self.__border.width, style)
    
    def get_size(self):
        return self.__size
    
    def set_size(self, size):
        if self.__size == size: return
        self.__size = size
        self.__size.apply(self)
        self.update()

    def get_radius(self):
        return self.__radius
    
    def set_radius(self, radius):
        if self.__radius == radius: return
        self.__radius = radius
        self.update()
    
    def set_enabled(self, enabled):
        self.__enabled = enabled
        self.update()
    
    def set_disabled(self, disabled):
        self.__enabled = not disabled
        self.update()
    
    def get_color(self):
        return QColor(self.__color)
    
    def set_color(self, color):
        self.__color = color
        self.update()
    
    def get_content(self):
        return self.__content
    
    def set_content(self, content):
        if self.__content == content: return
        self.__layout.replaceWidget(self.__content, content)
        self.__content.deleteLater()
        self.__content = content
        self.__overlay.resize(self.__content.size())
        self.update()
    
    def get_overlay(self):
        return self.__overlay

    def set_overlay(self, overlay):
        if self.__overlay == overlay: return
        self.__layout.replaceWidget(self.__overlay, overlay)
        self.__overlay.deleteLater()
        self.__overlay = overlay
        self.__overlay.resize(self.__content.size())
        self.update()

    def get_tooltip(self):
        return self.__tooltip
    
    def set_tooltip(self, tooltip):
        self.__tooltip = tooltip
        self.setToolTip(self.__tooltip)
    
    def mousePressEvent(self, event):
        if not self.__enabled: return
        if self.__interaction is not None:
            if self.__blocking: event.accept()
            self.__interaction.on_press(event.button())
            self.update()
    
    def mouseReleaseEvent(self, event):
        if not self.__enabled: return
        if self.__interaction is not None:
            if self.__blocking: event.accept()
            self.__interaction.on_release(event.button())
            self.update()
    
    def enterEvent(self, event):
        if not self.__enabled: return
        if self.__focusable:
            self.__focused = True
            self.focused.emit(FocusEvent(self, True))
        if self.__interaction is not None:
            if self.__blocking: event.accept()
            self.__interaction.on_enter()
            self.update()
    
    def mouseMoveEvent(self, event):
        if not self.__enabled: return
        if self.__interaction is not None:
            if self.__blocking: event.accept()
            self.__interaction.on_move()
            self.update()

    def leaveEvent(self, event):
        if not self.__enabled: return
        if self.__focusable:
            self.__focused = False
            self.focused.emit(FocusEvent(self, False))
        if self.__interaction is not None:
            if self.__blocking: event.accept()
            self.__interaction.on_leave()
            self.update()
    
    def dragEnterEvent(self, event):
        if not self.__enabled: return
        if self.__interaction is not None:
            if self.__blocking: event.accept()
            self.__interaction.on_drag_enter(event.source())
            self.update()
    
    def dragLeaveEvent(self, event):
        if not self.__enabled: return
        if self.__interaction is not None:
            if self.__blocking: event.accept()
            self.__interaction.on_drag_leave()
            self.update()
        
    def dragMoveEvent(self, event):
        if not self.__enabled: return
        if self.__interaction is not None:
            if self.__blocking: event.accept()
            self.__interaction.on_drag_move()
            self.update()
    
    def dropEvent(self, event):
        if not self.__enabled: return
        if self.__interaction is not None:
            if self.__blocking: event.accept()
            self.__interaction.on_drag_drop()
            self.update()
    
    def set_selected(self, selected):
        if not self.__selectable: return
        self.__selected = selected
        self.update()
    
    def paint_background(self, painter, shape):
        painter.setBrush(self.background)
        painter.drawPath(shape)
    
    def paint_border(self, painter, shape):
        border_pen = self.border
        if border_pen is None: return
        painter.setPen(border_pen)
        painter.drawPath(shape)
    
    def paint_selected(self, painter, shape):
        border_width = 5
        border_color = QColor(255, 255, 255, 50)
        border_pen = QPen(border_color, border_width)
        border_pen.setStyle(Qt.DotLine)
        painter.setPen(border_pen)
        painter.drawPath(shape)
    
    def paint_focused(self, painter, shape):
        focus_color = QColor(255, 255, 255, 25)
        painter.setBrush(QBrush(focus_color))
        painter.drawPath(shape)
    
    def paint_pressed(self, _painter, _shape):
        pass

    def paint_dragging(self, _painter, _shape):
        pass

    def paint_disabled(self, _painter, _shape):
        pass

    def paint(self, painter, shape):

        # Draw the background
        painter.save()
        self.paint_background(painter, shape)
        painter.restore()

        # Draw the border
        if self.__border is not None:
            painter.save()
            self.paint_border(painter, shape)
            painter.restore()

        # Draw on selected
        if self.is_selected:
            painter.save()
            self.paint_selected(painter, shape)
            painter.restore()

        # Draw on focused
        if self.is_focused:
            painter.save()
            self.paint_focused(painter, shape)
            painter.restore()
        
        # Draw on pressed
        if self.is_enabled and self.is_pressed:
            painter.save()
            self.paint_pressed(painter, shape)
            painter.restore()
        
        # Draw on dragging
        if self.is_enabled and self.is_dragging:
            painter.save()
            self.paint_dragging(painter, shape)
            painter.restore()
        
        # Draw on disabled
        if self.is_disabled:
            painter.save()
            self.paint_disabled(painter, shape)
            painter.restore()
    
    def get_shape(self):
        shape = QPainterPath()
        shape.addRoundedRect(
            self.rect(),
            self.__radius,
            self.__radius
        )
        return shape
    
    def paintEvent(self, event):

        # Accept the event
        event.accept()

        # Create the painter
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.HighQualityAntialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(Qt.NoPen)

        # Define the painter shape
        shape = self.get_shape()

        # Paint the card
        self.paint(painter, shape)

class _DropShadow(QWidget):
    def __init__(self,
        content,
        parent = None
        ):
        super().__init__(parent)

        # Settings
        self.setObjectName('ModalCard::_DropShadow')
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # Members
        self._content = content
    
    def paintEvent(self, event):

        # Prepare the buffer
        shape = self._content.get_shape()
        shadow_rect = self.rect()
        shadow_pixmap = QPixmap(
            shadow_rect.width(),
            shadow_rect.height()
        )
        shadow_pixmap.fill(Qt.transparent)

        # Paint the drop shadow
        shadow_painter = QPainter(shadow_pixmap)
        shadow_painter.setRenderHint(QPainter.Antialiasing, True)
        shadow_painter.setRenderHint(QPainter.HighQualityAntialiasing, True)
        shadow_painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        shadow_painter.setRenderHint(QPainter.TextAntialiasing, True)
        shadow_painter.setPen(Qt.NoPen)
        shadow_painter.setBrush(style.COLOR_BLACK)
        shadow_painter.drawPath(shape)

        # Blur the drop shadow
        shadow_item = QGraphicsPixmapItem(shadow_pixmap)
        shadow_blur = QGraphicsBlurEffect()
        shadow_blur.setBlurHints(QGraphicsBlurEffect.QualityHint)
        shadow_blur.setBlurRadius(style.SHADOW_DISTANCE)
        shadow_item.setGraphicsEffect(shadow_blur)

        # Draw the drop shadow
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.HighQualityAntialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(Qt.NoBrush)
        painter.translate(
            style.SHADOW_DIRECTION[0] * style.SHADOW_DISTANCE,
            style.SHADOW_DIRECTION[1] * style.SHADOW_DISTANCE
        )
        painter.drawPixmap(
            shadow_rect,
            shadow_pixmap
        )

        # Render the content card
        super().paintEvent(event)

class ModalCard(QDialog):
    def __init__(
        self: 'ModalCard',
        size: Size = Size(),
        radius: int = style.RADIUS_SIZE,
        border: Optional[Border] = None,
        color: QColor = style.COLOR_NONE,
        tooltip: Optional[str] = None,
        focusable: bool = False,
        selectable: bool = False,
        interaction: Optional[Interaction] = None,
        parent: Optional[QWidget] = None
        ):
        super().__init__(parent)

        # Settings
        self.setObjectName('Card')
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlags(
            Qt.NoDropShadowWindowHint |
            Qt.FramelessWindowHint |
            Qt.Popup
        )

        # Create the main layout
        self.__layout = QStackedLayout(self)
        self.__layout.setContentsMargins(0, 0, 0, 0)
        self.__layout.setStackingMode(QStackedLayout.StackAll)

        # Create the main content card
        self.__content = Card(
            size = size,
            radius = radius,
            border = border,
            color = color,
            tooltip = tooltip,
            focusable = focusable,
            selectable = selectable,
            interaction = interaction,
            parent = self
        )

        # Create the drop shadow
        self.__shadow = _DropShadow(
            content = self.__content
        )

        # Set the layout
        self.__layout.addWidget(self.__shadow)
        self.__layout.addWidget(self.__content)
        self.setLayout(self.__layout)
    
    def set_content(self, content):
        if self.__content.get_content() == content: return
        self.__content.set_content(content)
    
    def execute(self, location):
        self.move(location)
        self.setFocus()
        self.exec_()
    
    def focusOutEvent(self, event):
        self.close()
    
    def _update_shadow_geometry(self):
        rect = self.__content.rect()
        self.__shadow.setGeometry(rect.adjusted(
            0, 0,
            style.SHADOW_DISTANCE * 2,
            style.SHADOW_DISTANCE * 2
        ))
        self.__shadow.update()
    
    def moveEvent(self, event):
        self._update_shadow_geometry()
        super().moveEvent(event)