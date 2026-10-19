"""The hover popup of asset, shot, Multi and Root cards.

Built as a widget tree (rather than HTML) so the department icons can carry
their own hover popups. Recipes and nodes take the framework's generic
hover; the catalog routes those before anything reaches here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import DEPT_ICONS, SHOT_DEPT_ICONS

if TYPE_CHECKING:
    from .dept_scope import DepartmentScope
    from .workfiles import WorkfileManager


class AssetHover:
    """Builds a card's hover widget."""

    def __init__(
        self, *, dept_scope: "DepartmentScope", workfiles: "WorkfileManager",
    ) -> None:
        self._dept_scope = dept_scope
        self._workfiles = workfiles

    def widget(self, asset):
        """Pipeline-specific asset hover popup, built as a widget tree.

        Compact at-a-glance card so the user can see asset info without
        opening the detail panel. Sections (top → bottom):

        - Big title + type/project/category subtitle
        - Tag pills (custom tags only; structural ones suppressed)
        - Department icon grid — all known depts for the context.
          Active depts tinted, inactive depts deep-grey. Each icon has
          a native Qt tooltip showing its name + version on hover.
        - Member/shot/sequence rows for Multi/Root containers

        Built as a widget tree (rather than HTML) so sub-elements like
        the dept icons can carry their own tooltips. All reads come
        from ``asset.metadata`` and ``asset.tags`` — no I/O.
        """
        from PySide6.QtWidgets import (
            QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
        )
        from tumbletrove.asset_browser.core.theme import (
            BG_MID, BORDER, FONT_BODY, FONT_FAMILY, FONT_SMALL,
            FONT_TINY, FONT_TITLE, TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY,
        )

        name = asset.name or "Untitled"
        tags = list(asset.tags or ())
        metadata = asset.metadata or {}

        type_tag = next(
            (t.split(":", 1)[1] for t in tags if t.startswith("type:")), None
        )
        type_label = {
            "asset": "Asset",
            "shot": "Shot",
            "group": "Multi",
            "scene": "Root",
        }.get(type_tag, type_tag.title() if type_tag else "")
        project = next(
            (t.split(":", 1)[1] for t in tags if t.startswith("project:")),
            None,
        )
        category = next(
            (t.split(":", 1)[1] for t in tags if t.startswith("category:")),
            None,
        )

        root = QWidget()
        root.setStyleSheet(
            f"QWidget {{ background: transparent; "
            f'font-family: "{FONT_FAMILY}"; color: {TEXT_PRIMARY}; }}'
        )
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # ── Title (bigger than the rest) ──────────────
        title = QLabel(name, root)
        title.setStyleSheet(
            f"color:{TEXT_PRIMARY}; font-weight:700; "
            f"font-size:{FONT_TITLE}px; background:transparent;"
        )
        layout.addWidget(title)

        meta_line = " · ".join(
            v for v in (type_label, project, category) if v
        )
        if meta_line:
            subtitle = QLabel(meta_line, root)
            subtitle.setStyleSheet(
                f"color:{TEXT_SECONDARY}; font-size:{FONT_BODY}px; "
                f"background:transparent;"
            )
            layout.addWidget(subtitle)

        def add_separator():
            layout.addSpacing(10)
            sep = QFrame(root)
            sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet(f"color:{BORDER}; background:{BORDER};")
            sep.setFixedHeight(1)
            layout.addWidget(sep)
            layout.addSpacing(6)

        def add_section_header(text):
            hdr = QLabel(text.upper(), root)
            hdr.setStyleSheet(
                f"color:{TEXT_DIM}; font-size:{FONT_TINY}px; "
                f"font-weight:600; letter-spacing:1px; background:transparent;"
            )
            layout.addWidget(hdr)

        # ── Tags ─────────────────────────────────────
        # Pills only for non-structural tags. type:/status:/project:/
        # category: are surfaced in title/subtitle or on the card.
        suppress = ("type:", "status:", "project:", "category:")
        pill_tags = [t for t in tags if not t.startswith(suppress)]
        if pill_tags:
            add_separator()
            add_section_header("Tags")
            pills_widget = QWidget(root)
            pills_layout = QHBoxLayout(pills_widget)
            pills_layout.setContentsMargins(0, 4, 0, 0)
            pills_layout.setSpacing(4)
            for tag in pill_tags:
                display = tag.split(":", 1)[-1] if ":" in tag else tag
                pill = QLabel(display, pills_widget)
                pill.setStyleSheet(
                    f"background-color:{BG_MID}; "
                    f"border:1px solid {BORDER}; border-radius:8px; "
                    f"padding:1px 8px; color:{TEXT_SECONDARY}; "
                    f"font-size:{FONT_SMALL}px;"
                )
                pills_layout.addWidget(pill)
            pills_layout.addStretch(1)
            layout.addWidget(pills_widget)

        # ── Department grid ──────────────────────────
        if type_tag in ("asset", "shot"):
            grid_widget = self._department_grid(
                root, type_tag, metadata, asset,
            )
            if grid_widget is not None:
                add_separator()
                add_section_header("Departments")
                layout.addWidget(grid_widget)

        # ── Container info (Multi / Root only) ──────
        extras: list[tuple[str, str]] = []
        # member_count is a typed field now (0 for non-containers); only
        # Multi/Root cards should surface the Members row.
        if type_tag in ("group", "scene"):
            extras.append(("Members", str(asset.member_count)))
        if "shot_count" in metadata:
            extras.append(("Shots", str(metadata["shot_count"])))
        if "sequence" in metadata and metadata["sequence"]:
            extras.append(("Sequence", str(metadata["sequence"])))
        if extras:
            add_separator()
            for k, v in extras:
                row = QLabel(
                    f"<span style='color:{TEXT_DIM};'>{k}:</span> "
                    f"<span style='color:{TEXT_PRIMARY};'>{v}</span>",
                    root,
                )
                row.setStyleSheet(
                    f"font-size:{FONT_BODY}px; background:transparent;"
                )
                layout.addWidget(row)

        return root

    def _department_grid(self, parent, type_tag, metadata, asset):
        """Build the department icon grid as a QWidget.

        Each icon carries a :class:`ButtonHoverInfo` popup that fans out
        on hover (when the parent popup is pinned) and shows version,
        last user, last save, and last publish for that department.

        Returns ``None`` if no departments are configured for the
        context, so the caller knows to skip the section entirely.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QGridLayout, QLabel, QWidget
        from tumbletrove.asset_browser.core.hover_html import format_age_html
        from tumbletrove.asset_browser.core.hover_info import ButtonHoverInfo
        from tumbletrove.asset_browser.core.icons import icon_pixmap
        from tumbletrove.asset_browser.core.theme import (
            BORDER, TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY,
        )
        from tumblepipe.config.department import list_departments

        context = "shots" if type_tag == "shot" else "assets"
        raw_depts = metadata.get("departments")
        # The card's own departments — its assignment plus anything that
        # already has work — so the hover grid matches its deck.
        shown, _assigned = self._dept_scope.for_entity(
            asset.id, context,
            with_work=(
                raw_depts.keys() if isinstance(raw_depts, dict) else ()
            ),
        )
        by_name = {
            d.name: d for d in list_departments(
                context, include_generated=False,
            )
            if d.enabled
        }
        all_depts = [by_name[name] for name in shown if name in by_name]
        if not all_depts:
            return None

        # ``departments`` is dict[str, str] on every card and every
        # detail alike (see ``latest_by_dept``), so read it straight.
        active_versions: dict[str, str] = {}
        if isinstance(raw_depts, dict):
            active_versions = {k: v for k, v in raw_depts.items() if v}

        asset_id = asset.id
        workfiles = self._workfiles

        active_color = TEXT_PRIMARY
        inactive_color = "#3e3e44"
        is_shot = type_tag == "shot"

        grid_widget = QWidget(parent)
        grid_widget.setStyleSheet("background: transparent;")
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        # Min 3, max 4 columns. Below 4 depts we still reserve a 3-wide
        # grid so single-dept rows don't visually collapse to one icon.
        n_per_row = min(4, max(3, len(all_depts)))

        def _make_content_fn(dept_name, display_name, is_active, version):
            def _content():
                title = (
                    f"<div style='color:{TEXT_PRIMARY}; font-weight:600; "
                    f"font-size:13px;'>{display_name}</div>"
                )
                if is_active:
                    sub_text = f"Active · {version}" if version else "Active"
                else:
                    sub_text = "Inactive"
                subtitle = (
                    f"<div style='color:{TEXT_SECONDARY}; margin-top:2px;'>"
                    f"{sub_text}</div>"
                )

                rows: list[str] = []
                if is_active and asset_id and workfiles is not None:
                    if version:
                        user = workfiles.get_user_for_version(
                            asset_id, dept_name, version,
                        )
                        if user:
                            rows.append(
                                f"<div style='margin-top:6px;'>"
                                f"<span style='color:{TEXT_DIM};'>User:</span> "
                                f"<span style='color:{TEXT_PRIMARY};'>"
                                f"{user}</span></div>"
                            )
                        saved_dt = workfiles.get_mtime_for_version(
                            asset_id, dept_name, version,
                        )
                        saved_ts = (
                            saved_dt.timestamp() if saved_dt is not None
                            else None
                        )
                        rows.append(
                            f"<div style='margin-top:6px;'>"
                            f"{format_age_html('Last saved', saved_ts)}"
                            f"</div>"
                        )
                    pub_dt = workfiles.get_latest_export_mtime(
                        asset_id, dept_name,
                    )
                    pub_ts = (
                        pub_dt.timestamp() if pub_dt is not None else None
                    )
                    rows.append(
                        f"<div style='margin-top:6px;'>"
                        f"{format_age_html('Last published', pub_ts)}"
                        f"</div>"
                    )
                    if pub_ts is not None:
                        pub_user = workfiles.get_user_for_export(
                            asset_id, dept_name,
                        )
                        if pub_user:
                            rows.append(
                                f"<div style='margin-top:6px;'>"
                                f"<span style='color:{TEXT_DIM};'>"
                                f"Published by:</span> "
                                f"<span style='color:{TEXT_PRIMARY};'>"
                                f"{pub_user}</span></div>"
                            )

                sep = ""
                if rows:
                    sep = (
                        f"<div style='border-top:1px solid {BORDER}; "
                        f"margin:8px 0 0 0;'></div>"
                    )
                return title + subtitle + sep + "".join(rows)
            return _content

        for i, dept in enumerate(all_depts):
            row, col = divmod(i, n_per_row)
            icon_name = None
            if is_shot:
                icon_name = SHOT_DEPT_ICONS.get(dept.name)
            if icon_name is None:
                icon_name = DEPT_ICONS.get(dept.name, "package")
            is_active = dept.name in active_versions
            color = active_color if is_active else inactive_color
            pm = icon_pixmap(icon_name, 22, color)

            icon_label = QLabel(grid_widget)
            icon_label.setPixmap(pm)
            icon_label.setFixedSize(28, 28)
            icon_label.setAlignment(Qt.AlignCenter)
            icon_label.setStyleSheet("background: transparent;")
            icon_label.setAttribute(Qt.WA_Hover, True)

            display_name = (dept.short or dept.name).title()
            version = active_versions.get(dept.name) or "" if is_active else ""

            # ButtonHoverInfo replaces the plain QToolTip with a richer
            # popup (version, user, last save, last publish). Only fires
            # when the parent hover popup is pinned (cursor can't reach
            # the dept icons otherwise — see ButtonHoverInfo docstring).
            ButtonHoverInfo(
                icon_label,
                _make_content_fn(dept.name, display_name, is_active, version),
                placement="above",
            )

            grid.addWidget(icon_label, row, col)

        grid.setColumnStretch(n_per_row, 1)
        return grid_widget
