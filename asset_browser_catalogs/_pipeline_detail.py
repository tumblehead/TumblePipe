"""Detail-panel section builders for the Pipeline catalog.

Every widget that appears in the right-hand detail panel — the
combined info section, the per-asset / per-shot / per-container info
sections, the department deck with its inline version dropdowns and
context menu, the membership pills, the todo list, the asset-actions
row — is constructed by a method on :class:`DetailSectionBuilder`.

The catalog's :meth:`get_detail_sections` returns ``DetailSection``
entries whose ``widget_factory`` points at one of these methods.

Like :class:`WorkfileManager`, the builder holds a back-reference to
the catalog because every section reads multiple catalog services
(asset resolver, container parser, workfile manager for user / mtime
labels, dept-version overrides, membership lookups, etc.). The win
of this module is file separation — the 1100 LoC of Qt widget code
no longer dominates the catalog file.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _pipeline_catalog import PipelineCatalog

log = logging.getLogger(__name__)


class DetailSectionBuilder:
    """Qt widget construction for the Pipeline catalog's detail panel."""

    def __init__(self, catalog: "PipelineCatalog") -> None:
        self._catalog = catalog

    @staticmethod
    def mix_hex(base_hex: str, accent_hex: str, ratio: float) -> str:
        """Linearly blend two ``#rrggbb`` colors. ``ratio`` is the share
        of ``accent_hex`` in the result (0..1)."""
        try:
            def _hx(s: str) -> tuple[int, int, int]:
                s = s.lstrip("#")
                return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
            br, bg, bb = _hx(base_hex)
            ar, ag, ab = _hx(accent_hex)
            r = int(br + (ar - br) * ratio)
            g = int(bg + (ag - bg) * ratio)
            b = int(bb + (ab - bb) * ratio)
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return base_hex

    def build_combined_info_section(self, ctx: DetailContext):
        """Info tab content — identity breadcrumb, filesystem path,
        the per-kind info table, and the description paragraph.

        Action buttons live in the DetailPanel's sticky bottom bar now;
        this section is purely descriptive.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
        detail = ctx.detail

        holder = QWidget()
        vbox = QVBoxLayout(holder)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        # Identity breadcrumb (URI) and filesystem path — uniform across
        # all asset kinds where the data is resolvable.
        breadcrumb = self.build_identity_breadcrumb(detail)
        if breadcrumb is not None:
            vbox.addWidget(breadcrumb)
        path_row = self.build_path_row(detail)
        if path_row is not None:
            vbox.addWidget(path_row)

        # Per-kind info table.
        if "type:asset" in detail.tags:
            info_w = self.build_asset_info_section(ctx)
        elif "type:shot" in detail.tags:
            info_w = self.build_shot_info_section(ctx)
        else:
            info_w = None
        if info_w is not None:
            vbox.addWidget(info_w)

        # Description (plain paragraph) — last so the structured
        # metadata above is the first thing the eye lands on.
        if detail.description:
            desc = QLabel(detail.description)
            desc.setWordWrap(True)
            desc.setTextInteractionFlags(Qt.TextSelectableByMouse)
            vbox.addWidget(desc)

        vbox.addStretch(1)
        return holder

    def build_identity_breadcrumb(self, detail):
        """Render the entity URI as a copyable monospaced breadcrumb,
        or ``None`` if no URI is resolvable for this detail kind.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QHBoxLayout, QLabel, QPushButton, QWidget,
        )
        from asset_browser.core.icons import icon as make_icon
        from asset_browser.core.theme import (
            BG_MID, BORDER, FONT_SMALL, TEXT_DIM, TEXT_SECONDARY, scaled,
        )

        segs = self.resolve_identity_segments(detail)
        if not segs:
            return None
        text = " / ".join(segs)

        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(scaled(4))

        lbl = QLabel(text)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl.setStyleSheet(
            f"font-family: Consolas, 'Courier New', monospace; "
            f"font-size: {FONT_SMALL}px; color: {TEXT_SECONDARY}; "
            f"background: {BG_MID}; border: 1px solid {BORDER}; "
            f"border-radius: {scaled(3)}px; padding: 2px 6px;"
        )
        lbl.setWordWrap(True)
        row.addWidget(lbl, stretch=1)

        copy_btn = QPushButton()
        copy_btn.setFixedSize(scaled(20), scaled(20))
        copy_btn.setIcon(make_icon("copy", scaled(12), TEXT_DIM))
        copy_btn.setToolTip("Copy URI to clipboard")
        copy_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
            f"QPushButton:hover {{ background: {BG_MID}; "
            f"border-radius: {scaled(3)}px; }}"
        )
        copy_btn.clicked.connect(
            lambda _checked=False, t=text: self.copy_to_clipboard(t)
        )
        row.addWidget(copy_btn)
        return w

    def build_path_row(self, detail):
        """Render the export-folder path as a click-to-copy
        monospaced row, or ``None`` if there is no resolvable path.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QHBoxLayout, QLabel, QPushButton, QWidget,
        )
        from asset_browser.core.icons import icon as make_icon
        from asset_browser.core.theme import (
            BG_MID, BORDER, FONT_TINY, TEXT_DIM, scaled,
        )

        try:
            path = self._catalog._resolve_export_path(detail.id if detail else "")
        except Exception:
            log.debug("export-path resolution failed", exc_info=True)
            return None
        if not path:
            return None

        text = str(path)
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(scaled(4))

        lbl = QLabel(text)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl.setStyleSheet(
            f"font-family: Consolas, 'Courier New', monospace; "
            f"font-size: {FONT_TINY}px; color: {TEXT_DIM}; "
            f"background: transparent; border: none;"
        )
        lbl.setWordWrap(True)
        lbl.setToolTip(text)
        row.addWidget(lbl, stretch=1)

        copy_btn = QPushButton()
        copy_btn.setFixedSize(scaled(20), scaled(20))
        copy_btn.setIcon(make_icon("copy", scaled(12), TEXT_DIM))
        copy_btn.setToolTip("Copy path to clipboard")
        copy_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
            f"QPushButton:hover {{ background: {BG_MID}; "
            f"border-radius: {scaled(3)}px; }}"
        )
        copy_btn.clicked.connect(
            lambda _checked=False, t=text: self.copy_to_clipboard(t)
        )
        row.addWidget(copy_btn)
        return w

    def resolve_identity_segments(self, detail):
        """Return the breadcrumb segments for an asset's URI, or
        ``None`` when no identity is resolvable.

        Splits ``detail.id`` (``project/CHAR/Baby`` form) and appends
        variants if present.
        """
        meta = detail.metadata or {}
        parts = (detail.id or "").split("/")
        parts = [p for p in parts if p]
        if not parts:
            return None
        variants = meta.get("variants") or []
        if "type:asset" in detail.tags and variants:
            return parts + ["/".join(variants)] if len(variants) > 1 \
                else parts + [str(variants[0])]
        return parts

    def copy_to_clipboard(self, text: str) -> None:
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
        except Exception:
            log.debug("clipboard copy failed", exc_info=True)

    def build_container_info_section(self, ctx: DetailContext):
        """Info tab content for a Group/Scene detail.

        Shows identity (project / context / path) and member count.
        For groups, the editable departments toggle lives on its own
        Departments tab (see :meth:`_build_group_departments_section`).
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QGridLayout,
            QLabel,
            QVBoxLayout,
            QWidget,
        )

        detail = ctx.detail
        meta = detail.metadata or {}
        kind = meta.get("kind", "")
        proj_name = meta.get("project", "")
        path = meta.get("path", "")
        member_count = int(meta.get("member_count") or 0)
        ctx_label = meta.get("context", "")

        holder = QWidget()
        vbox = QVBoxLayout(holder)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(10)

        # Identity grid — project / context / path / members
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        row = 0

        def _row(label: str, value: str) -> None:
            nonlocal row
            k = QLabel(label)
            k.setStyleSheet("color: #888;")
            v = QLabel(value)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(k, row, 0, Qt.AlignTop)
            grid.addWidget(v, row, 1)
            row += 1

        # Display label decouples from internal ``kind`` slug — the
        # data model still calls these "groups", but the UI surfaces
        # them as "Multi" since the term reads more concretely.
        _KIND_DISPLAY = {"group": "Multi", "scene": "Root"}
        if proj_name:
            _row("Project", proj_name)
        if kind:
            _row("Type", _KIND_DISPLAY.get(kind, kind.capitalize()))
        if ctx_label and kind == "group":
            _row("Context", ctx_label.capitalize())
        if path:
            _row("Path", path)
        unit = "asset" if kind == "scene" else "member"
        _row("Members", f"{member_count} {unit}{'' if member_count == 1 else 's'}")

        vbox.addLayout(grid)

        # Per-dept "Open" buttons — groups only. The deck on the
        # group card surfaces the same actions, but the buttons here
        # are more discoverable for users who haven't found the deck
        # expand affordance yet.
        group_depts = list(meta.get("departments") or ())
        if kind == "group" and group_depts:
            from PySide6.QtWidgets import (
                QHBoxLayout,
                QLabel,
                QPushButton,
            )
            heading = QLabel("Work scenes")
            heading.setStyleSheet("color: #aaa; font-weight: bold;")
            vbox.addWidget(heading)
            btn_row = QHBoxLayout()
            btn_row.setSpacing(6)
            for dept_name in group_depts:
                short = DEPT_SHORT_NAMES.get(
                    dept_name, dept_name.title(),
                )
                btn = QPushButton(f"Open {short}")
                btn.setToolTip(
                    f"Open the latest {dept_name} workfile for this Multi",
                )
                btn.clicked.connect(
                    lambda checked=False, d=dept_name:
                        self.execute_action(
                            f"open_workfile:{d}", detail,
                        )
                )
                btn_row.addWidget(btn)
            btn_row.addStretch(1)
            vbox.addLayout(btn_row)

        vbox.addStretch(1)
        return holder

    def build_group_departments_section(self, ctx: DetailContext):
        """Departments tab content for a Group detail.

        Inline checkbox list mirroring the dialog-based Edit flow's
        ``departments`` multi-select. Commits via :meth:`edit_collection`.
        """
        from PySide6.QtWidgets import (
            QCheckBox,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QVBoxLayout,
            QWidget,
        )

        detail = ctx.detail
        meta = detail.metadata or {}
        depts_current = list(meta.get("departments") or ())
        known_depts = list(meta.get("known_departments") or ())

        holder = QWidget()
        vbox = QVBoxLayout(holder)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        if not known_depts:
            empty = QLabel("(no departments defined for this context)")
            empty.setStyleSheet("color: #666;")
            vbox.addWidget(empty)
            vbox.addStretch(1)
            return holder

        boxes_holder = QWidget()
        boxes_lyt = QVBoxLayout(boxes_holder)
        boxes_lyt.setContentsMargins(0, 0, 0, 0)
        boxes_lyt.setSpacing(2)
        current_set = {str(d) for d in depts_current}
        boxes: list[QCheckBox] = []
        for d in known_depts:
            cb = QCheckBox(d)
            cb.setChecked(d in current_set)
            boxes_lyt.addWidget(cb)
            boxes.append(cb)
        vbox.addWidget(boxes_holder)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        apply_btn = QPushButton("Apply")
        btn_row.addWidget(apply_btn)
        vbox.addLayout(btn_row)

        cid = detail.id  # container id == tag

        def _apply() -> None:
            selected = tuple(
                cb.text() for cb in boxes if cb.isChecked()
            )
            try:
                ok = bool(self.edit_collection(
                    cid, {"departments": selected},
                ))
            except Exception:
                log.exception("edit_collection failed for %s", cid)
                ok = False
            if ok and ctx.refresh_detail is not None:
                try:
                    ctx.refresh_detail()
                except Exception:
                    log.exception("refresh_detail failed")

        apply_btn.clicked.connect(_apply)
        vbox.addStretch(1)
        return holder

    def build_combined_departments_section(self, ctx: DetailContext):
        """Departments tab content — Multi / Root membership pills on
        top (a shot's Root-ref is conceptually the root layer of its
        department stack), then the departments grid below.

        Wrapped defensively: if either sub-builder throws, we still
        return a non-empty widget so the detail panel keeps the
        Departments tab visible.
        """
        from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

        holder = QWidget()
        vbox = QVBoxLayout(holder)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        try:
            mem_w = self.build_membership_section(ctx)
        except Exception:
            log.exception("Membership section build failed")
            mem_w = None
        if mem_w is not None:
            vbox.addWidget(mem_w)

        try:
            depts_w = self.build_departments_section(ctx)
        except Exception:
            log.exception(
                "Departments section build failed for %s", ctx.detail.id,
            )
            depts_w = None
        if depts_w is not None:
            vbox.addWidget(depts_w)
        else:
            err = QLabel(
                "(failed to load departments — see log for traceback)"
            )
            err.setStyleSheet("color: #f06060;")
            err.setWordWrap(True)
            vbox.addWidget(err)

        vbox.addStretch(1)
        return holder

    def build_asset_info_section(self, ctx: DetailContext):
        detail = ctx.detail
        meta = detail.metadata or {}
        rows: list[tuple[str, str]] = []
        if meta.get("project"):
            rows.append(("Project", str(meta["project"])))
        if meta.get("category"):
            rows.append(("Category", str(meta["category"])))
        variants = meta.get("variants") or []
        if variants:
            rows.append(("Variants", ", ".join(variants)))
        if detail.versions:
            latest = detail.versions[-1]
            rows.append(("Latest", getattr(latest, "version", "") or ""))
        return self.build_info_table(rows)

    def build_shot_info_section(self, ctx: DetailContext):
        meta = ctx.detail.metadata or {}
        rows: list[tuple[str, str]] = []
        if meta.get("project"):
            rows.append(("Project", str(meta["project"])))
        if meta.get("sequence"):
            rows.append(("Sequence", str(meta["sequence"])))
        fs = meta.get("frame_start")
        fe = meta.get("frame_end")
        ft = meta.get("frame_total")
        if fs is not None:
            rows.append(("Frame Start", str(fs)))
        if fe is not None:
            rows.append(("Frame End", str(fe)))
        if ft is not None:
            rows.append(("Frame Total", str(ft)))
        if meta.get("fps") is not None:
            rows.append(("FPS", str(meta["fps"])))

        # Scene reference — resolved (including sequence inheritance).
        scene_label, inherited = self.shot_scene_label(ctx.detail.id)
        if scene_label:
            suffix = " (inherited)" if inherited else ""
            rows.append(("Root", f"{scene_label}{suffix}"))
        return self.build_info_table(rows)

    def shot_scene_label(self, asset_id: str) -> tuple[str, bool]:
        """Return ``(label, inherited)`` for the scene currently
        attached to a shot, or ``("", False)`` if none.

        ``inherited`` is True when the scene_ref is inherited from the
        parent sequence rather than set on the shot itself.
        """
        try:
            parts = asset_id.split("/", 1)
            if len(parts) != 2:
                return ("", False)
            project_name = parts[0]
            proj = self._catalog._registry.get(project_name)
            if proj is None or not self._catalog._clients.is_ready(project_name):
                return ("", False)
            self._catalog._activate_project(proj)
            from tumblepipe.config import scene as scene_mod
            shot_uri = self._catalog._resolver.uri_for(asset_id)
            if shot_uri is None:
                return ("", False)
            direct = scene_mod.get_scene_ref(shot_uri)
            if direct is not None:
                scene_uri = direct
                inherited = False
            else:
                resolved, _src = scene_mod.get_inherited_scene_ref(shot_uri)
                if resolved is None:
                    return ("", False)
                scene_uri = resolved
                inherited = True
            segs = getattr(scene_uri, "segments", None)
            label = (
                "/".join(segs) if segs else str(scene_uri)
            )
            return (label, inherited)
        except Exception:
            log.debug(
                "Failed to read scene_ref for %s", asset_id, exc_info=True,
            )
            return ("", False)

    def build_info_table(self, rows: list[tuple[str, str]]):
        """Build a label/value grid section box from a list of rows.
        Returns ``None`` when there are no rows."""
        from PySide6.QtWidgets import QGridLayout, QLabel
        from PySide6.QtCore import Qt
        from asset_browser.ui.detail_panel import make_section_box
        from asset_browser.core.theme import (
            FONT_FAMILY, FONT_SMALL, TEXT_DIM, TEXT_SECONDARY, scaled,
        )

        if not rows:
            return None

        w, lay = make_section_box()
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(scaled(8))
        grid.setVerticalSpacing(scaled(2))
        label_style = (
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_DIM}; border: none; background: transparent;"
        )
        value_style = (
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_SECONDARY}; border: none; background: transparent;"
        )
        for row, (label, value) in enumerate(rows):
            key_lbl = QLabel(label)
            key_lbl.setStyleSheet(label_style)
            key_lbl.setAlignment(Qt.AlignRight | Qt.AlignTop)
            grid.addWidget(key_lbl, row, 0)
            val_lbl = QLabel(value)
            val_lbl.setStyleSheet(value_style)
            val_lbl.setWordWrap(True)
            grid.addWidget(val_lbl, row, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        return w

    def build_membership_section(self, ctx: DetailContext):
        """Show pills for every group and scene this asset belongs to."""
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout
        from PySide6.QtCore import Qt
        from asset_browser.ui.detail_panel import make_section_box
        from asset_browser.core.theme import (
            ACCENT, BG_MID, BORDER, FONT_FAMILY, FONT_SMALL,
            TEXT_DIM, TEXT_SECONDARY,
        )

        memberships = self._catalog.get_asset_membership(ctx.detail.id)
        if not memberships:
            return None

        w, lay = make_section_box()
        flow = QHBoxLayout()
        flow.setContentsMargins(0, 0, 0, 0)
        flow.setSpacing(4)

        pill_style = (
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_SECONDARY}; background-color: {BG_MID}; "
            f"border: 1px solid {BORDER}; border-radius: 10px; "
            f"padding: 2px 8px;"
        )
        for _cid, label, kind in memberships:
            prefix = "M" if kind == "group" else "R"
            pill = QLabel(f"{prefix}  {label}")
            pill.setStyleSheet(pill_style)
            pill.setToolTip(f"{'Multi' if kind == 'group' else 'Root'}: {label}")
            flow.addWidget(pill)
        flow.addStretch(1)
        lay.addLayout(flow)
        return w

    def build_todos_section(self, ctx: DetailContext):
        """List the asset's todos with toggleable checkboxes + an inline
        add row and a "clear all" button.
        """
        from PySide6.QtWidgets import (
            QHBoxLayout, QLabel, QLineEdit, QPushButton,
        )
        from PySide6.QtCore import Qt
        from asset_browser.ui.detail_panel import make_section_box
        from asset_browser.ui.lucide_checkbox import LucideCheckBox
        from asset_browser.core.icons import icon
        from asset_browser.core.theme import (
            ACCENT, BG_DARK, BORDER, FONT_BODY, FONT_FAMILY,
            FONT_SMALL, TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY,
        )

        try:
            import asset_browser
            mgr = asset_browser.get_todos()
        except Exception:
            mgr = None
        if mgr is None:
            return None
        self._catalog._hook_todo_refresh(mgr)

        asset_id = ctx.detail.id
        todos = mgr.todos(self.id, asset_id)
        w, lay = make_section_box()

        _flat_btn = (
            "QPushButton { background: transparent; border: none; }"
            "QPushButton:hover { background-color: rgba(255,255,255,24);"
            "                    border-radius: 3px; }"
        )

        if not todos:
            empty = QLabel("No tasks.")
            empty.setStyleSheet(
                f'color: {TEXT_DIM}; font-family: "{FONT_FAMILY}"; '
                f"font-size: {FONT_SMALL}px; background: transparent;"
            )
            lay.addWidget(empty)
        else:
            for idx, todo in enumerate(todos):
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(6)
                cb = LucideCheckBox(todo.get("text", ""))
                cb.setChecked(bool(todo.get("done")))
                cb.setTextColor(TEXT_DIM if todo.get("done") else TEXT_PRIMARY)
                cb.toggled.connect(
                    lambda done, i=idx: mgr.set_done(
                        self.id, asset_id, i, done,
                    )
                )
                row.addWidget(cb, stretch=1)
                rm = QPushButton()
                rm.setIcon(icon("x", 12, TEXT_DIM))
                rm.setFlat(True)
                rm.setFixedSize(20, 20)
                rm.setCursor(Qt.PointingHandCursor)
                rm.setStyleSheet(_flat_btn)
                rm.clicked.connect(
                    lambda checked=False, i=idx: mgr.remove(
                        self.id, asset_id, i,
                    )
                )
                row.addWidget(rm)
                lay.addLayout(row)

        # Footer: add-new input + clear-all button
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 6, 0, 0)
        footer.setSpacing(4)
        add_input = QLineEdit()
        add_input.setPlaceholderText("Add task…")
        add_input.setStyleSheet(
            f"QLineEdit {{"
            f"  background-color: {BG_DARK};"
            f"  color: {TEXT_PRIMARY};"
            f"  border: 1px solid {BORDER};"
            f"  border-radius: 3px;"
            f"  padding: 2px 6px;"
            f'  font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px;'
            f"}}"
            f"QLineEdit:focus {{ border-color: {ACCENT}; }}"
        )

        def _do_add():
            text = add_input.text().strip()
            if not text:
                return
            add_input.clear()
            mgr.add(self.id, asset_id, text)

        add_input.returnPressed.connect(_do_add)
        footer.addWidget(add_input, stretch=1)

        add_btn = QPushButton()
        add_btn.setIcon(icon("plus", 14, TEXT_SECONDARY))
        add_btn.setFlat(True)
        add_btn.setFixedSize(22, 22)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setToolTip("Add task")
        add_btn.setStyleSheet(_flat_btn)
        add_btn.clicked.connect(_do_add)
        footer.addWidget(add_btn)

        if todos:
            from PySide6.QtWidgets import QMenu
            from asset_browser.core.theme import MENU_STYLE
            clear_btn = QPushButton()
            clear_btn.setIcon(icon("brush-cleaning", 14, TEXT_SECONDARY))
            clear_btn.setFlat(True)
            clear_btn.setFixedSize(22, 22)
            clear_btn.setCursor(Qt.PointingHandCursor)
            clear_btn.setToolTip("Clear tasks")
            clear_btn.setStyleSheet(_flat_btn)

            def _show_clear_menu():
                m = QMenu(clear_btn)
                m.setStyleSheet(MENU_STYLE)
                a_done = m.addAction("Clear completed")
                a_all = m.addAction("Clear all")
                chosen = m.exec(
                    clear_btn.mapToGlobal(
                        clear_btn.rect().bottomLeft()
                    )
                )
                if chosen is a_done:
                    mgr.clear_completed(self.id, asset_id)
                elif chosen is a_all:
                    mgr.clear(self.id, asset_id)

            clear_btn.clicked.connect(_show_clear_menu)
            footer.addWidget(clear_btn)

        lay.addLayout(footer)
        return w

    def build_departments_section(self, ctx: DetailContext):
        from PySide6.QtWidgets import (
            QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
            QVBoxLayout,
        )
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
        from asset_browser.ui.detail_panel import make_section_box
        from asset_browser.core.theme import (
            ACCENT, BG_DARK, BG_MID, BORDER, BUTTON_GHOST_STYLE, COMBO_STYLE,
            FONT_FAMILY, FONT_SMALL, TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY,
            scaled,
        )
        from _pipeline_widgets import DeptMetaLabel, DeptNameLabel

        # Pre-render the meta-row Lucide icons (clock for "edited", package
        # for "exported") to base64 PNG data URIs so QLabel rich text can
        # render them inline via <img> without writing cache files.
        def _icon_html(name: str, size: int = 11) -> str:
            try:
                import base64
                from asset_browser.core.icons import icon_pixmap
                pix = icon_pixmap(name, scaled(size), TEXT_DIM)
                arr = QByteArray()
                buf = QBuffer(arr)
                buf.open(QIODevice.WriteOnly)
                pix.save(buf, "PNG")
                b64 = base64.b64encode(bytes(arr)).decode("ascii")
                return (
                    f'<img src="data:image/png;base64,{b64}" '
                    f'width="{size}" height="{size}"/>'
                )
            except Exception:
                return ""
        _user_html = _icon_html("user-round")
        _edited_html = _icon_html("clock")
        _exported_html = _icon_html("upload")
        # Approximate width of an inline icon (icon px + leading space) used
        # for measurement when deciding which candidate to render.
        _icon_w = scaled(13)

        asset_id = ctx.detail.id
        dept_info: dict = (ctx.detail.metadata or {}).get("departments", {})
        is_shot = "type:shot" in ctx.detail.tags
        is_multi = "type:group" in ctx.detail.tags
        # Multis carry their context (``shots`` / ``assets``) on the
        # detail metadata; uncovered depts still render as "missing"
        # rows so the user can toggle them on.
        if is_multi:
            ent_ctx = (
                (ctx.detail.metadata or {}).get("context") or "shots"
            )
        else:
            ent_ctx = "shots" if is_shot else "assets"
        all_depts = self._catalog._list_entity_departments(ent_ctx)
        dept_shorts = self._catalog._list_entity_dept_shorts(ent_ctx)
        overrides = self._catalog._dept_versions.get(asset_id)
        scene_dv = self._catalog._scene.get_scene_dept_version(asset_id)
        active_dept = scene_dv[0] if scene_dv else None
        active_version = scene_dv[1] if scene_dv else None
        # Coverage toggle state for Multis: depts the user has flagged
        # this Multi to override. Uncovered depts render as missing
        # rows with an unchecked toggle.
        covered_set: set[str] = set(
            (ctx.detail.metadata or {}).get("covered_departments", [])
        ) if is_multi else set()

        w, lay = make_section_box()

        if not all_depts:
            empty = QLabel("No departments found.")
            empty.setStyleSheet(
                f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
                f"color: {TEXT_DIM}; border: none; background: transparent;"
            )
            lay.addWidget(empty)
            return w

        rows_box = QVBoxLayout()
        rows_box.setContentsMargins(0, 0, 0, 0)
        rows_box.setSpacing(0)

        # Row background colors:
        #   - active row: solid accent tint
        #   - even rows : slightly lighter than the box background
        #   - odd rows  : transparent (matches the box background)
        even_bg = BG_DARK
        odd_bg = "transparent"
        active_bg = self.mix_hex(BG_MID, ACCENT, 0.35)

        name_style = (
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_SECONDARY}; border: none; background: transparent;"
        )
        active_name_style = (
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {ACCENT}; font-weight: bold; "
            f"border: none; background: transparent;"
        )
        missing_style = (
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_DIM}; border: none; background: transparent; "
            f"font-style: italic;"
        )
        user_style = (
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_DIM}; border: none; background: transparent;"
        )

        for row, dept_name in enumerate(all_depts):
            versions = dept_info.get(dept_name) or []
            available = bool(versions)
            is_active = dept_name == active_dept

            row_bg = active_bg if is_active else (
                even_bg if (row % 2 == 0) else odd_bg
            )

            row_frame = QFrame()
            row_frame.setObjectName("deptRow")
            row_frame.setStyleSheet(
                f"QFrame#deptRow {{ background: {row_bg}; "
                f"border: none; border-radius: 3px; }}"
            )
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(scaled(4), scaled(2), scaled(4), scaled(2))
            row_layout.setSpacing(scaled(8))

            # Multi-only column: per-dept override toggle. Checked
            # means the Multi covers this dept (its workfile takes
            # precedence over individual member workfiles); unchecked
            # means the dept falls through to per-member workfiles.
            if is_multi:
                from PySide6.QtWidgets import QCheckBox
                toggle = QCheckBox()
                toggle.setChecked(dept_name in covered_set)
                toggle.setToolTip(
                    f"Override {dept_name} for all members of this Multi"
                )
                toggle.toggled.connect(
                    lambda checked, gid=asset_id, dn=dept_name:
                        self._catalog._containers._toggle_group_dept_coverage(
                            gid, dn, bool(checked),
                        )
                )
                row_layout.addWidget(toggle)

            # Col 0: dept name (full → short → ellided as the column shrinks)
            name_lbl = DeptNameLabel(
                floor_width=scaled(28),
                short_padding=scaled(4),
                sizehint_padding=scaled(2),
            )
            if is_active:
                name_lbl.setStyleSheet(active_name_style)
            else:
                name_lbl.setStyleSheet(name_style if available else missing_style)
            short_name = dept_shorts.get(dept_name, "")
            name_lbl.setFullText(
                dept_name.title(),
                short=short_name.title() if short_name else "",
            )
            row_layout.addWidget(name_lbl)

            # Col 1: user · time-ago — right-aligned, drops user then date
            # as the row narrows (see DeptMetaLabel).
            user_lbl = DeptMetaLabel(
                user_html=_user_html,
                edited_html=_edited_html,
                exported_html=_exported_html,
                icon_width=_icon_w,
                sizehint_padding=scaled(2),
            )
            user_lbl.setStyleSheet(user_style)
            user_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row_layout.addWidget(user_lbl, stretch=1)

            combo: QComboBox | None = None
            if available:
                ordered = list(reversed(versions))  # newest first
                # Default selection priority:
                #   1. previously chosen override
                #   2. version of the loaded scene (if this row is active)
                #   3. newest version
                if dept_name in overrides:
                    current = overrides[dept_name]
                elif is_active and active_version and active_version in versions:
                    current = active_version
                else:
                    current = ordered[0]

                # Col 2: version dropdown.
                #
                # We write a SINGLE stylesheet here (not COMBO_STYLE +
                # overrides) because Qt's QSS engine doesn't reliably
                # merge ``image:`` from a base ::down-arrow rule with a
                # later one — the chevron disappears. Inlining the
                # chevron PNG keeps it visible.
                #
                # Width is hardcoded based on font metrics for "v0001"
                # plus a stable chrome budget — Qt's pre-paint font
                # metrics weren't reliable enough for the combo to
                # measure itself.
                combo = QComboBox()
                combo.setStyleSheet(f"""
                    QComboBox {{
                        background-color: {BG_DARK};
                        border: 1px solid {BORDER};
                        border-radius: 4px;
                        padding: 4px 16px 4px 6px;
                        color: {TEXT_PRIMARY};
                        font-family: "{FONT_FAMILY}";
                        font-size: {FONT_SMALL}px;
                    }}
                    QComboBox:hover {{
                        border-color: {ACCENT};
                    }}
                    QComboBox::drop-down {{
                        subcontrol-origin: padding;
                        subcontrol-position: right center;
                        border: none;
                        width: 14px;
                    }}
                    QComboBox::down-arrow {{
                        image: none;
                        width: 10px;
                        height: 10px;
                        margin-right: 2px;
                    }}
                    QComboBox QAbstractItemView {{
                        background-color: {BG_DARK};
                        border: 1px solid {BORDER};
                        color: {TEXT_PRIMARY};
                        selection-background-color: {ACCENT};
                        selection-color: white;
                    }}
                """)
                for ver in ordered:
                    combo.addItem(ver, ver)
                idx = combo.findData(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                # Hardcoded fixed width that comfortably fits a "v0001"-
                # style label plus the 14px chevron + padding + borders at
                # FONT_SMALL = 12px. We avoid measurement-based sizing
                # because Qt's pre-paint QFontMetrics gives unreliable
                # results that have been collapsing this combo to ~25px.
                combo.setFixedWidth(scaled(72))
                combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                row_layout.addWidget(combo)

                # Col 3: open icon button — saves space vs the "Open" label
                # so narrow rows don't force the section box past the panel.
                from asset_browser.core.icons import icon as make_icon
                open_btn = QPushButton()
                open_btn.setIcon(make_icon("play", scaled(14), ACCENT))
                open_btn.setIconSize(QSize(scaled(14), scaled(14)))
                open_btn.setFixedSize(scaled(28), scaled(24))
                open_btn.setStyleSheet(BUTTON_GHOST_STYLE)
                open_btn.setToolTip("Open this version")
                open_btn.clicked.connect(
                    lambda _checked=False, c=combo, dn=dept_name,
                    aid=asset_id, rd=ctx.refresh_detail:
                        self._catalog._workfiles.open_version_now(
                            aid, dn, c.currentData(), rd,
                        )
                )
                row_layout.addWidget(open_btn)

                # Compute the latest export age once per row — it doesn't
                # depend on which version is selected (the export folder
                # tracks the latest export for the dept).
                exported_age = self._catalog._workfiles.format_relative_time(
                    self._catalog._workfiles.get_latest_export_mtime(asset_id, dept_name)
                )

                # Initial fill + live update on combo change
                user_lbl.set_parts(
                    self._catalog._workfiles.get_user_for_version(asset_id, dept_name, current) or "",
                    self._catalog._workfiles.format_relative_time(
                        self._catalog._workfiles.get_mtime_for_version(asset_id, dept_name, current)
                    ),
                    exported=exported_age,
                )

                def _on_change(
                    _idx,
                    c=combo, dn=dept_name, ulbl=user_lbl, aid=asset_id,
                    exp=exported_age,
                ):
                    v = c.currentData()
                    self.on_dept_version_picked(aid, dn, v)
                    ulbl.set_parts(
                        self._catalog._workfiles.get_user_for_version(aid, dn, v) or "",
                        self._catalog._workfiles.format_relative_time(
                            self._catalog._workfiles.get_mtime_for_version(aid, dn, v)
                        ),
                        exported=exp,
                    )

                combo.currentIndexChanged.connect(_on_change)
            else:
                dash = QLabel("—")
                dash.setStyleSheet(missing_style)
                dash.setMinimumWidth(scaled(56))
                dash.setAlignment(Qt.AlignCenter)
                row_layout.addWidget(dash)

                # Match the Open button's icon style — clicking creates
                # v0001 from the dept template, which is intuitive enough
                # that the green play icon doubles for "create + open".
                from asset_browser.core.icons import icon as make_icon
                create_btn = QPushButton()
                create_btn.setIcon(make_icon("play", scaled(14), ACCENT))
                create_btn.setIconSize(QSize(scaled(14), scaled(14)))
                create_btn.setFixedSize(scaled(28), scaled(24))
                create_btn.setStyleSheet(BUTTON_GHOST_STYLE)
                create_btn.setToolTip(
                    f"Create {dept_name}/v0001 from the dept template."
                )
                create_btn.clicked.connect(
                    lambda _checked=False, dn=dept_name,
                    aid=asset_id, rd=ctx.refresh_detail:
                        self._catalog._workfiles.new_from_template(aid, dn, rd)
                )
                row_layout.addWidget(create_btn)

            # ── Row-wide right-click context menu ──
            # Connect on the frame and the two labels (combo + button
            # have their own click semantics and stay exempt).
            menu_targets = [row_frame, name_lbl, user_lbl]
            for target in menu_targets:
                target.setContextMenuPolicy(Qt.CustomContextMenu)
                target.customContextMenuRequested.connect(
                    lambda pos, src=target, dn=dept_name, av=available,
                    ia=is_active, c=combo, aid=asset_id,
                    rd=ctx.refresh_detail:
                        self.show_dept_context_menu(
                            aid, dn, av, ia, c, src, rd, pos,
                        )
                )

            rows_box.addWidget(row_frame)

        lay.addLayout(rows_box)
        return w

    @staticmethod
    def build_asset_actions_section(self, ctx: DetailContext):
        """Save / Publish / Refresh — always operate on the loaded scene.

        The header (entity / dept / version) lives outside this box —
        it's set as the section ``title`` in :meth:`get_detail_sections`.
        """
        from PySide6.QtWidgets import QHBoxLayout, QPushButton
        from asset_browser.ui.detail_panel import make_section_box
        from asset_browser.core.theme import (
            BUTTON_GHOST_STYLE, BUTTON_PRIMARY_STYLE, scaled,
        )

        scene_ctx = self._catalog._scene.get_loaded_scene_context()
        has_ctx = scene_ctx is not None

        w, lay = make_section_box()

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(scaled(4))

        save_btn = QPushButton("Save")
        save_btn.setStyleSheet(BUTTON_PRIMARY_STYLE)
        save_btn.setEnabled(has_ctx)
        if not has_ctx:
            save_btn.setToolTip(
                "The loaded scene has no pipeline context."
            )
        save_btn.clicked.connect(
            lambda _checked=False, rd=ctx.refresh_detail:
                self._catalog._scene.save_current_scene(rd)
        )
        btn_row.addWidget(save_btn)

        pub_btn = QPushButton("Publish")
        pub_btn.setStyleSheet(BUTTON_PRIMARY_STYLE)
        pub_btn.setEnabled(has_ctx)
        if not has_ctx:
            pub_btn.setToolTip(
                "The loaded scene has no pipeline context."
            )
        pub_btn.clicked.connect(
            lambda _checked=False, rd=ctx.refresh_detail:
                self._catalog._scene.publish_current_scene(rd)
        )
        btn_row.addWidget(pub_btn)
        # Note: this detail-panel section is currently dead code (the
        # asset browser renders the Save/Publish buttons via the
        # quick-actions toolbar instead — see get_quick_actions +
        # get_quick_action_hover). The hover-info wiring for those
        # lives in the shared asset_browser/core/hover_info.py path.

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setStyleSheet(BUTTON_GHOST_STYLE)
        refresh_btn.setToolTip(
            "Re-scan the selected asset and reload the details panel."
        )
        refresh_btn.clicked.connect(
            lambda _checked=False, aid=ctx.detail.id, rd=ctx.refresh_detail:
                self._catalog._scene.refresh_asset(aid, rd)
        )
        btn_row.addWidget(refresh_btn)

        lay.addLayout(btn_row)
        return w

    def on_dept_version_picked(
        self, asset_id: str, dept: str, version: str | None,
    ) -> None:
        if not version:
            return
        self._catalog._dept_versions.set(asset_id, dept, version)
        log.debug(
            "Pipeline: dept version override %s/%s -> %s",
            asset_id, dept, version,
        )

    def show_dept_context_menu(
        self, asset_id: str, dept: str, available: bool, is_active: bool,
        combo, source_widget, refresh_cb, pos=None,
    ) -> None:
        """Right-click menu for a dept row in the details panel.

        ``pos`` is the click position in ``source_widget`` coordinates;
        the menu pops at the cursor (mapped to global). Falls back to
        the widget's bottom-left when ``pos`` is None.
        """
        from PySide6.QtWidgets import QMenu
        from asset_browser.core.theme import MENU_STYLE

        menu = QMenu(source_widget)
        menu.setStyleSheet(MENU_STYLE)

        if available and combo is not None:
            ver = combo.currentData()
            open_act = menu.addAction(f"Open {ver}" if ver else "Open")
            open_act.triggered.connect(
                lambda _checked=False:
                    self._catalog._workfiles.open_version_now(asset_id, dept, ver, refresh_cb)
            )

        loc_act = menu.addAction("Open Location")
        loc_act.triggered.connect(
            lambda _checked=False: self._catalog._workfiles.open_dept_work_dir(asset_id, dept)
        )

        export_act = menu.addAction("View Latest Export")
        export_act.triggered.connect(
            lambda _checked=False: self._catalog._workfiles.open_latest_export(asset_id, dept)
        )

        if available:
            new_inst_act = menu.addAction("Open in New Houdini")
            new_inst_act.triggered.connect(
                lambda _checked=False:
                    self._catalog._workfiles.open_in_new_instance(asset_id, dept)
            )

        if is_active:
            menu.addSeparator()
            reload_act = menu.addAction("Reload Scene")
            reload_act.triggered.connect(
                lambda _checked=False: self._catalog._scene.reload_current_scene(refresh_cb)
            )

        menu.addSeparator()
        new_cur_act = menu.addAction("New: Current")
        new_cur_act.triggered.connect(
            lambda _checked=False:
                self._catalog._workfiles.new_from_current(asset_id, dept, refresh_cb)
        )
        new_tmpl_act = menu.addAction("New: Template")
        new_tmpl_act.triggered.connect(
            lambda _checked=False:
                self._catalog._workfiles.new_from_template(asset_id, dept, refresh_cb)
        )

        if pos is not None:
            global_pos = source_widget.mapToGlobal(pos)
        else:
            global_pos = source_widget.mapToGlobal(
                source_widget.rect().bottomLeft()
            )
        menu.exec(global_pos)

