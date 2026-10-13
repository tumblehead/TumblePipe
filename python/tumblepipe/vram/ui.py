"""The VRAM Inspector window.

Top: the whole process's dedicated VRAM (Windows' counter), what ``gpumem``
accounts for, and the gap between them. Below: the report tree — one row per
``gpumem`` client, opened down through node, prim and primvar, or texture
file, or Copernicus layer.

Columns, left to right, go from estimate to fact:

* **VRAM** — the row's share of its client's *measured* total (exact for the
  client rows, an estimate below them).
* **Upload** — what the viewport model says the row puts on the GPU.
* **Data** — what the scene holds. Exact.

Everything runs on Houdini's main thread. The stage scan is sliced by a
zero-interval timer into ~20 ms steps so the session stays responsive, and a
stage that changed since the scan is flagged rather than silently rescanned.

``gpumem`` runs only when the artist asks: opening the window, Rescan, Free
cached. Never on a timer. Houdini's own command segfaults
(``GUI_GeoRender::gpuMemoryUsage`` ← ``DM_SceneManager::memoryButtons``) when
it runs while the viewport is tearing down a geometry render, e.g. right after
a prim is hidden. TumbleTrove 0.46.0, where this tool used to live, polled it
every 3 s and crashed a lighting session exactly that way (H22.0.368).
"""

from __future__ import annotations

import logging
import os
import time

import hou
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from tumbletrove.common.gui import gui_dispatch, main_window
from tumbletrove.common.theme import (
    ACCENT, BG_DARK, BG_DARKEST, BORDER, BUTTON_FLAT_STYLE, SCROLLBAR_STYLE,
    SEARCH_STYLE, TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY, WARNING, scaled,
    styled_menu,
)
from . import collect, cop_scan, report
from .report import Row, human_bytes
from .usd_scan import ScanOptions, StageScan

log = logging.getLogger(__name__)

_COLS = ("Name", "VRAM", "Upload", "Data", "Details")
_COL_NAME, _COL_SHARE, _COL_UPLOAD, _COL_DATA, _COL_DETAIL = range(5)
_ROW_ROLE = Qt.UserRole + 1
_FRACTION_ROLE = Qt.UserRole + 2

_SLICE_SECONDS = 0.02

_TREE_STYLE = f"""
QTreeWidget {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    alternate-background-color: {BG_DARKEST};
}}
QHeaderView::section {{
    background-color: {BG_DARKEST};
    color: {TEXT_SECONDARY};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 4px 6px;
}}
"""

_KIND_HINT = {
    "geometry": "Viewer geometry — split across nodes by the size of what they upload",
    "textures": "Viewport texture cache — split across the files materials bind",
    "cop": "Copernicus buffers — split across the layers cooked COP nodes hold",
    "opencl": "OpenCL buffer pool (simulations, OpenCL SOPs)",
    "render": "Karma XPU",
}


class _ShareBar(QStyledItemDelegate):
    """Paints a thin bar under the VRAM figure: the row's fraction of its
    parent, so the biggest consumer at each level stands out at a glance."""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        fraction = index.data(_FRACTION_ROLE)
        if not fraction:
            return
        r = option.rect
        h = max(2, scaled(3))
        bar = QRectF(r.left() + 2, r.bottom() - h, (r.width() - 4) * min(1.0, fraction), h)
        painter.save()
        painter.fillRect(bar, QColor(ACCENT))
        painter.restore()


class VramInspector(QWidget):
    """What in this session is using the GPU's memory."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("VRAM Inspector")
        self.setObjectName("tumblepipe_vram_inspector")
        self.resize(scaled(980), scaled(640))
        self.setStyleSheet(f"QWidget#tumblepipe_vram_inspector {{ background-color: {BG_DARKEST}; }}")

        self._clients = []
        self._geometry: dict[str, list] = {}
        self._serials: dict[str, tuple] = {}
        self._textures = []
        self._cop = cop_scan.CopScan()
        self._rows: list[Row] = []
        self._scans: list[tuple[collect.Viewer, StageScan]] = []
        self._scan_started = 0.0

        self._build()
        self._slicer = QTimer(self)
        self._slicer.setInterval(0)
        self._slicer.timeout.connect(self._scan_slice)

        self.rescan()

    # ── construction ─────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(scaled(10), scaled(10), scaled(10), scaled(10))
        root.setSpacing(scaled(8))

        bar = QHBoxLayout()
        self._rescan_btn = QPushButton("Rescan", self)
        self._rescan_btn.setToolTip("Read the viewers' stages and the COP layers again")
        self._rescan_btn.clicked.connect(self.rescan)
        self._free_btn = QPushButton("Free cached", self)
        self._free_btn.setToolTip(
            "Ask every GPU client to release memory it holds but is not using "
            "(gpumem -f). It is reloaded on demand.")
        self._free_btn.clicked.connect(self._free_cached)
        for b in (self._rescan_btn, self._free_btn):
            b.setStyleSheet(BUTTON_FLAT_STYLE)
            bar.addWidget(b)
        bar.addStretch(1)
        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Filter by name or path…")
        self._filter.setClearButtonEnabled(True)
        self._filter.setStyleSheet(SEARCH_STYLE)
        self._filter.setMinimumWidth(scaled(260))
        self._filter.textChanged.connect(self._apply_filter)
        bar.addWidget(self._filter)
        root.addLayout(bar)

        self._summary = QLabel(self)
        self._summary.setTextFormat(Qt.RichText)
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(f"color: {TEXT_PRIMARY};")
        root.addWidget(self._summary)

        self._status = QLabel(self)
        self._status.setStyleSheet(f"color: {TEXT_DIM};")
        root.addWidget(self._status)

        tree = QTreeWidget(self)
        tree.setColumnCount(len(_COLS))
        tree.setHeaderLabels(list(_COLS))
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setStyleSheet(_TREE_STYLE + SCROLLBAR_STYLE)
        tree.setItemDelegateForColumn(_COL_SHARE, _ShareBar(tree))
        header = tree.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(_COL_NAME, QHeaderView.Interactive)
        tree.setColumnWidth(_COL_NAME, scaled(340))
        for col in (_COL_SHARE, _COL_UPLOAD, _COL_DATA):
            tree.setColumnWidth(col, scaled(90))
        tree.headerItem().setToolTip(_COL_SHARE, "Share of the measured gpumem total "
                                     "(exact on client rows, estimated below)")
        tree.headerItem().setToolTip(_COL_UPLOAD, "What the viewport model puts on the GPU")
        tree.headerItem().setToolTip(_COL_DATA, "What the scene holds (exact)")
        tree.itemExpanded.connect(self._fill_children)
        tree.itemClicked.connect(self._on_click)
        tree.itemDoubleClicked.connect(self._on_double_click)
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._context_menu)
        self._tree = tree
        root.addWidget(tree, 1)

    # ── scanning ─────────────────────────────────────────────────────────

    def rescan(self) -> None:
        """Measure, then read every LOP viewer's stage, then the COPs."""
        if self._slicer.isActive():
            return
        self._clients = collect.measure()
        self._scans = []
        seen: dict[tuple, StageScan] = {}
        for viewer in collect.lop_viewers(self._clients):
            try:
                stage = viewer.stage()
            except hou.Error:
                stage = None
            if stage is None:
                continue
            key = viewer.serial()
            scan = seen.get(key)
            if scan is None:
                options = ScanOptions(rules=collect.texture_rules(viewer.viewer))
                from .images import read_image_info
                scan = StageScan(stage, options,
                                 owner_of=collect.owner_resolver(viewer.network),
                                 image_info=read_image_info)
                seen[key] = scan
            self._scans.append((viewer, scan))
        self._rescan_btn.setEnabled(False)
        self._scan_started = time.monotonic()
        self._status.setText("Scanning…")
        self._slicer.start()

    def _scan_slice(self) -> None:
        deadline = time.monotonic() + _SLICE_SECONDS
        try:
            for _viewer, scan in self._scans:
                while not scan.done and time.monotonic() < deadline:
                    scan.step(100)
                if not scan.done:
                    visited = sum(s.visited for _v, s in self._scans)
                    self._status.setText(f"Scanning stage… {visited:,} prims read")
                    return
        except Exception:
            log.exception("VRAM scan failed")
            self._status.setText("Scan failed — see the console for details.")
            self._finish_scan()
            return
        self._finish_scan()

    def _finish_scan(self) -> None:
        self._slicer.stop()
        self._rescan_btn.setEnabled(True)
        self._geometry = {}
        self._serials = {}
        textures: dict[str, object] = {}
        for viewer, scan in self._scans:
            self._geometry[viewer.pane] = list(scan.prims)
            self._serials[viewer.pane] = viewer.serial()
            for tex in scan.textures:
                textures.setdefault(tex.path, tex)
        self._textures = list(textures.values())
        try:
            self._cop = cop_scan.scan()
        except Exception:
            log.exception("COP scan failed")
            self._cop = cop_scan.CopScan()
        took = time.monotonic() - self._scan_started
        prims = sum(len(v) for v in self._geometry.values())
        note = (f"Scanned {_count(prims, 'drawn prim')}, "
                f"{_count(len(self._textures), 'texture')}, "
                f"{_count(len(self._cop.layers), 'COP layer')} in {took:.1f}s.")
        if self._cop.skipped:
            note += f" {self._cop.skipped} uncooked COP nodes left alone."
        self._status.setText(note)
        self._rebuild()

    # ── measuring ────────────────────────────────────────────────────────

    def _free_cached(self) -> None:
        freed = []
        for device in {c.device for c in self._clients if c.cached_bytes > 0}:
            mb = sum(c.cached_bytes for c in self._clients if c.device == device) // (1 << 20) + 1
            try:
                out, _ = hou.hscript(f'gpumem -r "{device}" -f {mb}')
                freed.append(out.strip())
            except hou.Error:
                log.exception("gpumem -f failed on %s", device)
        self._status.setText(" ".join(f for f in freed if f) or "Nothing cached to free.")
        self._clients = collect.measure()
        self._rebuild()

    # ── tree ─────────────────────────────────────────────────────────────

    def _rebuild(self) -> None:
        expanded = self._expanded_keys()
        self._rows = report.build(self._clients, self._geometry, self._textures,
                                  self._cop.layers)
        self._tree.setUpdatesEnabled(False)
        try:
            self._tree.clear()
            total = sum(r.share for r in self._rows if r.measured) or 1
            for row in self._rows:
                item = self._make_item(row, row.share / total)
                self._tree.addTopLevelItem(item)
            self._restore_expanded(expanded)
        finally:
            self._tree.setUpdatesEnabled(True)
        self._update_summary()
        self._apply_filter(self._filter.text())

    def _make_item(self, row: Row, fraction: float) -> QTreeWidgetItem:
        item = QTreeWidgetItem([
            row.label,
            human_bytes(row.share),
            human_bytes(row.upload) if row.upload or row.children else "",
            human_bytes(row.data) if row.data or row.children else "",
            self._detail(row),
        ])
        item.setData(_COL_NAME, _ROW_ROLE, row)
        item.setData(_COL_SHARE, _FRACTION_ROLE, fraction)
        for col in (_COL_SHARE, _COL_UPLOAD, _COL_DATA):
            item.setTextAlignment(col, Qt.AlignRight | Qt.AlignVCenter)
        tip = row.ref or row.label
        item.setToolTip(_COL_NAME, tip)
        item.setToolTip(_COL_DETAIL, self._detail(row))
        if row.measured:
            font = item.font(_COL_NAME)
            font.setBold(True)
            for col in (_COL_NAME, _COL_SHARE):
                item.setFont(col, font)
            item.setToolTip(_COL_SHARE, "Measured by gpumem")
        elif row.kind in ("geometry", "textures", "cop") and not row.measured:
            item.setForeground(_COL_SHARE, QBrush(QColor(TEXT_DIM)))
            item.setToolTip(_COL_SHARE, "No gpumem client to measure against — model estimate")
        if row.children:
            item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        return item

    def _detail(self, row: Row) -> str:
        if row.measured:
            parts = [row.detail]
            cov = report.coverage(row)
            if cov is not None:
                if cov > 1.15:
                    parts.append(f"scene asks for {cov:.1f}× this — evicted or reduced")
                else:
                    parts.append(f"scan explains {cov:.0%}")
            elif not row.children and row.kind in _KIND_HINT:
                parts.append(_KIND_HINT[row.kind])
            return " · ".join(p for p in parts if p)
        return row.detail

    def _fill_children(self, item: QTreeWidgetItem) -> None:
        """Create an item's children the first time it opens — a lighting
        stage has tens of thousands of primvar rows nobody expands."""
        if item.childCount():
            return
        row: Row = item.data(_COL_NAME, _ROW_ROLE)
        if row is None:
            return
        whole = row.share or 1
        for child in row.children:
            item.addChild(self._make_item(child, child.share / whole))
        if not row.children:
            item.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)

    def _key(self, item: QTreeWidgetItem) -> tuple:
        parts = []
        while item is not None:
            row = item.data(_COL_NAME, _ROW_ROLE)
            parts.append((row.label, row.ref) if row else (item.text(0), ""))
            item = item.parent()
        return tuple(reversed(parts))

    def _expanded_keys(self) -> set[tuple]:
        keys = set()

        def visit(item):
            if item.isExpanded():
                keys.add(self._key(item))
                for i in range(item.childCount()):
                    visit(item.child(i))

        for i in range(self._tree.topLevelItemCount()):
            visit(self._tree.topLevelItem(i))
        return keys

    def _restore_expanded(self, keys: set[tuple]) -> None:
        if not keys:
            return

        def visit(item):
            if self._key(item) in keys:
                item.setExpanded(True)  # fills children via itemExpanded
                for i in range(item.childCount()):
                    visit(item.child(i))

        for i in range(self._tree.topLevelItemCount()):
            visit(self._tree.topLevelItem(i))

    def _apply_filter(self, text: str) -> None:
        """Hide top-level branches with no match. Matches are searched in the
        report rows, not just the items built so far, and a matching branch
        is opened down to the match."""
        needle = text.strip().casefold()
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            row: Row = item.data(_COL_NAME, _ROW_ROLE)
            if not needle:
                item.setHidden(False)
                continue
            hit = any(needle in r.label.casefold() or needle in r.ref.casefold()
                      for r in row.walk())
            item.setHidden(not hit)
            if hit:
                self._open_to(item, needle)

    def _open_to(self, item: QTreeWidgetItem, needle: str) -> None:
        row: Row = item.data(_COL_NAME, _ROW_ROLE)
        if not any(needle in r.label.casefold() or needle in r.ref.casefold()
                   for r in row.walk()):
            return
        own = needle in row.label.casefold() or needle in row.ref.casefold()
        if row.children and not own:
            item.setExpanded(True)
            for i in range(item.childCount()):
                self._open_to(item.child(i), needle)

    def _update_summary(self) -> None:
        measured = sum(c.used_bytes for c in self._clients)
        cached = sum(c.cached_bytes for c in self._clients)
        process_total = collect.process_bytes()
        devices = sorted({c.device for c in self._clients if c.device})
        parts = []
        if process_total is not None:
            parts.append(f"<b>Houdini process:</b> {human_bytes(process_total)} dedicated")
        parts.append(f"<b>gpumem:</b> {human_bytes(measured)} across "
                     f"{len(self._clients)} clients"
                     + (f" ({human_bytes(cached)} cached, freeable)" if cached else ""))
        if process_total is not None and process_total > measured:
            parts.append(f"<span style='color:{TEXT_SECONDARY}'>not reported by Houdini: "
                         f"{human_bytes(process_total - measured)}</span>")
            self._summary.setToolTip(
                "Windows' dedicated-VRAM figure for the whole Houdini process, "
                "minus what gpumem accounts for: window surfaces and render "
                "targets, the driver, and any GPU user that does not register "
                "with gpumem (plug-ins, other renderers).")
        stale = [pane for pane, serial in self._serials.items()
                 if self._viewer_serial(pane) not in (serial, None)]
        if stale:
            parts.append(f"<span style='color:{WARNING}'>stage changed since the scan — Rescan</span>")
        line = " · ".join(parts)
        if devices:
            line += f"<br><span style='color:{TEXT_DIM}'>{', '.join(devices)}</span>"
        self._summary.setText(line)
        self._free_btn.setEnabled(cached > 0)

    def _viewer_serial(self, pane: str) -> tuple | None:
        for viewer in collect.lop_viewers(self._clients):
            if viewer.pane == pane:
                return viewer.serial()
        return None

    # ── actions ──────────────────────────────────────────────────────────

    def _on_click(self, item: QTreeWidgetItem, _col: int) -> None:
        row: Row = item.data(_COL_NAME, _ROW_ROLE)
        if row is None or not row.ref:
            return
        try:
            if row.kind in ("node",) or (row.kind == "layer" and row.ref.startswith("/")):
                node = hou.node(row.ref)
                if node is not None:
                    node.setCurrent(True, clear_all_selected=True)
            elif row.kind in ("prim", "primvar", "topology", "instances"):
                self._select_prim(item, row.ref)
        except hou.Error:
            log.debug("could not select %s", row.ref, exc_info=True)

    def _on_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        row: Row = item.data(_COL_NAME, _ROW_ROLE)
        if row is None or row.kind not in ("node", "layer"):
            return
        node = hou.node(row.ref)
        if node is None:
            return
        editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if editor is not None:
            editor.setPwd(node.parent())
            editor.setCurrentNode(node)
            editor.homeToSelection()

    def _select_prim(self, item: QTreeWidgetItem, prim_path: str) -> None:
        """Select the prim in the viewer whose client this row hangs under."""
        top = item
        while top.parent() is not None:
            top = top.parent()
        top_row: Row = top.data(_COL_NAME, _ROW_ROLE)
        for viewer in collect.lop_viewers(self._clients):
            if top_row is not None and viewer.pane != top_row.ref:
                continue
            setter = getattr(viewer.viewer, "setCurrentSceneGraphSelection", None)
            if setter is not None:
                setter([prim_path])
            return

    def _context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        row: Row = item.data(_COL_NAME, _ROW_ROLE)
        if row is None:
            return
        menu = styled_menu(self._tree)
        if row.ref:
            menu.addAction("Copy path", lambda: QGuiApplication.clipboard().setText(row.ref))
        if row.kind == "texture" and os.path.isfile(row.ref):
            menu.addAction("Show in folder", lambda: _reveal(row.ref))
        menu.addAction("Copy row", lambda: QGuiApplication.clipboard().setText(
            "\t".join(item.text(c) for c in range(len(_COLS)))))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def closeEvent(self, event) -> None:
        self._slicer.stop()
        super().closeEvent(event)


def _count(n: int, noun: str) -> str:
    return f"{n:,} {noun}{'' if n == 1 else 's'}"


def _reveal(path: str) -> None:
    import subprocess
    import sys
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


# ── entry point ──────────────────────────────────────────────────────────────

_window: VramInspector | None = None


def show() -> None:
    """Show (or raise) the VRAM Inspector. Safe from any thread."""
    gui_dispatch(_show)


def _show() -> None:
    global _window
    if _window is not None:
        try:
            if _window.isVisible():
                _window.raise_()
                _window.activateWindow()
                return
            _window.show()
            _window.rescan()
            return
        except RuntimeError:
            _window = None  # its C++ side is gone
    _window = VramInspector(parent=main_window())
    _window.show()
