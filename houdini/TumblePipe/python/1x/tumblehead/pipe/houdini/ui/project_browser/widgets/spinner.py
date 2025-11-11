from qtpy.QtCore import Qt, QTimer, QPoint
from qtpy.QtWidgets import QWidget, QLabel, QVBoxLayout, QApplication
from qtpy.QtGui import QPainter, QColor, QFont, QPixmap, QTransform
from hou import qt as hqt
import time


class SpinnerOverlay(QWidget):
    """Semi-transparent overlay with animated spinner for indicating loading states"""

    def __init__(self, parent, message="Loading..."):
        # Create as top-level window for guaranteed visibility
        super().__init__()

        # Store parent and message
        self._target_widget = parent
        self._message = message
        self._rotation = 0
        self._show_time = 0
        self._minimum_display_ms = 300  # Minimum time to show spinner

        # Setup as top-level overlay window
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        # Create layout
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        self.setLayout(layout)

        # Add stretch to center content
        layout.addStretch()

        # Create message label
        self._message_label = QLabel(message)
        self._message_label.setAlignment(Qt.AlignCenter)
        self._message_label.setWordWrap(True)
        self._message_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 14px;
                font-weight: bold;
                background-color: transparent;
            }
        """)
        layout.addWidget(self._message_label, 0, Qt.AlignHCenter)

        # Add stretch to center content
        layout.addStretch()

        # Create timer for animation
        self._timer = QTimer()
        self._timer.timeout.connect(self._rotate_spinner)

        # Initially hidden
        self.hide()

    def _rotate_spinner(self):
        """Rotate the spinner animation"""
        self._rotation = (self._rotation + 15) % 360
        self.update()  # Update the entire overlay

    def paintEvent(self, event):
        """Custom paint for the overlay"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw elegant semi-transparent dark background
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))

        # Draw message text
        painter.setPen(QColor(255, 255, 255))
        font = painter.font()
        font.setPointSize(12)
        font.setBold(True)
        painter.setFont(font)

        # Draw message below spinner
        text_rect = self.rect()
        text_rect.setTop(text_rect.center().y() + 20)
        painter.drawText(text_rect, Qt.AlignCenter, self._message)

        # Draw spinner icon in center
        center = self.rect().center()
        try:
            icon = hqt.Icon("NETVIEW_reload_needsupdate")
            pixmap = icon.pixmap(32, 32)

            # Create transform for rotation around center
            transform = QTransform()
            transform.translate(center.x(), center.y() - 10)  # Slightly above center
            transform.rotate(self._rotation)
            transform.translate(-16, -16)  # Half icon size

            # Draw rotated icon
            painter.setTransform(transform)
            painter.drawPixmap(0, 0, pixmap)
        except Exception as e:
            pass  # Fallback to simple rectangle if icon loading fails
            # Fallback: draw simple rotating white rectangle
            painter.setPen(QColor(255, 255, 255))
            painter.setBrush(QColor(255, 255, 255))
            painter.translate(center.x(), center.y() - 10)
            painter.rotate(self._rotation)
            painter.drawRect(-12, -2, 24, 4)

        painter.end()

    def show_spinner(self, message=None):
        """Show the spinner overlay"""

        if message:
            self._message_label.setText(message)

        # Record show time for minimum display duration
        self._show_time = time.time() * 1000

        # Calculate global position of target widget
        if self._target_widget:
            target_rect = self._target_widget.rect()
            global_pos = self._target_widget.mapToGlobal(QPoint(0, 0))

            # Validate screen position
            screen = QApplication.primaryScreen()
            if screen:
                screen_geometry = screen.geometry()

                # Check if position is within screen bounds
                if not screen_geometry.contains(global_pos):
                    # Fallback to center of screen
                    global_pos = screen_geometry.center()
                    global_pos.setX(global_pos.x() - target_rect.width() // 2)
                    global_pos.setY(global_pos.y() - target_rect.height() // 2)

            # Position overlay over target widget using global coordinates
            self.setGeometry(global_pos.x(), global_pos.y(), target_rect.width(), target_rect.height())

            # Test alternative: Also try as child widget positioned correctly
            self.setParent(self._target_widget)
            self.setGeometry(0, 0, target_rect.width(), target_rect.height())

            # Debug widget hierarchy
            widget = self._target_widget
            level = 0

        # Show overlay with high z-order
        self.show()
        self.raise_()

        # Start animation
        self._timer.start(50)

        # Force multiple UI updates to ensure visibility
        QApplication.processEvents()
        QApplication.processEvents()

        # Disable target widget interactions
        if self._target_widget:
            self._target_widget.setEnabled(False)


    def hide_spinner(self):
        """Hide the spinner overlay with minimum display time"""

        # Calculate time shown
        current_time = time.time() * 1000
        time_shown = current_time - self._show_time

        if time_shown < self._minimum_display_ms:
            # Wait for minimum display time
            remaining_time = self._minimum_display_ms - time_shown
            QTimer.singleShot(int(remaining_time), self._actually_hide)
        else:
            self._actually_hide()

    def _actually_hide(self):
        """Actually hide the spinner"""

        self._timer.stop()
        self.hide()

        # Re-enable target widget interactions
        if self._target_widget:
            self._target_widget.setEnabled(True)

        # Force UI update
        QApplication.processEvents()

    def resizeEvent(self, event):
        """Keep overlay positioned over target widget"""
        if self._target_widget and self.isVisible():
            target_rect = self._target_widget.rect()
            global_pos = self._target_widget.mapToGlobal(QPoint(0, 0))
            self.setGeometry(global_pos.x(), global_pos.y(), target_rect.width(), target_rect.height())
        super().resizeEvent(event)