"""The Entity Editor's Render settings panel (#70): one field per render
setting of the selected shots, sequences or all shots, beside the Shots
table."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import (
    QEvent, QObject,
    Qt, Signal,
)
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from tumblepipe.config import entity_sheet as es
from tumblepipe.config.entity_sheet import GroupKey
from tumblepipe.config import render_config
from tumblepipe.util import value_ladder_qt

from tumbletrove.asset_browser.core.theme import (
    ACCENT, ACCENT_HOVER, BUTTON_GHOST_STYLE, COMBO_STYLE, FONT_FAMILY, FONT_SMALL, TEXT_DIM,
    WARNING, scaled,
)
from .entity_editor_widgets import _BOLD, _HINT_STYLE, _INHERITED, _button, _hint

if TYPE_CHECKING:
    from .entity_editor import _SheetPane


# ---------------------------------------------------------------------------
# The Shots tab's Render settings panel
# ---------------------------------------------------------------------------

_RENDER_TIPS = {
    es.RENDER_APPLY: (
        'Whether the shot\'s root layer authors the settings below on the '
        'project\'s RenderSettings prim. Off (or not set anywhere): renders '
        'use the RenderSettings the scene carries.'
    ),
    es.RENDER_RESOLUTION: (
        'Width and height in pixels; Presets picks a common size or the '
        'project\'s. Also sizes the layout camera\'s frame and playblasts, '
        'applied to the render or not.'
    ),
    es.RENDER_CAMERA: (
        'The camera prim the render looks through. Authored only when that '
        'camera exists in the project\'s root_default_prims.usda; otherwise '
        'the scene\'s camera renders.'
    ),
    render_config.SAMPLES: 'Karma\'s path traced samples per pixel.',
    render_config.DICING: 'Karma\'s dicing quality scale: lower dices geometry coarser and faster.',
    render_config.DIFFUSE_LIMIT: 'How many diffuse bounces a ray takes.',
    render_config.REFLECTION_LIMIT: 'How many reflection bounces a ray takes.',
    render_config.REFRACTION_LIMIT: 'How many refraction bounces a ray takes.',
    render_config.VOLUME_LIMIT: 'How many volume bounces a ray takes.',
    render_config.SSS_LIMIT: 'How many subsurface scattering bounces a ray takes.',
    render_config.DOF: 'Whether Karma renders depth of field.',
    render_config.MBLUR: 'Whether Karma renders motion blur.',
    render_config.AOVS: (
        'The AOVs every shot under this level renders, from the AOVs of '
        'th::render_vars: the root layer adds each beside the ones the '
        'project\'s render defaults already have. Set it on All shots for '
        'the whole project. A list set on a sequence or a shot replaces the '
        'one it would inherit; a shot adds more with th::render_vars in its '
        'render department. A shot whose render department is already '
        'published picks new AOVs up at that department\'s next publish.'
    ),
}
_RENDER_ORDER_NOTE = (
    'Each setting comes from the strongest level that sets it: the shot, '
    'its sequence, all shots, the project, then the pipeline defaults. '
    'Samples, ray limits, dicing, depth of field, motion blur and AOVs that no '
    'level sets keep the project\'s render defaults. A department\'s render '
    'settings node and a Farm Submit override still win. Apply brings the '
    'shots\' root layers up to date.'
)
_MIXED = '(mixed)'
_NOT_SET = 'not set'
_UNPIN = object()   # the Inherit button, or a field emptied: take the pin away
_RENDER_DEFAULTS = "not set: the project's render defaults decide"


def render_origin(state: str, source: str, name: str = '') -> str:
    """Where a render setting's value comes from, for the panel. ``name``
    tells a Karma setting only the pipeline defaults hold, which no render
    uses (``render_config.unset_default``)."""
    unset = render_config.unset_default(name, source)
    if state == es.OWN:
        return 'set here'
    if state == es.SET:
        return 'set here, waiting for Apply'
    if state == es.CLEARED:
        if unset:
            return "back to the project's render defaults on Apply"
        return f'inherits from {source or "nothing"} on Apply'
    if unset:
        return _RENDER_DEFAULTS
    return f'from {source}' if source else 'not set anywhere'


class _NoWheel(QObject):
    """Keeps the wheel from changing a field the pointer only passes over
    while the panel scrolls: a field takes it once it has focus."""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt)
        if event.type() == QEvent.Wheel and not obj.hasFocus():
            event.ignore()
            return True
        return False


class _Double(QDoubleSpinBox):
    """Numbers as the database holds them: ``2``, ``0.25``, not ``2.000``."""

    def textFromValue(self, value: float) -> str:  # noqa: N802 (Qt)
        return render_config.format_number(round(value, self.decimals()))


def _blankable(spin, low, high, no_wheel: _NoWheel):
    """``spin`` whose lowest value (below every value it takes) shows as a
    text instead (nothing set, or mixed). Commits on Enter, focus out or an
    arrow step, not on each key."""
    spin.setRange(low, high)
    spin.setKeyboardTracking(False)
    spin.setFocusPolicy(Qt.StrongFocus)
    spin.setSpecialValueText(_NOT_SET)
    spin.installEventFilter(no_wheel)
    # Middle-drag steps it; Ctrl + middle-click empties it (the default).
    value_ladder_qt.install(spin)
    return spin


class _Field(QWidget):
    """One render setting's editor. :meth:`show_value` shows a value
    (None: nothing, ``mixed``: the targets differ); :attr:`committed`
    carries what the artist set: a value, or :data:`_UNPIN` when they
    emptied it."""
    committed = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._filling = False
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(scaled(4))

    def show_value(self, value, mixed: bool, unset: str = _NOT_SET) -> None:
        self._filling = True
        try:
            self._show(value, mixed, unset)
        finally:
            self._filling = False

    def _show(self, value, mixed: bool, unset: str) -> None:
        raise NotImplementedError

    def _emit(self, value) -> None:
        if not self._filling:
            self.committed.emit(value)


class _CheckField(_Field):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.box = QCheckBox(text)
        self._layout.addWidget(self.box)
        self.box.clicked.connect(self._on_clicked)

    def _show(self, value, mixed, unset) -> None:
        self.box.setTristate(mixed)
        self.box.setCheckState(
            Qt.PartiallyChecked if mixed else Qt.Checked if value is True else Qt.Unchecked
        )

    def _on_clicked(self, _checked=False) -> None:
        value = self.box.checkState() != Qt.Unchecked
        self.box.setTristate(False)
        self._emit(value)


class _TextField(_Field):
    def __init__(self, placeholder: str, parent=None) -> None:
        super().__init__(parent)
        self.edit = QLineEdit()
        self._placeholder = placeholder
        self.edit.setPlaceholderText(placeholder)
        self._layout.addWidget(self.edit)
        self._shown = ''
        self.edit.editingFinished.connect(self._on_finished)

    def _show(self, value, mixed, unset) -> None:
        text = '' if mixed or value is None else str(value)
        self.edit.setPlaceholderText(_MIXED if mixed else self._placeholder)
        if self.edit.text() != text:
            self.edit.setText(text)
        self._shown = text

    def _on_finished(self) -> None:
        text = self.edit.text().strip()
        if text != self._shown:
            self._emit(text or _UNPIN)


class _NumberField(_Field):
    """A whole number above 0 (``int``) or a number of 0 or more."""

    def __init__(self, kind: str, no_wheel: _NoWheel, parent=None) -> None:
        super().__init__(parent)
        if kind == 'int':
            self.spin = _blankable(QSpinBox(), 0, 1_000_000, no_wheel)
        else:
            self.spin = _blankable(_Double(), -1.0, 100_000.0, no_wheel)
            self.spin.setDecimals(3)
        self.spin.setMinimumWidth(scaled(80))
        self._layout.addWidget(self.spin)
        self._layout.addStretch()
        self.spin.valueChanged.connect(self._on_changed)

    def _show(self, value, mixed, unset) -> None:
        self.spin.setSpecialValueText(_MIXED if mixed else unset)
        if mixed or isinstance(value, bool) or not isinstance(value, (int, float)) \
                or value != value:
            self.spin.setValue(self.spin.minimum())
        elif isinstance(self.spin, QSpinBox):
            self.spin.setValue(max(int(value), self.spin.minimum()))
        else:
            self.spin.setValue(max(float(value), self.spin.minimum()))

    def _on_changed(self, value) -> None:
        if value <= self.spin.minimum():
            self._emit(_UNPIN)
        else:
            self._emit(int(value) if isinstance(self.spin, QSpinBox) else float(value))


class _ChoiceField(_Field):
    """On or off, or nothing set: the first entry shows that state, and
    picking it takes the pin away."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.combo = QComboBox()
        self.combo.setStyleSheet(COMBO_STYLE)
        self.combo.addItem(_NOT_SET, _UNPIN)
        self.combo.addItem('On', True)
        self.combo.addItem('Off', False)
        self.combo.setMinimumWidth(scaled(80))
        self._layout.addWidget(self.combo)
        self._layout.addStretch()
        self.combo.activated.connect(lambda i: self._emit(self.combo.itemData(i)))

    def _show(self, value, mixed, unset) -> None:
        index = 0 if mixed or not isinstance(value, bool) else (1 if value else 2)
        self.combo.setItemText(0, _MIXED if mixed else unset if index == 0 else 'Inherit')
        self.combo.setCurrentIndex(index)


class _ResolutionField(_Field):
    """Width and height as two whole numbers, as the usdrender ROP has
    them, and a Presets menu. Both emptied takes the pin away; a change to
    one pins both."""

    def __init__(self, presets: Callable[[], list], no_wheel: _NoWheel, parent=None) -> None:
        super().__init__(parent)
        self._presets = presets
        self.width_spin = _blankable(QSpinBox(), 0, 65_536, no_wheel)
        self.height_spin = _blankable(QSpinBox(), 0, 65_536, no_wheel)
        self.width_spin.setToolTip('Width in pixels')
        self.height_spin.setToolTip('Height in pixels')
        for spin in (self.width_spin, self.height_spin):
            spin.setMinimumWidth(scaled(60))
            spin.valueChanged.connect(self._on_changed)
        self._layout.addWidget(self.width_spin)
        times = QLabel('x')
        times.setStyleSheet(_HINT_STYLE)
        self._layout.addWidget(times)
        self._layout.addWidget(self.height_spin)
        self.presets_button = QToolButton()
        self.presets_button.setText('Presets')
        self.presets_button.setToolTip('Common resolutions, the project\'s first.')
        self.presets_button.setPopupMode(QToolButton.InstantPopup)
        self.presets_button.setStyleSheet(BUTTON_GHOST_STYLE.replace('QPushButton', 'QToolButton'))
        menu = QMenu(self.presets_button)
        menu.aboutToShow.connect(lambda m=menu: self._fill_menu(m))
        self.presets_button.setMenu(menu)
        self._layout.addWidget(self.presets_button)
        self._layout.addStretch()

    def _fill_menu(self, menu: QMenu) -> None:
        menu.clear()
        for label, size in self._presets():
            action = menu.addAction(render_config.preset_text(label, size))
            action.triggered.connect(lambda _c=False, s=size: self.pick(s))

    def pick(self, size) -> None:
        """A preset chosen."""
        self._emit([int(size[0]), int(size[1])])

    def _show(self, value, mixed, unset) -> None:
        found = None if mixed else render_config.valid_resolution(value)
        for spin, part in ((self.width_spin, 0), (self.height_spin, 1)):
            spin.setSpecialValueText(_MIXED if mixed else unset)
            spin.setValue(found[part] if found else 0)

    def _on_changed(self, _value) -> None:
        w, h = self.width_spin.value(), self.height_spin.value()
        if w > 0 and h > 0:
            self._emit([w, h])
        elif w == 0 and h == 0:
            self._emit(_UNPIN)
        # One of the two still empty: wait for the other.


class _AovsField(_Field):
    """The AOVs a level adds: their names typed (``albedo, depth``), or
    ticked in the AOVs menu, which lists the AOVs of th::render_vars.
    Emptied takes the pin away; ``none`` pins an empty list."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(_NOT_SET)
        self._layout.addWidget(self.edit, 1)
        self._shown = ''
        self._names: list[str] = []
        self.edit.editingFinished.connect(self._on_finished)
        self.menu_button = QToolButton()
        self.menu_button.setText('AOVs')
        self.menu_button.setToolTip('Tick the AOVs of th::render_vars to add.')
        self.menu_button.setPopupMode(QToolButton.InstantPopup)
        self.menu_button.setStyleSheet(BUTTON_GHOST_STYLE.replace('QPushButton', 'QToolButton'))
        menu = QMenu(self.menu_button)
        menu.aboutToShow.connect(lambda m=menu: self._fill_menu(m))
        self.menu_button.setMenu(menu)
        self._layout.addWidget(self.menu_button)

    def _fill_menu(self, menu: QMenu) -> None:
        menu.clear()
        for name in render_config.AOV_NAMES:
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name in self._names)
            action.triggered.connect(lambda on, n=name: self.tick(n, on))

    def tick(self, name: str, on: bool) -> None:
        """An AOV ticked or unticked in the menu."""
        names = [n for n in self._names if n != name] + ([name] if on else [])
        self._emit(render_config.valid_aovs(names) or _UNPIN)

    def _show(self, value, mixed, unset) -> None:
        found = None if mixed else render_config.valid_aovs(value)
        self._names = list(found or [])
        text = '' if mixed or value is None else render_config.format_value(render_config.AOVS, value)
        self.edit.setPlaceholderText(_MIXED if mixed else unset)
        if self.edit.text() != text:
            self.edit.setText(text)
        self._shown = text

    def _on_finished(self) -> None:
        text = self.edit.text().strip()
        if text != self._shown:
            self._emit(text or _UNPIN)


class _RenderSettings(QWidget):
    """The Shots tab's side panel: the render settings a shot's root layer
    applies (#70), for the selected shots, the selected sequences (a
    sequence's own row), or with **All shots** pressed, all shots
    (``entity:/shots``), what every sequence and shot inherits.

    Each setting shows its value and where it comes from; changing it pins
    it on the targets, Inherit takes the pin away. The edits are pending
    like the sheet's and written by Apply."""

    def __init__(self, pane: '_SheetPane') -> None:
        super().__init__(pane)
        self._pane = pane
        self._filling = False
        self._no_wheel = _NoWheel(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(scaled(4), scaled(2), scaled(4), scaled(4))
        layout.setSpacing(scaled(4))
        top = QHBoxLayout()
        self.title = QLabel('Render settings')
        self.title.setFont(_BOLD)
        top.addWidget(self.title, 1)
        self.all_shots_button = QPushButton('All shots')
        self.all_shots_button.setCheckable(True)
        self.all_shots_button.setAutoDefault(False)
        self.all_shots_button.setStyleSheet(BUTTON_GHOST_STYLE + (
            f'QPushButton:checked {{ color: {ACCENT_HOVER}; border-color: {ACCENT}; }}'
        ))
        self.all_shots_button.setToolTip(
            'Edit all shots (entity:/shots), what every sequence and shot '
            'inherits unless it sets its own, instead of the selection. All '
            'shots itself inherits from the project, then the pipeline defaults.'
        )
        self.all_shots_button.toggled.connect(lambda _on: self.fill())
        top.addWidget(self.all_shots_button)
        layout.addLayout(top)
        self.target = _hint()
        layout.addWidget(self.target)

        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(scaled(8))
        grid.setVerticalSpacing(scaled(1))
        self.editors: dict[str, _Field] = {}
        self.origins: dict[str, QLabel] = {}
        self.inherit_buttons: dict[str, QPushButton] = {}
        self.section_titles: dict[str, QLabel] = {}
        row = 0
        for section, names in render_config.SECTIONS:
            heading = QLabel(section)
            heading.setStyleSheet(
                f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
                f'color: {TEXT_DIM}; font-weight: bold; padding-top: {scaled(6)}px;'
            )
            self.section_titles[section] = heading
            grid.addWidget(heading, row, 0, 1, 3)
            row += 1
            for name in names:
                label = QLabel(es.SHOTS_KIND.label(name))
                label.setToolTip(_RENDER_TIPS[name])
                grid.addWidget(label, row, 0)
                editor = self._make_editor(name)
                editor.setToolTip(_RENDER_TIPS[name])
                editor.committed.connect(lambda value, n=name: self._on_committed(n, value))
                grid.addWidget(editor, row, 1)
                inherit = _button('Inherit', lambda n=name: self._on_inherit(n))
                grid.addWidget(inherit, row, 2)
                origin = _hint()
                grid.addWidget(origin, row + 1, 1, 1, 2)
                self.editors[name] = editor
                self.origins[name] = origin
                self.inherit_buttons[name] = inherit
                row += 2
        grid.setColumnStretch(1, 1)
        layout.addWidget(host)
        self.effect = _hint()
        layout.addWidget(self.effect)
        layout.addWidget(_hint(_RENDER_ORDER_NOTE))
        layout.addStretch()

    def _make_editor(self, name: str) -> _Field:
        if name == es.RENDER_APPLY:
            return _CheckField('')
        if name == es.RENDER_RESOLUTION:
            return _ResolutionField(self.presets, self._no_wheel)
        if name == es.RENDER_CAMERA:
            return _TextField('/cameras/render_camera')
        if name == render_config.AOVS:
            return _AovsField()
        kind = render_config.KARMA[render_config.split(name)[1]]
        if kind == 'bool':
            return _ChoiceField()
        return _NumberField(kind, self._no_wheel)

    def presets(self) -> list:
        """The resolution presets, the project's resolution first (what
        all shots inherits from the project, or the pipeline defaults)."""
        sheet = self._pane.sheet
        return render_config.resolution_presets(
            sheet.base.for_everyone.values.get(es.RENDER_RESOLUTION)
        )

    # ── What it edits ────────────────────────────────────

    def targets(self) -> tuple[str, list]:
        """What it edits, as sheet cell targets: ``('everyone',
        [EVERYONE])`` (all shots), ``('rows', ids)``, ``('groups',
        [GroupKey, ...])`` or ``('none', [])``."""
        pane = self._pane
        if self.all_shots_button.isChecked():
            return 'everyone', [es.EVERYONE]
        sheet = pane.sheet
        rows = [i for i in pane.selected_row_ids() if not sheet.row(i).retire]
        if rows:
            return 'rows', rows
        groups = pane.selected_groups()
        if groups:
            return 'groups', [GroupKey(g) for g in groups]
        return 'none', []

    def _renders(self, target: es.Target, name: str):
        """The value of ``name`` a render of the target gets from the root
        layer: None for a Karma setting only the pipeline defaults hold."""
        cell = self._pane.sheet.cell(target, name)
        if cell.state in (es.INHERITED, es.CLEARED) and render_config.unset_default(
                name, cell.source):
            return None
        return cell.value

    @staticmethod
    def _level(mode: str) -> str:
        return {'everyone': render_config.ALL_SHOTS_LEVEL, 'groups': 'sequence'}.get(mode, 'shot')

    def _target_text(self, mode: str, targets: list) -> str:
        if mode == 'everyone':
            return (
                'All shots: what every sequence and shot inherits unless it '
                'sets its own. It inherits from the project, then the '
                'pipeline defaults.'
            )
        if mode == 'groups':
            if len(targets) == 1:
                return f'Sequence {targets[0].name}: what its shots inherit'
            return f'{len(targets)} sequences: what their shots inherit'
        if mode == 'rows':
            sheet = self._pane.sheet
            if len(targets) == 1:
                row = sheet.row(targets[0])
                return f'Shot {row.group}/{row.name}'
            return f'{len(targets)} shots'
        return 'Select shots or a sequence\'s row, or press All shots.'

    # ── Showing ──────────────────────────────────────────

    def fill(self) -> None:
        mode, targets = self.targets()
        self._filling = True
        try:
            self.target.setText(self._target_text(mode, targets))
            level = self._level(mode)
            tip = (
                f'Take the value set here away: it inherits from '
                f'{render_config.weaker_levels(level)} again.'
            )
            for name in es.RENDER_FIELDS:
                self.inherit_buttons[name].setToolTip(tip)
                self._fill_field(name, targets)
            effects = set()
            for target in targets:
                values = {
                    name: self._pane.sheet.cell(target, name).value
                    for name in render_config.IMAGE_FIELDS
                }
                karma = {
                    name: value for name in render_config.PINNED_FIELDS
                    if (value := self._renders(target, name)) is not None
                }
                effects.add(render_config.effect(values, karma))
            if not targets:
                self.effect.setText('')
            elif len(effects) == 1:
                text = effects.pop()
                if mode != 'rows':
                    text = f'A shot that inherits all of these: {text}'
                self.effect.setText(text)
            else:
                self.effect.setText(
                    'The selected shots render differently; select one to see how.'
                )
        finally:
            self._filling = False

    def _fill_field(self, name: str, targets: list) -> None:
        editor = self.editors[name]
        origin = self.origins[name]
        inherit = self.inherit_buttons[name]
        editor.setEnabled(bool(targets))
        if not targets:
            editor.show_value(None, mixed=False)
            editor.setStyleSheet('')
            origin.setText('')
            inherit.setEnabled(False)
            return
        found = [self._pane.sheet.cell(target, name) for target in targets]
        values = [self._renders(target, name) for target in targets]
        mixed = any(value != values[0] for value in values[1:])
        unset = _NOT_SET
        editor.show_value(None if mixed else values[0], mixed, unset)
        origins = {render_origin(cell.state, cell.source, name) for cell in found}
        text = origins.pop() if len(origins) == 1 else 'differs between them'
        if name == es.RENDER_APPLY and not mixed and values[0] is None:
            text += ' (off)'
        origin.setText(text)
        pending = any(cell.state in (es.SET, es.CLEARED) for cell in found)
        origin.setStyleSheet(
            _HINT_STYLE.replace(TEXT_DIM, WARNING) if pending else _HINT_STYLE
        )
        # Inherited values read as the sheet's inherited cells do.
        inherited = all(cell.state in (es.INHERITED, es.CLEARED) for cell in found)
        editor.setStyleSheet(f'color: {_INHERITED.color().name()};' if inherited else '')
        inherit.setEnabled(any(cell.state in (es.OWN, es.SET) for cell in found))

    # ── Editing ──────────────────────────────────────────

    def _write(self, name: str, value=_UNPIN) -> None:
        """Set ``value`` on every target (_UNPIN: take the pin away), as
        one undo step."""
        _mode, targets = self.targets()
        if not targets:
            return
        pane = self._pane
        sheet = pane.sheet

        def change():
            for target in targets:
                try:
                    if value is _UNPIN:
                        sheet.reset(target, name)
                    else:
                        sheet.set_value(target, name, value)
                except ValueError as exc:
                    pane._errors.append(str(exc))
                    return
        pane.edit(change)
        # pane.edit refreshes the sheet, which fills this; a refused value
        # is put back to what the targets hold.
        self.fill()

    def _on_committed(self, name: str, value) -> None:
        if self._filling:
            return
        self._write(name, value)

    def _on_inherit(self, name: str) -> None:
        self._write(name, _UNPIN)
