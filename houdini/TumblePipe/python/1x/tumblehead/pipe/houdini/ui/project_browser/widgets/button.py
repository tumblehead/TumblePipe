from qtpy.QtCore import Qt, Signal, QEvent
from qtpy import QtWidgets


class ButtonSurface(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

    def payload(self):
        raise NotImplementedError()

    def overwrite(self, payload):
        raise NotImplementedError()


class State:
    Hover = "hover"
    Pressed = "pressed"
    Selected = "selected"
    Overwritten = "overwritten"


class ButtonHost(QtWidgets.QWidget):
    clicked = Signal(object)
    state_changed = Signal(str, object, object)

    def __init__(self, surface, parent=None):
        super().__init__(parent)

        # Check if the surface is a button surface
        if not isinstance(surface, ButtonSurface):
            raise ValueError("Surface must be a ButtonSurface")

        # Members
        self._surface = surface

        # Set the layout
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        # Add the surface
        layout.addWidget(surface)

        # Add the mouse press event
        self.setMouseTracking(True)
        self.setProperty(State.Hover, False)
        self.setProperty(State.Pressed, False)
        self.setProperty(State.Selected, False)

        # Set hover highlighting
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "ButtonHost[hover=true][pressed=false] {"
            "   background-color: #474747;"
            "}"
            "ButtonHost[selected=true][hover=false] {"
            "   background-color: #5e4a8a;"
            "}"
            "ButtonHost[selected=true][overwritten=true][hover=false] {"
            "   background-color: #b01c3c;"
            "}"
            "ButtonHost[selected=true][hover=true] {"
            "   background-color: #58482a;"
            "}"
        )

    def surface(self):
        return self._surface

    def set_state(self, state, next_value):
        prev_value = self.get_state(state)
        self.setProperty(state, next_value)
        self.setStyle(self.style())
        self.state_changed.emit(state, prev_value, next_value)

    def get_state(self, state):
        return self.property(state)

    def set_states(self, **states):
        for state, value in states.items():
            self.setProperty(state, value)
        self.setStyle(self.style())

    def get_states(self, *states):
        return {state: self.property(state) for state in states}

    def mousePressEvent(self, event):
        if event.type() != QEvent.Type.MouseButtonPress:
            return super().mousePressEvent(event)
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        self.set_state(State.Pressed, True)
        return super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.type() != QEvent.Type.MouseButtonRelease:
            return super().mouseReleaseEvent(event)
        if not self.get_state(State.Pressed):
            return super().mouseReleaseEvent(event)
        self.clicked.emit(self._surface.payload())
        self.set_state(State.Pressed, False)
        return super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self.set_state(State.Hover, True)
        return super().enterEvent(event)

    def leaveEvent(self, event):
        self.set_state(State.Hover, False)
        return super().leaveEvent(event)

    def deleteLater(self):
        self._surface.deleteLater()
        return super().deleteLater()

    def overwrite(self, payload):
        overwritten = self._surface.overwrite(payload)
        self.set_state(State.Overwritten, overwritten)
        return overwritten