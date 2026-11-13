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
Ctrl-drag, or the right button, to remove one. Keys: Left / Right move the
frame cursor (Shift: 10 frames), Home / End jump to the ends, Space or
Enter toggles the cursor's frame, Delete removes it. Static drawing only:
no animation.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from tumbletrove.asset_browser.core.theme import (
    ACCENT, BG_DARK, BORDER, FONT_FAMILY, TEXT_PRIMARY, TEXT_SECONDARY,
)

from tumblepipe.farm.specs import frame_ranges
from tumblepipe.farm.submit import frame_selection as fs

#: The widget's height: a track and a row of frame numbers under it.
HEIGHT = 32
_TRACK_TOP, _TRACK_HEIGHT = 2, 16
_DRAG_PIXELS = 4

_HELP = (
    "Click a frame to add or remove it. Drag to add a stretch of frames; "
    "Shift- or Ctrl-drag, or drag with the right button, to remove one. "
    "Keys: Left / Right move the cursor (Shift: 10 frames), Home / End, "
    "Space toggles the frame, Delete removes it. The text field above is "
    "what is sent: the timeline writes it, and typing there redraws this."
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
        self._context: tuple[tuple[int, int], ...] = ()
        self._cursor: int | None = None
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
        """Show ``view``'s frames (None: no range to show), picking frames
        on ``step`` from its first."""
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

    def _track(self) -> QRectF:
        return QRectF(1, _TRACK_TOP, max(1, self.width() - 2), _TRACK_HEIGHT)

    def frame_at(self, x: float) -> int | None:
        """The frame under ``x``, snapped to the step; None without a view."""
        view = self._view
        if view is None:
            return None
        track = self._track()
        count = view.hi - view.lo + 1
        index = int((x - track.left()) / track.width() * count)
        return fs.snap(view.lo + index, view.lo, view.hi, self._step)

    def _x_of(self, frame: float) -> float:
        view = self._view
        track = self._track()
        count = view.hi - view.lo + 1
        return track.left() + (frame - view.lo) / count * track.width()

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
                self.update()
        if frame is not None:
            QToolTip.showText(
                event.globalPosition().toPoint(), self._frame_tip(frame), self,
            )

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

    def keyPressEvent(self, event) -> None:
        view = self._view
        if view is None:
            return super().keyPressEvent(event)
        key = event.key()
        cursor = view.lo if self._cursor is None else self._cursor
        jump = 10 * self._step if event.modifiers() & Qt.ShiftModifier else self._step
        if key == Qt.Key_Left:
            self._cursor = fs.snap(cursor - jump, view.lo, view.hi, self._step)
        elif key == Qt.Key_Right:
            self._cursor = fs.snap(cursor + jump, view.lo, view.hi, self._step)
        elif key == Qt.Key_Home:
            self._cursor = view.lo
        elif key == Qt.Key_End:
            self._cursor = fs.snap(view.hi, view.lo, view.hi, self._step)
        elif key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self._cursor = cursor
            self.apply(cursor, cursor, False)
        elif key in (Qt.Key_Delete, Qt.Key_Backspace):
            self._cursor = cursor
            self.apply(cursor, cursor, True)
        else:
            return super().keyPressEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:
        self.update()
        super().focusOutEvent(event)

    # ── Words ─────────────────────────────────────────────

    def _what(self) -> str:
        view = self._view
        if view is None:
            return "No frame range to show: tick a shot with a frame range."
        shown = f"Shows {view.lo}-{view.hi}"
        if view.caption:
            shown += f" ({view.caption})"
        if view.mixed:
            shown += (
                ". The ticked shots' ranges differ; each shot uses only "
                "the frames inside its own"
            )
        return shown + "."

    def _refresh_tooltip(self) -> None:
        lines = [self._what()]
        if self._selection.error is not None:
            lines.append(f"Fix the text first: {self._selection.error}")
        elif self._selection.is_empty and self._empty_hint:
            lines.append(self._empty_hint)
        lines.append(_HELP)
        self.setToolTip("\n\n".join(lines))
        self.setAccessibleName("Frame timeline")
        self.setAccessibleDescription(lines[0])

    def _frame_tip(self, frame: int) -> str:
        on = fs.contains(self._selection, frame)
        if self._selection.error is not None:
            return f"Frame {frame}. Fix the text first."
        return f"Frame {frame}: {'selected' if on else 'not selected'}"

    # ── Painting ──────────────────────────────────────────

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
        selection = self._selection
        if self._context:
            shade = QColor(TEXT_SECONDARY)
            shade.setAlpha(40)
            edges = [view.lo - 1, *[v for r in self._context for v in r], view.hi + 1]
            for gap_first, gap_last in zip(edges[0::2], edges[1::2]):
                if gap_last - gap_first > 1:
                    painter.fillRect(
                        self._span_rect(gap_first + 1, gap_last - 1), QBrush(shade, Qt.BDiagPattern),
                    )
        fill = QColor(self._colour)
        if selection.error is not None:
            painter.fillRect(track.adjusted(1, 1, -1, -1), QBrush(QColor(TEXT_SECONDARY), Qt.BDiagPattern))
        elif selection.is_empty:
            ghost = QColor(fill)
            ghost.setAlpha(70)
            if self._mode == fs.RANGES:
                painter.fillRect(track.adjusted(1, 1, -1, -1), ghost)
            else:
                for frame in fs.default_frames(view.lo, view.hi, self._step):
                    painter.fillRect(self._span_rect(frame, frame), ghost)
        else:
            spans = (
                frame_ranges.of_frames(selection.frames) if self._mode == fs.FRAMES
                else selection.ranges
            )
            for a, b in spans:
                if b < view.lo or a > view.hi:
                    continue
                painter.fillRect(self._span_rect(max(a, view.lo), min(b, view.hi)), fill)
        if self._drag is not None:
            a, b, removing = self._drag
            band = QColor(TEXT_PRIMARY if removing else self._colour.lighter(140))
            band.setAlpha(90)
            painter.fillRect(self._span_rect(a, b), band)
        if self.hasFocus() and self._cursor is not None:
            painter.setPen(QPen(QColor(TEXT_PRIMARY), 1))
            x = self._x_of(self._cursor + 0.5)
            painter.drawLine(int(x), int(track.top()), int(x), int(track.bottom()))
        # Frame numbers: the ends, and what the view is between them.
        painter.setPen(QColor(TEXT_SECONDARY))
        painter.drawText(label_rect, Qt.AlignLeft | Qt.AlignVCenter, str(view.lo))
        painter.drawText(label_rect, Qt.AlignRight | Qt.AlignVCenter, str(view.hi))
        middle = (
            "Fix the text above" if selection.error is not None
            else (self._empty_hint if selection.is_empty else '')
        )
        if self.hasFocus() and self._cursor is not None:
            middle = str(self._cursor)
        elif view.caption and not middle:
            middle = view.caption
        if middle:
            painter.drawText(label_rect, Qt.AlignHCenter | Qt.AlignVCenter, middle)
