"""A one-row frame timeline that picks frames and ranges with the mouse.

Remy asked (2026-11-13) for a timeline under Farm Submit's Preview frames
and Frame list fields. :class:`FrameTimeline` is that ruler, written to be
reused wherever a frame range is set or parts of a timeline are marked.

It never holds a selection of its own: the text field beside it does. The
owner feeds it the field's text (:meth:`FrameTimeline.set_text`) and the
frames to show (:meth:`FrameTimeline.set_view`); a gesture emits
:attr:`FrameTimeline.edited` with the new text, made by
``farm.submit.frame_selection`` from the same parse and format code the
submit reads, which the owner puts in the field. So the field and the
timeline cannot disagree.

Mouse: click a frame to add or remove it; drag to add a stretch; Shift- or
Ctrl-drag, or the right button, to remove one. Ctrl+wheel zooms about the
pointer and Shift+wheel (or a sideways wheel) pans a zoomed ruler; a plain
wheel scrolls the form as ever. Keys: Left / Right move the frame cursor
(Shift: 10 frames), Home / End jump to the ends, Space or Enter toggles
the cursor's frame, Delete removes it. Static drawing only: no animation.

Precision (Remy, 2026-11-17): a 200-frame shot gets under two pixels a
frame, so the frame under the pointer is outlined and its number drawn
beside it, and a drag shows the first and last frame it covers, before
the button is let go. The render's Step (every Nth frame) is drawn as
the frames it renders.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from tumbletrove.asset_browser.core.theme import (
    ACCENT, BG_DARK, BORDER, FONT_FAMILY, TEXT_PRIMARY, TEXT_SECONDARY,
)

from tumblepipe.asset_browser.tooltips import wrapped
from tumblepipe.farm.specs import frame_ranges
from tumblepipe.farm.submit import frame_selection as fs
from tumblepipe.util.text import ordinal

#: The widget's height: a track and a row of frame numbers under it.
HEIGHT = 32
_TRACK_TOP, _TRACK_HEIGHT = 2, 16
_DRAG_PIXELS = 4
#: One wheel notch (120 eighths of a degree) zooms by this much.
_ZOOM_PER_NOTCH = 0.8

_HELP = (
    "Click a frame to add or remove it; drag to add a stretch, "
    "Shift-drag or right-drag to remove one. Ctrl+wheel zooms, "
    "Shift+wheel pans. The field above is what is sent."
)


class FrameTimeline(QWidget):
    """The frames ``lo``..``hi`` of a view as a ruler, the selection of
    the field's text drawn on it in ``colour``."""

    #: New field text after a gesture.
    edited = Signal(str)

    def __init__(self, mode: str, *, colour: str = ACCENT, empty_hint: str = '', parent=None) -> None:
        super().__init__(parent)
        self._mode = mode
        self._colour = QColor(colour)
        self._empty_hint = empty_hint
        self._selection = fs.read('', mode)
        self._view: fs.View | None = None
        self._step = 1
        self._window: tuple[int, int] | None = None   # zoomed: the frames shown
        self._context: tuple[tuple[int, int], ...] = ()
        self._cursor: int | None = None
        self._hover: int | None = None
        self._press: tuple[int, int, bool] | None = None   # (x, frame, removing)
        self._drag: tuple[int, int, bool] | None = None    # (a, b, removing)
        self.setFixedHeight(HEIGHT)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.PreventContextMenu)
        self._refresh_tooltip()

    # ── Owner's side ──────────────────────────────────────

    def set_text(self, text: str) -> None:
        """Redraw for the field's ``text``; emits nothing."""
        self._selection = fs.read(text, self._mode)
        self._refresh_tooltip()
        self.update()

    def set_view(self, view: fs.View | None, step: int = 1) -> None:
        """Show ``view``'s frames (None: no range to show) on ``step``: a
        Preview frames timeline picks only frames on it from the first; a
        Frame list timeline draws the frames each range renders on it."""
        if view is None or self._view is None or (view.lo, view.hi) != (self._view.lo, self._view.hi):
            self._window = None
        self._view = view
        self._step = max(1, step)
        if view is not None and self._cursor is not None:
            self._cursor = max(view.lo, min(view.hi, self._cursor))
        self._refresh_tooltip()
        self.update()

    def set_context(self, ranges) -> None:
        """Frames outside ``ranges`` (when any) are drawn shaded: e.g. a
        render preview's frames between a Frame list's ranges, which are
        never rendered."""
        self._context = tuple(ranges or ())
        self.update()

    def selection(self) -> fs.Selection:
        return self._selection

    def view(self) -> fs.View | None:
        return self._view

    def shown(self) -> tuple[int, int] | None:
        """The frames the ruler shows: the view's, or the zoomed part."""
        if self._view is None:
            return None
        return self._window or (self._view.lo, self._view.hi)

    def set_window(self, window: tuple[int, int] | None) -> None:
        """Zoom to ``window`` (None, or all of the view: not zoomed)."""
        view = self._view
        if view is None or window is None or tuple(window) == (view.lo, view.hi):
            self._window = None
        else:
            self._window = (max(view.lo, window[0]), min(view.hi, window[1]))
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(300, HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return QSize(120, HEIGHT)

    # ── Gestures (the mouse and the keys both end here) ───

    def apply(self, a: int, b: int, removing: bool) -> None:
        """Add (or remove) ``a``..``b``; one frame toggles unless
        ``removing``. Emits :attr:`edited` when the text changes."""
        view = self._view
        if view is None or self._selection.error is not None:
            return
        if a == b and not removing:
            new = fs.toggle(self._selection, a, view.lo, view.hi, self._step)
        elif removing:
            new = fs.remove(self._selection, a, b, view.lo, view.hi)
        else:
            new = fs.add(self._selection, a, b, view.lo, view.hi, self._step)
        if new == self._selection:
            return
        old_text = fs.write(self._selection)
        self._selection = new
        self._refresh_tooltip()
        self.update()
        text = fs.write(new)
        if text != old_text:
            self.edited.emit(text)

    # ── Geometry ──────────────────────────────────────────

    def _pick_step(self) -> int:
        """The step a pick snaps to: a Preview frame must be on the step;
        a Frame list range may start anywhere (each renders on the step
        from its own first frame)."""
        return self._step if self._mode == fs.FRAMES else 1

    def _track(self) -> QRectF:
        return QRectF(1, _TRACK_TOP, max(1, self.width() - 2), _TRACK_HEIGHT)

    def _frame_float(self, x: float) -> float | None:
        """The (fractional) frame under ``x``; None without a view."""
        shown = self.shown()
        if shown is None:
            return None
        track = self._track()
        count = shown[1] - shown[0] + 1
        return shown[0] + (x - track.left()) / track.width() * count

    def frame_at(self, x: float) -> int | None:
        """The frame under ``x``, snapped to the step; None without a view."""
        at = self._frame_float(x)
        if at is None:
            return None
        lo, hi = self.shown()
        frame = min(hi, max(lo, int(at // 1)))
        view = self._view
        return fs.snap(frame, view.lo, view.hi, self._pick_step())

    def _x_of(self, frame: float) -> float:
        lo, hi = self.shown()
        track = self._track()
        count = hi - lo + 1
        return track.left() + (frame - lo) / count * track.width()

    def _span_rect(self, a: int, b: int) -> QRectF:
        track = self._track()
        left = self._x_of(a)
        right = max(self._x_of(b + 1), left + 2)
        return QRectF(left, track.top(), right - left, track.height())

    # ── Mouse and keys ────────────────────────────────────

    def _removing(self, event) -> bool:
        mods = event.modifiers()
        return bool(
            event.button() == Qt.RightButton
            or mods & (Qt.ShiftModifier | Qt.ControlModifier)
        )

    def mousePressEvent(self, event) -> None:
        frame = self.frame_at(event.position().x())
        if frame is None or self._selection.error is not None or event.button() not in (
            Qt.LeftButton, Qt.RightButton,
        ):
            return super().mousePressEvent(event)
        self._press = (int(event.position().x()), frame, self._removing(event))
        self._cursor = frame
        self.update()

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()
        frame = self.frame_at(x)
        if self._press is not None and frame is not None:
            px, start, removing = self._press
            if abs(x - px) >= _DRAG_PIXELS or self._drag is not None:
                self._drag = (min(start, frame), max(start, frame), removing)
        if frame != self._hover:
            self._hover = frame
            # The tooltip names the frame it was opened over: a new frame
            # closes it rather than leave it naming the old one.
            QToolTip.hideText()
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        press, drag = self._press, self._drag
        self._press = self._drag = None
        if press is None:
            return super().mouseReleaseEvent(event)
        if drag is not None:
            self.apply(drag[0], drag[1], drag[2])
        else:
            self.apply(press[1], press[1], press[2])
        self.update()

    def wheelEvent(self, event) -> None:
        view = self._view
        delta = event.angleDelta()
        mods = event.modifiers()
        if view is None:
            return event.ignore()
        shown = self.shown()
        if mods & Qt.ControlModifier and delta.y():
            anchor = self._frame_float(event.position().x())
            factor = _ZOOM_PER_NOTCH ** (delta.y() / 120)
            self.set_window(fs.zoom(view.lo, view.hi, self._window, anchor, factor))
            return event.accept()
        sideways = delta.x() or (delta.y() if mods & Qt.ShiftModifier else 0)
        if self._window is not None and sideways:
            count = shown[1] - shown[0] + 1
            frames = -round(sideways / 120 * max(1, count // 8)) or (-1 if sideways > 0 else 1)
            self.set_window(fs.pan(view.lo, view.hi, self._window, frames))
            return event.accept()
        event.ignore()      # a plain wheel scrolls the form

    def keyPressEvent(self, event) -> None:
        view = self._view
        if view is None:
            return super().keyPressEvent(event)
        key = event.key()
        step = self._pick_step()
        cursor = view.lo if self._cursor is None else self._cursor
        jump = 10 * step if event.modifiers() & Qt.ShiftModifier else step
        if key == Qt.Key_Left:
            self._cursor = fs.snap(cursor - jump, view.lo, view.hi, step)
        elif key == Qt.Key_Right:
            self._cursor = fs.snap(cursor + jump, view.lo, view.hi, step)
        elif key == Qt.Key_Home:
            self._cursor = view.lo
        elif key == Qt.Key_End:
            self._cursor = fs.snap(view.hi, view.lo, view.hi, step)
        elif key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self._cursor = cursor
            self.apply(cursor, cursor, False)
        elif key in (Qt.Key_Delete, Qt.Key_Backspace):
            self._cursor = cursor
            self.apply(cursor, cursor, True)
        else:
            return super().keyPressEvent(event)
        if self._window is not None:
            self.set_window(fs.showing(view.lo, view.hi, self._window, self._cursor))
        self.update()

    def focusOutEvent(self, event) -> None:
        self.update()
        super().focusOutEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.ToolTip:
            frame = self.frame_at(event.pos().x())
            text = self._general_tip()
            if frame is not None:
                text = self._frame_tip(frame) + "\n\n" + text
            QToolTip.showText(event.globalPos(), wrapped(text), self)
            return True
        return super().event(event)

    # ── Words ─────────────────────────────────────────────

    def _what(self) -> str:
        view = self._view
        if view is None:
            return "No frame range to show: tick a shot with a frame range."
        shown = f"Shows {view.lo}-{view.hi}"
        if view.caption:
            shown += f" ({view.caption})"
        if self._step > 1:
            shown += f", every {ordinal(self._step)} frame"
        if view.mixed:
            shown += ". Each shot uses only the frames inside its own range"
        return shown + "."

    def _general_tip(self) -> str:
        lines = [self._what()]
        if self._selection.error is not None:
            lines.append(f"Fix the text first: {self._selection.error}")
        elif self._selection.is_empty and self._empty_hint:
            lines.append(f"Empty: {self._empty_hint}.")
        lines.append(_HELP)
        return "\n\n".join(lines)

    def _refresh_tooltip(self) -> None:
        # Rich text: wraps at a readable width (asset_browser.tooltips).
        self.setToolTip(wrapped(self._general_tip()))
        self.setAccessibleName("Frame timeline")
        self.setAccessibleDescription(self._what())

    def _frame_tip(self, frame: int) -> str:
        if self._selection.error is not None:
            return f"Frame {frame}. Fix the text first."
        on = fs.contains(self._selection, frame)
        return f"Frame {frame}: {'selected' if on else 'not selected'}"

    def floating_label(self) -> str:
        """What the chip beside the pointer says: the stretch a drag covers,
        else the frame under the pointer, else the keyboard cursor's frame;
        empty for none."""
        if self._drag is not None:
            a, b, removing = self._drag
            text = str(a) if a == b else f"{a}-{b}"
            return ("remove " if removing else "") + text
        if self._hover is not None:
            return str(self._hover)
        if self.hasFocus() and self._cursor is not None:
            return str(self._cursor)
        return ''

    # ── Painting ──────────────────────────────────────────

    def _frames_drawn(self, spans, lo: int, hi: int):
        """``spans`` as the rects to fill: whole spans on step 1, else
        each frame a Frame list range renders on the step."""
        if self._step > 1 and self._mode == fs.RANGES:
            return [self._span_rect(f, f) for f in fs.stepped_in(spans, self._step, lo, hi)]
        return [
            self._span_rect(max(a, lo), min(b, hi))
            for a, b in spans if not (b < lo or a > hi)
        ]

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        track = self._track()
        painter.setPen(QPen(QColor(BORDER), 1))
        painter.setBrush(QColor(BG_DARK))
        painter.drawRect(track)
        font = QFont(FONT_FAMILY)
        font.setPixelSize(10)
        painter.setFont(font)
        label_rect = QRectF(2, _TRACK_TOP + _TRACK_HEIGHT + 1, self.width() - 4,
                            HEIGHT - _TRACK_TOP - _TRACK_HEIGHT - 1)
        view = self._view
        if view is None:
            painter.setPen(QColor(TEXT_SECONDARY))
            painter.drawText(track, Qt.AlignCenter, "no frame range")
            return
        lo, hi = self.shown()
        selection = self._selection
        painter.save()
        painter.setClipRect(track.adjusted(1, 1, -1, -1))
        if self._context:
            shade = QColor(TEXT_SECONDARY)
            shade.setAlpha(40)
            edges = [view.lo - 1, *[v for r in self._context for v in r], view.hi + 1]
            for gap_first, gap_last in zip(edges[0::2], edges[1::2]):
                if gap_last - gap_first > 1 and not (gap_last - 1 < lo or gap_first + 1 > hi):
                    painter.fillRect(
                        self._span_rect(max(lo, gap_first + 1), min(hi, gap_last - 1)),
                        QBrush(shade, Qt.BDiagPattern),
                    )
        fill = QColor(self._colour)
        if selection.error is not None:
            painter.fillRect(track.adjusted(1, 1, -1, -1), QBrush(QColor(TEXT_SECONDARY), Qt.BDiagPattern))
        elif selection.is_empty:
            ghost = QColor(fill)
            ghost.setAlpha(70)
            if self._mode == fs.RANGES:
                for rect in self._frames_drawn(((view.lo, view.hi),), lo, hi):
                    painter.fillRect(rect, ghost)
            else:
                for frame in fs.default_frames(view.lo, view.hi, self._step):
                    if lo <= frame <= hi:
                        painter.fillRect(self._span_rect(frame, frame), ghost)
        else:
            spans = (
                frame_ranges.of_frames(selection.frames) if self._mode == fs.FRAMES
                else selection.ranges
            )
            for rect in self._frames_drawn(spans, lo, hi):
                painter.fillRect(rect, fill)
        if self._drag is not None:
            a, b, removing = self._drag
            band = QColor(TEXT_PRIMARY if removing else self._colour.lighter(140))
            band.setAlpha(90)
            painter.fillRect(self._span_rect(max(a, lo), min(b, hi)), band)
        marker = self._hover if self._hover is not None else (
            self._cursor if self.hasFocus() else None
        )
        if marker is not None and lo <= marker <= hi:
            # The frame a click lands on: outlined when there is room, a
            # line through it either way.
            cell = self._span_rect(marker, marker)
            painter.setPen(QPen(QColor(TEXT_PRIMARY), 1))
            painter.setBrush(Qt.NoBrush)
            if cell.width() >= 5:
                painter.drawRect(cell.adjusted(0, 0, -1, -1))
            x = int(self._x_of(marker + 0.5))
            painter.drawLine(x, int(track.top()), x, int(track.bottom()))
        if self._window is not None:
            # Where the zoomed part sits in the whole view.
            total = view.hi - view.lo + 1
            left = track.left() + (lo - view.lo) / total * track.width()
            width = max(4.0, (hi - lo + 1) / total * track.width())
            painter.fillRect(QRectF(left, track.bottom() - 2, width, 2), QColor(TEXT_SECONDARY))
        painter.restore()
        # Frame numbers: the shown ends, and what the view is between them.
        painter.setPen(QColor(TEXT_SECONDARY))
        painter.drawText(label_rect, Qt.AlignLeft | Qt.AlignVCenter, str(lo))
        painter.drawText(label_rect, Qt.AlignRight | Qt.AlignVCenter, str(hi))
        chip = self.floating_label()
        if chip:
            self._draw_chip(painter, label_rect, chip)
            return
        middle = (
            "Fix the text above" if selection.error is not None
            else (self._empty_hint if selection.is_empty else '')
        )
        if self._window is not None and not middle:
            middle = f"zoomed: {lo}-{hi} of {view.lo}-{view.hi}"
        elif view.caption and not middle:
            middle = view.caption
        if middle:
            painter.drawText(label_rect, Qt.AlignHCenter | Qt.AlignVCenter, middle)

    def _draw_chip(self, painter: QPainter, label_rect: QRectF, text: str) -> None:
        """``text`` in a chip under the frame (or stretch) it names, kept
        inside the widget; it covers the end numbers it overlaps."""
        if self._drag is not None:
            a, b, _removing = self._drag
            centre = (self._x_of(a) + self._x_of(b + 1)) / 2
        else:
            frame = self._hover if self._hover is not None else self._cursor
            centre = self._x_of(frame + 0.5)
        width = QFontMetrics(painter.font()).horizontalAdvance(text) + 8
        left = max(0.0, min(self.width() - width, centre - width / 2))
        rect = QRectF(left, label_rect.top(), width, label_rect.height())
        painter.setPen(QPen(self._colour, 1))
        painter.setBrush(QColor(BG_DARK))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.setPen(QColor(TEXT_PRIMARY))
        painter.drawText(rect, Qt.AlignCenter, text)
