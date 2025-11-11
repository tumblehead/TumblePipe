from qtpy.QtCore import QObject, QPropertyAnimation, QEasingCurve, QTimer, Qt, QPointF
from qtpy.QtWidgets import QGraphicsOpacityEffect, QWidget, QApplication
from qtpy.QtGui import QPalette, QColor, QPainterPath
import math


class BorderPathCalculator:
    """Calculate coordinates for rounded rectangle border path"""

    @staticmethod
    def calculate_border_path(width, height, border_radius=5):
        """Calculate path coordinates that follow panel border shape"""
        points = []

        # Top edge (left to right, excluding corners)
        for x in range(border_radius, width - border_radius + 1, 2):
            points.append((x, 0))

        # Top-right corner (arc)
        corner_points = BorderPathCalculator._calculate_corner_arc(
            width - border_radius, border_radius, border_radius, 0, 90
        )
        points.extend(corner_points)

        # Right edge (top to bottom)
        for y in range(border_radius, height - border_radius + 1, 2):
            points.append((width, y))

        # Bottom-right corner (arc)
        corner_points = BorderPathCalculator._calculate_corner_arc(
            width - border_radius, height - border_radius, border_radius, 90, 180
        )
        points.extend(corner_points)

        # Bottom edge (right to left)
        for x in range(width - border_radius, border_radius - 1, -2):
            points.append((x, height))

        # Bottom-left corner (arc)
        corner_points = BorderPathCalculator._calculate_corner_arc(
            border_radius, height - border_radius, border_radius, 180, 270
        )
        points.extend(corner_points)

        # Left edge (bottom to top)
        for y in range(height - border_radius, border_radius - 1, -2):
            points.append((0, y))

        # Top-left corner (arc)
        corner_points = BorderPathCalculator._calculate_corner_arc(
            border_radius, border_radius, border_radius, 270, 360
        )
        points.extend(corner_points)

        return points

    @staticmethod
    def _calculate_corner_arc(center_x, center_y, radius, start_angle, end_angle):
        """Calculate points for rounded corner arc"""
        points = []
        angle_step = 5  # degrees

        for angle in range(start_angle, end_angle + 1, angle_step):
            radian = math.radians(angle)
            x = center_x + radius * math.cos(radian)
            y = center_y + radius * math.sin(radian)
            points.append((int(x), int(y)))

        return points


class BorderSpinnerAnimation(QObject):
    """Animation system for border-shaped spinner that travels around panel perimeter"""

    def __init__(self, parent=None):
        super().__init__(parent)

    def start_border_spinner(self, widget):
        """Start border spinner animation on a panel widget"""

        if not widget:
            return

        try:
            # Add spinner properties to widget
            widget._spinner_active = True
            widget._spinner_position = 0.0  # 0.0 to 1.0 around perimeter
            widget._spinner_path = BorderPathCalculator.calculate_border_path(
                widget.width(), widget.height(), 5
            )

            # Create position animation
            self._animation = QPropertyAnimation(widget, b"spinner_position")
            self._animation.setDuration(1800)  # 1.8 second loop
            self._animation.setStartValue(0.0)
            self._animation.setEndValue(1.0)
            self._animation.setLoopCount(-1)  # Infinite loop
            self._animation.setEasingCurve(QEasingCurve.Linear)

            # Override widget's paintEvent to draw spinner
            self._setup_spinner_painting(widget)

            # Start animation
            self._animation.start()


        except Exception as e:
            pass  # Silently handle border spinner start errors

    def stop_border_spinner(self, widget):
        """Stop border spinner animation on a panel widget"""

        try:
            if hasattr(widget, '_spinner_active'):
                widget._spinner_active = False

            if hasattr(self, '_animation'):
                self._animation.stop()

            # Trigger final repaint to remove spinner
            widget.update()


        except Exception as e:
            pass  # Silently handle border spinner stop errors

    def _setup_spinner_painting(self, widget):
        """Setup custom painting for spinner on widget"""
        # Store original paintEvent if it exists
        original_paint_event = getattr(widget, 'paintEvent', None)

        def custom_paint_event(event):
            # Call original paint event first
            if original_paint_event:
                original_paint_event(event)

            # Draw spinner if active
            if hasattr(widget, '_spinner_active') and widget._spinner_active:
                self._draw_border_spinner(widget, event)

        # Replace paintEvent with our custom version
        widget.paintEvent = custom_paint_event

    def _draw_border_spinner(self, widget, event):
        """Draw the border spinner segment"""
        if not hasattr(widget, '_spinner_path') or not widget._spinner_path:
            return

        from qtpy.QtGui import QPainter, QPen

        painter = QPainter(widget)
        painter.setRenderHint(QPainter.Antialiasing)

        # Calculate current position along path
        path_length = len(widget._spinner_path)
        if path_length == 0:
            return

        position = getattr(widget, '_spinner_position', 0.0)
        current_index = int(position * (path_length - 1))

        # Draw spinner segment (multiple points for visibility)
        segment_length = 12  # Length of spinner segment in path points
        spinner_color = QColor(74, 144, 226)  # Blue color

        painter.setPen(QPen(spinner_color, 3))

        for i in range(segment_length):
            point_index = (current_index + i) % path_length
            if point_index < len(widget._spinner_path):
                x, y = widget._spinner_path[point_index]
                # Draw small circle at each point to create segment
                painter.drawEllipse(x - 1, y - 1, 3, 3)

        painter.end()


class FlashAnimation(QObject):
    """System for creating elegant green overlay flash animations"""

    def __init__(self, parent=None):
        super().__init__(parent)

    def flash_widget(self, widget, duration=600):
        """Create elegant green overlay flash animation without modifying widget styling"""

        if not widget:
            return

        try:
            # Create green overlay (doesn't modify widget's own styling)
            overlay = GreenFlashOverlay(widget)

            # Create opacity effect for fade animation
            opacity_effect = QGraphicsOpacityEffect()
            overlay.setGraphicsEffect(opacity_effect)

            # Create smooth fade animation
            self._animation = QPropertyAnimation(opacity_effect, b"opacity")
            self._animation.setDuration(duration)
            self._animation.setStartValue(0.0)  # Start transparent
            self._animation.setKeyValueAt(0.1, 0.8)  # Quick fade in to visible
            self._animation.setKeyValueAt(0.3, 0.6)  # Hold visible briefly
            self._animation.setKeyValueAt(1.0, 0.0)  # Smooth fade out
            self._animation.setEasingCurve(QEasingCurve.OutCubic)

            # Clean up when animation finishes
            def cleanup():
                overlay.hide()
                overlay.deleteLater()

            self._animation.finished.connect(cleanup)

            # Show overlay and start animation
            overlay.show()
            overlay.raise_()
            self._animation.start()


        except Exception as e:
            pass  # Silently handle green overlay flash errors


class SpinnerManager(QObject):
    """Manager for coordinating border spinners and flash animations"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_spinners = {}
        self._border_animator = BorderSpinnerAnimation(self)
        self._flash_animator = FlashAnimation(self)

    def show_spinner(self, widget, message="Loading..."):
        """Show border spinner on a specific panel widget"""

        if widget not in self._active_spinners:
            # Start border spinner animation
            self._border_animator.start_border_spinner(widget)
            self._active_spinners[widget] = True

    def hide_spinner(self, widget):
        """Hide border spinner on a specific panel widget"""

        if widget in self._active_spinners:
            self._border_animator.stop_border_spinner(widget)
            del self._active_spinners[widget]

    def flash_success(self, widget, duration=600):
        """Flash widget green for success indication"""
        self._flash_animator.flash_widget(widget, duration=duration)