"""Slim submit-jobs dialog for the TumblePipe asset-browser catalog.

A compact dialog (this replaced the retired Project Browser's
``JobSubmissionDialog``).
One shared form drives publish + render submission for all selected entities;
defaults are seeded from the first entity's properties. Render starts enabled
and publish disabled — the dialog opens from the top-bar Render quick action
(current scene's entity) as well as the multi-select context menu, and
rendering is the common case for both.

Single-entity opens get an Entity selector defaulting to the entity the
dialog was opened for; picking another entity re-targets the submission and
reseeds the form from that entity's properties. Multi-select opens keep
their fixed entity list (no selector).

Submission is synchronous and per-entity — each call to
``tumblepipe.farm.jobs.houdini.batch_submit.submit_entity_batch`` runs on the
caller's thread, and the dialog reports a single success/failure summary at the
end. For a richer per-task progress UI, use the ProcessDialog flow in
``tumblepipe.pipe.houdini.ui.process_dialog``.
"""

from __future__ import annotations

import logging
from typing import Sequence

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QSpinBox,
    QVBoxLayout, QWidget,
)

from tumbletrove.asset_browser.core.theme import (
    ACCENT, BG_DARK, BG_DARKEST, BORDER, FONT_BODY, FONT_FAMILY,
    TEXT_PRIMARY, TEXT_SECONDARY,
)

log = logging.getLogger(__name__)


# ── Theme ─────────────────────────────────────────────────

_DIALOG_STYLE = f"""
QDialog {{ background-color: {BG_DARKEST}; }}
QLabel {{
    color: {TEXT_PRIMARY};
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
    background: transparent;
}}
QGroupBox {{
    color: {TEXT_PRIMARY};
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    color: {TEXT_PRIMARY};
}}
QGroupBox::indicator {{ width: 14px; height: 14px; }}
QLineEdit, QSpinBox, QComboBox {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 3px 5px;
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
    min-height: 20px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{ border: none; width: 16px; }}
QCheckBox {{
    color: {TEXT_PRIMARY};
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
    spacing: 6px;
}}
QPushButton {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 5px 14px;
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:default {{
    background-color: {ACCENT};
    color: {BG_DARKEST};
    border-color: {ACCENT};
}}
"""


# ── Helpers ───────────────────────────────────────────────

def _safe_get_properties(entity_uri):
    """Return ``api.config.get_properties(entity_uri)`` or ``{}`` on failure.

    Importing ``tumblepipe.api`` here is a passive lookup: the catalog has
    already activated the project before showing the dialog.
    """
    try:
        from tumblepipe.api import default_client
        client = default_client()
        props = client.config.get_properties(entity_uri)
        return props or {}
    except Exception:
        log.exception("Failed to read properties for %s", entity_uri)
        return {}


def _list_dept_names(context: str, *, only_publishable: bool, only_renderable: bool) -> list[str]:
    """Return department names for ``context`` ('shots' or 'assets').

    Filters by ``publishable`` / ``renderable`` flags. Excludes
    Python-generated departments (which can't be exported from Houdini)
    and disabled ones.
    """
    try:
        from tumblepipe.config.department import list_departments
        depts = list_departments(
            context, include_generated=False, include_disabled=False,
        )
        if only_publishable:
            depts = [d for d in depts if d.publishable]
        if only_renderable:
            depts = [d for d in depts if d.renderable]
        return [d.name for d in depts]
    except Exception:
        log.exception("Failed to list departments for context=%s", context)
        return []


def _nested(d: dict, dotted: str, default=None):
    cur = d
    for part in dotted.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _list_selectable_entities() -> list[tuple[object, str, str]]:
    """Return ``(uri, name, context)`` for every terminal entity in the
    active project, sorted by path. Empty list on failure (the entity
    selector is simply not shown).

    ``list_entity_uris(closure=True)`` returns childless *category* nodes
    (e.g. an empty ``assets/CHAR``) alongside real entities, so each URI is
    vetted with ``is_terminal_entity`` (schema-keyed).
    """
    try:
        from tumblepipe.api import default_client
        from tumblepipe.config.entities import is_terminal_entity
        config = default_client().config
        entities: list[tuple[object, str, str]] = []
        for uri in config.list_entity_uris(closure=True):
            segments = uri.segments
            context = segments[0] if segments else None
            if context not in ("shots", "assets"):
                continue
            if not is_terminal_entity(config, uri):
                continue
            entities.append((uri, segments[-1], context))
        entities.sort(key=lambda item: str(item[0]))
        return entities
    except Exception:
        log.exception("Failed to list entities for the entity selector")
        return []


# ── Dialog ────────────────────────────────────────────────

class SubmitJobsDialog(QDialog):
    """Compact submit dialog for one or more pipeline entities.

    Args:
        entity_uris: List of ``tumblepipe.util.uri.Uri`` (or any object with
            ``str(uri)``) — one per selected entity. All must share the
            same ``context`` (i.e. all shots OR all assets).
        entity_names: Display names parallel to ``entity_uris``.
        context: ``'shots'`` or ``'assets'`` — drives department filtering.
        parent: Parent widget. Pass ``hou.qt.mainWindow()`` from Houdini.
    """

    def __init__(
        self,
        entity_uris: Sequence,
        entity_names: Sequence[str],
        context: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not entity_uris:
            raise ValueError("entity_uris must be non-empty")
        if context not in ("shots", "assets"):
            raise ValueError(f"context must be 'shots' or 'assets', got {context!r}")
        self._entity_uris = list(entity_uris)
        self._entity_names = list(entity_names)
        self._context = context

        # Defaults from the first entity's properties.
        self._defaults = _safe_get_properties(self._entity_uris[0])

        self.setWindowTitle("Submit Jobs")
        self.setMinimumWidth(440)
        self.setStyleSheet(_DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Header — entity count and a truncated name list.
        self._header = QLabel(self._build_header_text())
        self._header.setWordWrap(True)
        self._header.setStyleSheet(f"color: {TEXT_SECONDARY};")
        root.addWidget(self._header)

        # Entity selector — single-entity opens only (the quick action /
        # single right-click). A multi-select submit keeps its fixed list;
        # re-picking N entities in a combobox has no sane UI.
        self._entity_combo: QComboBox | None = None
        if len(self._entity_uris) == 1:
            selector = self._build_entity_selector()
            if selector is not None:
                root.addWidget(selector)

        self._publish_box = self._build_publish_section()
        root.addWidget(self._publish_box)

        self._render_box = self._build_render_section()
        root.addWidget(self._render_box)

        self._apply_entity_defaults()

        # Submit / Cancel row.
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self,
        )
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        ok_btn.setText("Submit")
        ok_btn.setDefault(True)
        buttons.accepted.connect(self._on_submit)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ── UI construction ───────────────────────────────────

    def _build_header_text(self) -> str:
        n = len(self._entity_uris)
        names = ", ".join(self._entity_names[:8])
        if len(self._entity_names) > 8:
            names += f", +{len(self._entity_names) - 8} more"
        suffix = "entity" if n == 1 else "entities"
        return f"Submit jobs for {n} {suffix} ({self._context}): {names}"

    def _build_entity_selector(self) -> QWidget | None:
        """Build the "Entity:" combobox row, or ``None`` when the project's
        entities can't be listed (the dialog then keeps its fixed target).

        Defaults to the entity the dialog was opened for (the loaded
        scene's, from the Render quick action); picking another entity
        re-targets the submission and reseeds the form from its properties.
        """
        entities = _list_selectable_entities()
        if not entities:
            return None

        combo = QComboBox()
        current = str(self._entity_uris[0])
        current_index = -1
        for i, (uri, name, ctx) in enumerate(entities):
            combo.addItem("/".join(uri.segments), (uri, name, ctx))
            if str(uri) == current:
                current_index = i
        if current_index < 0:
            # The opened entity is missing from the config listing (e.g.
            # off-config scene) — prepend it so the default stays truthful.
            combo.insertItem(
                0,
                "/".join(self._entity_uris[0].segments),
                (self._entity_uris[0], self._entity_names[0], self._context),
            )
            current_index = 0
        combo.setCurrentIndex(current_index)
        # Connected after the initial selection so seeding only reruns on
        # an actual user pick.
        combo.currentIndexChanged.connect(self._on_entity_changed)
        self._entity_combo = combo

        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(QLabel("Entity:"))
        row.addWidget(combo, 1)
        return wrap

    def _on_entity_changed(self, index: int) -> None:
        if self._entity_combo is None:
            return
        data = self._entity_combo.itemData(index)
        if not data:
            return
        uri, name, context = data
        self._entity_uris = [uri]
        self._entity_names = [name]
        self._context = context
        self._defaults = _safe_get_properties(uri)
        self._header.setText(self._build_header_text())
        self._apply_entity_defaults()

    def _build_publish_section(self) -> QGroupBox:
        box = QGroupBox("Publish")
        box.setCheckable(True)
        box.setChecked(False)
        form = QFormLayout(box)
        form.setContentsMargins(10, 14, 10, 10)
        form.setSpacing(6)

        self._pub_dept = QComboBox()
        form.addRow("Department:", self._pub_dept)

        self._pub_pool = QLineEdit()
        self._pub_pool.setPlaceholderText("general")
        form.addRow("Pool:", self._pub_pool)

        self._pub_priority = QSpinBox()
        self._pub_priority.setRange(0, 100)
        form.addRow("Priority:", self._pub_priority)

        return box

    def _build_render_section(self) -> QGroupBox:
        box = QGroupBox("Render")
        box.setCheckable(True)
        # Render is the common case for this dialog (publish has its own
        # quick action), so it starts enabled.
        box.setChecked(True)
        form = QFormLayout(box)
        form.setContentsMargins(10, 14, 10, 10)
        form.setSpacing(6)

        # Department
        self._rnd_dept = QComboBox()
        form.addRow("Department:", self._rnd_dept)

        # Variants — comma-separated list
        self._rnd_variants = QLineEdit()
        self._rnd_variants.setPlaceholderText("default")
        form.addRow("Variants (csv):", self._rnd_variants)

        # Range mode — Full range submits the full_render chain (all
        # frames + slapcomp/mp4); First / Middle / Last submits the
        # partial_render chain (3 check frames + notify) for a quick look
        # before committing the farm to the whole range.
        self._rnd_mode = QComboBox()
        self._rnd_mode.addItems(["Full range", "First / Middle / Last"])
        form.addRow("Range:", self._rnd_mode)

        # Frame range row: first / last
        self._rnd_first = QSpinBox()
        self._rnd_first.setRange(-1_000_000, 1_000_000)
        self._rnd_last = QSpinBox()
        self._rnd_last.setRange(-1_000_000, 1_000_000)
        frame_row = QHBoxLayout()
        frame_row.setSpacing(6)
        frame_row.addWidget(self._rnd_first)
        frame_row.addWidget(QLabel("→"))
        frame_row.addWidget(self._rnd_last)
        frame_wrap = QWidget()
        frame_wrap.setLayout(frame_row)
        form.addRow("Frames:", frame_wrap)

        # Pre/Post roll
        self._rnd_pre = QSpinBox()
        self._rnd_pre.setRange(0, 1000)
        self._rnd_post = QSpinBox()
        self._rnd_post.setRange(0, 1000)
        roll_row = QHBoxLayout()
        roll_row.setSpacing(6)
        roll_row.addWidget(self._rnd_pre)
        roll_row.addWidget(QLabel("/"))
        roll_row.addWidget(self._rnd_post)
        roll_wrap = QWidget()
        roll_wrap.setLayout(roll_row)
        form.addRow("Pre / Post roll:", roll_wrap)

        # Pool / Priority / Tile / Batch — pack on two rows
        self._rnd_pool = QLineEdit()
        self._rnd_pool.setPlaceholderText("general")
        form.addRow("Pool:", self._rnd_pool)

        self._rnd_priority = QSpinBox()
        self._rnd_priority.setRange(0, 100)
        self._rnd_tile = QSpinBox()
        self._rnd_tile.setRange(1, 64)
        self._rnd_batch = QSpinBox()
        self._rnd_batch.setRange(1, 1000)
        ppb_row = QHBoxLayout()
        ppb_row.setSpacing(6)
        ppb_row.addWidget(QLabel("Pri:"))
        ppb_row.addWidget(self._rnd_priority)
        ppb_row.addWidget(QLabel("Tiles:"))
        ppb_row.addWidget(self._rnd_tile)
        ppb_row.addWidget(QLabel("Batch:"))
        ppb_row.addWidget(self._rnd_batch)
        ppb_wrap = QWidget()
        ppb_wrap.setLayout(ppb_row)
        form.addRow("", ppb_wrap)

        # Samples
        self._rnd_samples = QSpinBox()
        self._rnd_samples.setRange(1, 4096)
        form.addRow("Samples:", self._rnd_samples)

        # Boolean checkboxes
        self._rnd_denoise = QCheckBox("Denoise")
        self._rnd_mblur = QCheckBox("Motion blur")
        self._rnd_dof = QCheckBox("DOF")
        self._rnd_standalone = QCheckBox("Standalone")
        self._rnd_standalone.setChecked(False)
        self._rnd_copy_edit = QCheckBox("Copy to edit")
        self._rnd_copy_edit.setChecked(False)

        flags_row1 = QHBoxLayout()
        flags_row1.setSpacing(12)
        flags_row1.addWidget(self._rnd_denoise)
        flags_row1.addWidget(self._rnd_mblur)
        flags_row1.addWidget(self._rnd_dof)
        flags_row1.addStretch(1)
        flags_wrap1 = QWidget()
        flags_wrap1.setLayout(flags_row1)
        form.addRow("", flags_wrap1)

        flags_row2 = QHBoxLayout()
        flags_row2.setSpacing(12)
        flags_row2.addWidget(self._rnd_standalone)
        flags_row2.addWidget(self._rnd_copy_edit)
        flags_row2.addStretch(1)
        flags_wrap2 = QWidget()
        flags_wrap2.setLayout(flags_row2)
        form.addRow("", flags_wrap2)

        return box

    def _apply_entity_defaults(self) -> None:
        """Seed the form from the current entity's properties and context.

        Runs once on construction and again on every entity-selector pick.
        Entity-derived fields (departments, frames, farm settings, render
        toggles) are re-seeded — deliberately overwriting user edits, since
        defaults follow the entity. Pure submission choices (range mode,
        Standalone, Copy to edit, section checkboxes) are left alone.
        """
        d = self._defaults

        pub_depts = _list_dept_names(
            self._context, only_publishable=True, only_renderable=False,
        )
        self._pub_dept.clear()
        self._pub_dept.addItems(pub_depts)
        # Seed: prefer entity property `submission.publish.department` if present.
        seed = _nested(d, 'submission.publish.department')
        if seed and seed in pub_depts:
            self._pub_dept.setCurrentText(seed)

        self._pub_pool.setText(str(_nested(d, 'farm.default_pool', '') or ''))
        self._pub_priority.setValue(int(_nested(d, 'farm.priority', 50)))

        rnd_depts = _list_dept_names(
            self._context, only_publishable=False, only_renderable=True,
        )
        self._rnd_dept.clear()
        self._rnd_dept.addItems(rnd_depts)
        seed = _nested(d, 'submission.render.department')
        if seed and seed in rnd_depts:
            self._rnd_dept.setCurrentText(seed)

        variants_default = _nested(d, 'variants') or ['default']
        if isinstance(variants_default, list):
            variants_text = ", ".join(str(v) for v in variants_default)
        else:
            variants_text = str(variants_default)
        self._rnd_variants.setText(variants_text)

        self._rnd_first.setValue(int(_nested(d, 'frame_start', 1001)))
        self._rnd_last.setValue(int(_nested(d, 'frame_end', 1100)))
        self._rnd_pre.setValue(int(_nested(d, 'roll_start', 0)))
        self._rnd_post.setValue(int(_nested(d, 'roll_end', 0)))
        self._rnd_pool.setText(str(_nested(d, 'farm.default_pool', '') or ''))
        self._rnd_priority.setValue(int(_nested(d, 'farm.priority', 50)))
        self._rnd_tile.setValue(int(_nested(d, 'farm.tile_count', 4)))
        self._rnd_batch.setValue(int(_nested(d, 'farm.batch_size', 10)))
        self._rnd_samples.setValue(
            int(_nested(d, 'render.pathtracedsamples', 64)),
        )
        self._rnd_denoise.setChecked(
            bool(_nested(d, 'render.enabledenoising', True)),
        )
        self._rnd_mblur.setChecked(
            bool(_nested(d, 'render.enablemblur', True)),
        )
        self._rnd_dof.setChecked(bool(_nested(d, 'render.enabledof', True)))

    # ── Submit ────────────────────────────────────────────

    def _on_submit(self) -> None:
        publish = self._publish_box.isChecked()
        render = self._render_box.isChecked()
        if not publish and not render:
            QMessageBox.warning(
                self, "Submit Jobs",
                "Enable at least one of Publish or Render before submitting.",
            )
            return

        # Build the shared settings dict once — it's the same for every entity
        # in the slim flow. Per-entity overrides are out of scope.
        settings: dict = {'publish': publish, 'render': render}
        if publish:
            settings['pub_department'] = self._pub_dept.currentText()
            settings['pub_pool'] = self._pub_pool.text().strip() or 'general'
            settings['pub_priority'] = self._pub_priority.value()
        if render:
            variants = [
                v.strip() for v in self._rnd_variants.text().split(',')
                if v.strip()
            ] or ['default']
            settings.update({
                'render_department': self._rnd_dept.currentText(),
                'render_mode': (
                    'first_middle_last'
                    if self._rnd_mode.currentIndex() == 1 else 'full'
                ),
                'render_pool': self._rnd_pool.text().strip() or 'general',
                'render_priority': self._rnd_priority.value(),
                'variants': variants,
                'first_frame': self._rnd_first.value(),
                'last_frame': self._rnd_last.value(),
                'pre_roll': self._rnd_pre.value(),
                'post_roll': self._rnd_post.value(),
                'tile_count': self._rnd_tile.value(),
                'batch_size': self._rnd_batch.value(),
                'denoise': self._rnd_denoise.isChecked(),
                'mblur': self._rnd_mblur.isChecked(),
                'dof': self._rnd_dof.isChecked(),
                'standalone': self._rnd_standalone.isChecked(),
                'copy_to_edit': self._rnd_copy_edit.isChecked(),
                'samples': self._rnd_samples.value(),
            })

        try:
            from tumblepipe.farm.jobs.houdini.batch_submit import (
                submit_entity_batch,
            )
        except Exception as exc:
            QMessageBox.critical(
                self, "Submit Jobs",
                f"tumblepipe.farm.jobs.houdini.batch_submit unavailable:\n{exc}",
            )
            return

        successes: list[tuple[str, list[str]]] = []
        failures: list[tuple[str, str]] = []
        for uri, name in zip(self._entity_uris, self._entity_names):
            config = {
                'entity': {
                    'uri': str(uri),
                    'name': name,
                    'context': self._context,
                },
                'settings': settings,
            }
            try:
                job_ids = submit_entity_batch(config)
                successes.append((name, list(job_ids or [])))
            except Exception as exc:
                log.exception("submit_entity_batch failed for %s", uri)
                failures.append((name, str(exc)))

        # Summary
        lines: list[str] = []
        if successes:
            lines.append(
                f"Submitted {len(successes)}/{len(self._entity_uris)} entities."
            )
            for name, ids in successes:
                lines.append(f"  • {name}: {', '.join(ids) if ids else '(no ids)'}")
        if failures:
            lines.append("")
            lines.append(f"{len(failures)} failed:")
            for name, err in failures:
                lines.append(f"  • {name}: {err}")
        msg_text = "\n".join(lines) if lines else "Nothing was submitted."

        if failures and not successes:
            QMessageBox.critical(self, "Submit Jobs", msg_text)
        elif failures:
            QMessageBox.warning(self, "Submit Jobs", msg_text)
        else:
            QMessageBox.information(self, "Submit Jobs", msg_text)
            self.accept()
