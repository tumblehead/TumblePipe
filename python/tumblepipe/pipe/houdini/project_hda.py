"""Project HDAs: turn a subnet into a digital asset stored in the project.

``<project>/otls/`` is on ``HOUDINI_OTLSCAN_PATH`` (``hpm.toml`` [runtime]),
so every session and farm task of the project loads the HDAs saved there.
The network editor's right-click menu (``OPmenu.xml``) offers
**TumblePipe ▸ Create Project HDA...** on any node that can become an asset;
the artist gives a name and everything else follows from it:

- the type is ``<project>::<name>::<major>.0``, where ``<project>`` is the
  project folder's name as a namespace (``HideAndReek`` -> ``hideandreek``);
- the version is ``1.0`` for a new name, and one major above the newest
  existing version when the name is taken, so an existing asset is never
  overwritten and scenes using it keep their version;
- names are compared ignoring case (Windows and macOS file systems fold
  it), and a taken name keeps the spelling it has: ``Mesh`` typed after
  ``mesh`` 1.0 makes ``mesh`` 2.0, the same version family;
- the file is ``<project>/otls/<category>_<project>.<name>.<version>.hda``,
  one file per version, the way the package's own ``otls/`` is laid out;
- the subnet's spare parameters become the asset's interface, the tab menu
  entry is filed under the project's name, and the node is left matching
  its new definition.

The naming half of this module is pure, so ``tests/test_project_hda.py``
tests it without Houdini; ``scripts/verify_project_hda.py`` drives the
Houdini half in hython, from the menu entry to a fresh session.
"""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import NamedTuple
from xml.sax.saxutils import escape

from tumblepipe.util.houdini import hou

#: The project folder project HDAs are saved to, and the scan path names.
OTLS_DIR = 'otls'

#: What a project HDA name may be: an identifier, so it reads the same as a
#: type name, a file name and a node name.
_IDENTIFIER = r'[A-Za-z][A-Za-z0-9_]*'
_NAME = re.compile(rf'{_IDENTIFIER}\Z')
#: A Houdini type version: dot-separated numbers (``1.0``, ``2``, ``1.0.3``).
_VERSION_TEXT = r'\d+(?:\.\d+)*'
_VERSION = re.compile(rf'{_VERSION_TEXT}\Z')


def project_namespace(project_name: str) -> str:
    """The HDA namespace for a project: its name, lowercased, identifier-safe."""
    namespace = re.sub(r'[^a-z0-9_]', '', project_name.lower())
    if not namespace:
        return 'project'
    if not namespace[0].isalpha():
        return f'p{namespace}'
    return namespace


def is_valid_name(name: str) -> bool:
    return name.isascii() and _NAME.match(name) is not None


def label_for(name: str) -> str:
    """The tab-menu label of a name: ``mesh_cleanup`` -> ``Mesh Cleanup``."""
    words = [word for word in name.split('_') if word]
    return ' '.join(word[0].upper() + word[1:] for word in words)


def _version_numbers(version: str) -> tuple[int, ...]:
    """``1.0.3`` -> ``(1, 0, 3)``; ``()`` for anything that is not a version."""
    if _VERSION.match(version) is None:
        return ()
    return tuple(int(part) for part in version.split('.'))


def next_version(existing: list[str]) -> str:
    """``1.0`` for a new name, else one major above the newest existing one.

    Strings that are not versions (an unversioned type's ``''``) are ignored.
    """
    majors = [numbers[0] for numbers in map(_version_numbers, existing) if numbers]
    return f'{max(majors, default=0) + 1}.0'


def file_name(category: str, namespace: str, name: str, version: str) -> str:
    """``sop_hideandreek.mesh_cleanup.1.0.hda`` for a Sop type."""
    return f'{category.lower()}_{namespace}.{name}.{version}.hda'


class Taken(NamedTuple):
    """A version of a name already in use, spelled as its type or file is."""
    name: str
    version: str


def taken_in_files(file_names: list[str], category: str,
                   namespace: str, name: str) -> list[Taken]:
    """The versions of ``name`` that :func:`file_name` files hold.

    Matched ignoring ASCII case: Windows and macOS file systems fold it, so
    the file of ``Mesh`` 1.0 *is* the file of ``mesh`` 1.0. Names are
    identifiers and versions numbers, so a file named any other way (a
    ``.bak`` copy, another asset) is not a version.
    """
    pattern = re.compile(
        rf'{re.escape(category.lower())}_{re.escape(namespace)}'
        rf'\.({_IDENTIFIER})\.({_VERSION_TEXT})\.hda',
        re.ASCII | re.IGNORECASE,
    )
    return [
        Taken(match.group(1), match.group(2))
        for match in map(pattern.fullmatch, file_names)
        if match is not None and match.group(1).lower() == name.lower()
    ]


@dataclass(frozen=True)
class Plan:
    #: The name as created: the spelling a taken name already has, else as
    #: given.
    name: str
    type_name: str
    label: str
    version: str
    path: Path
    #: The versions already taken, oldest first; empty for a new name.
    existing: tuple[str, ...]


def plan(category: str, project_path: Path, name: str,
         taken: list[Taken]) -> Plan:
    """What creating ``name`` in ``category`` would make.

    A taken name keeps the spelling of its newest version, so the new
    version joins that family whatever case it was typed in.
    """
    namespace = project_namespace(project_path.name)
    if taken:
        name = max(taken, key=lambda t: (_version_numbers(t.version), t.name)).name
    version = next_version([t.version for t in taken])
    existing = {t.version for t in taken if _version_numbers(t.version)}
    return Plan(
        name=name,
        type_name=f'{namespace}::{name}::{version}',
        label=label_for(name),
        version=version,
        path=project_path / OTLS_DIR / file_name(category, namespace, name, version),
        existing=tuple(sorted(existing, key=lambda v: (_version_numbers(v), v))),
    )


# ── Houdini ──────────────────────────────────────────────────────────────────

def _project_path() -> Path | None:
    from tumblepipe.api import get_project_path
    try:
        return get_project_path()
    except AssertionError:
        return None


def can_create(node) -> bool:
    """Whether the menu entry applies: a node that can become an asset, in a
    session that has a project."""
    return node is not None and node.canCreateDigitalAsset() and _project_path() is not None


def find_taken(category, project_path: Path, name: str) -> list[Taken]:
    """The versions of ``name`` in use, in any case: installed types, and
    files in the project's otls folder (a teammate's asset this session has
    not loaded)."""
    namespace = project_namespace(project_path.name)
    taken = [
        Taken(type_name, version)
        for scope, ns, type_name, version in (
            node_type.nameComponents()
            for node_type in category.nodeTypes().values()
        )
        if not scope and ns.lower() == namespace and type_name.lower() == name.lower()
    ]
    otls = project_path / OTLS_DIR
    if otls.is_dir():
        files = [entry.name for entry in otls.iterdir()]
        taken += taken_in_files(files, category.name(), namespace, name)
    return taken


def plan_for(node, name: str) -> Plan:
    category = node.type().category()
    project_path = _project_path()
    return plan(category.name(), project_path, name,
                find_taken(category, project_path, name))


def create(node, name: str):
    """Make ``node`` the first instance of a new project HDA called ``name``,
    or of the next version of the taken name it matches.

    Returns the node (createDigitalAsset replaces the subnet with an
    instance of the new type).
    """
    if not is_valid_name(name):
        raise ValueError(f'Not a valid HDA name: {name!r}')
    target = plan_for(node, name)
    if target.path.exists():
        raise FileExistsError(str(target.path))
    target.path.parent.mkdir(parents=True, exist_ok=True)

    # The subnet's spare parameters are its interface; createDigitalAsset
    # leaves them as spare parms on this one instance, so the definition
    # takes the whole template group, as the Type Properties' "promote spare
    # parameters" does. Values carry over: the parms keep their names.
    parm_template_group = node.parmTemplateGroup()
    max_inputs = 0
    if node.inputs():
        max_inputs = max(c.inputIndex() for c in node.inputConnections()) + 1
    location = target.path.as_posix()

    # No backups: the file is new, yet each save below would leave one in
    # otls/backup/. createDigitalAsset and setParmTemplateGroup can be told;
    # addSection and the tool edit cannot, so theirs are removed after.
    node = node.createDigitalAsset(
        name=target.type_name,
        hda_file_name=location,
        description=target.label,
        min_num_inputs=0,
        max_num_inputs=max_inputs,
        ignore_external_references=True,
        create_backup=False,
    )
    definition = node.type().definition()
    definition.setParmTemplateGroup(parm_template_group, create_backup=False)
    submenu = _project_path().name
    _set_tab_submenu(definition, submenu)
    hou.hda.installFile(location)
    node.matchCurrentDefinition()
    node.setName(target.name, unique_name=True)
    _remove_backups(target.path)
    return node


def _remove_backups(path: Path) -> None:
    """Delete the backups Houdini made of ``path`` while it was created."""
    backup = path.parent / 'backup'
    if not backup.is_dir():
        return
    for file in backup.glob(f'{path.stem}_bak*{path.suffix}'):
        file.unlink()
    if not any(backup.iterdir()):
        backup.rmdir()


#: Shelf network types whose name is not the category's, uppercased.
_VIEWER_NET_TYPES = {'Object': 'OBJ', 'Driver': 'ROP'}


def tool_shelf(category: str, submenu: str) -> str:
    """The ``Tools.shelf`` section of a project HDA: its tab menu entry.

    The default tool createDigitalAsset makes lives only in the session (no
    section is written), so a later session would file the asset under
    *Digital Assets*; the section makes the submenu part of the asset.
    """
    module = f'{category.lower()}toolutils'
    net_type = _VIEWER_NET_TYPES.get(category, category.upper())
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<shelfDocument>
  <tool name="$HDA_DEFAULT_TOOL" label="$HDA_LABEL" icon="$HDA_ICON">
    <toolMenuContext name="viewer">
      <contextNetType>{net_type}</contextNetType>
    </toolMenuContext>
    <toolMenuContext name="network">
      <contextOpType>$HDA_TABLE_AND_NAME</contextOpType>
    </toolMenuContext>
    <toolSubmenu>{escape(submenu)}</toolSubmenu>
    <script scriptType="python"><![CDATA[import {module}

{module}.genericTool(kwargs, '$HDA_NAME')]]></script>
  </tool>
</shelfDocument>
'''


def _set_tab_submenu(definition, submenu: str) -> None:
    node_type = definition.nodeType()
    category = node_type.category().name()
    definition.addSection('Tools.shelf', tool_shelf(category, submenu))
    # The section serves later sessions; this one already has the default
    # tool in memory.
    tool = hou.shelves.tool(hou.shelves.defaultToolName(category, node_type.name()))
    if tool is not None:
        tool.setToolLocations((submenu,))


def show_dialog(node) -> None:
    """The right-click entry: ask for a name, then :func:`create`."""
    dialog = _create_dialog(node, parent=hou.qt.mainWindow())
    if not dialog.exec():
        return
    name = dialog.name()
    try:
        created = create(node, name)
    except Exception as error:  # noqa: BLE001 — shown in the dialog, not raised
        hou.ui.displayMessage(
            f'Could not create the project HDA "{name}".',
            severity=hou.severityType.Error,
            title='Create Project HDA',
            details=str(error),
        )
        return
    created.setSelected(True, clear_all_selected=True)
    definition = created.type().definition()
    hou.ui.setStatusMessage(
        f'Created {created.type().name()} in {definition.libraryFilePath()}',
        severity=hou.severityType.Message,
    )


@cache
def _dialog_class():
    """The name dialog's class, defined on first use: PySide6 is imported
    only when the dialog is shown, never by a headless import."""
    from PySide6 import QtCore, QtWidgets

    class CreateDialog(QtWidgets.QDialog):
        def __init__(self, node, parent=None):
            super().__init__(parent)
            self._node = node
            self.setWindowTitle('Create Project HDA')
            self.setMinimumWidth(420)

            self._name = QtWidgets.QLineEdit()
            self._name.setPlaceholderText('e.g. mesh_cleanup')
            self._name.setText(node.name() if is_valid_name(node.name()) else '')
            self._name.selectAll()
            self._summary = QtWidgets.QLabel()
            self._summary.setWordWrap(True)
            self._summary.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

            buttons = QtWidgets.QDialogButtonBox()
            self._create = buttons.addButton(
                'Create', QtWidgets.QDialogButtonBox.AcceptRole)
            buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)

            form = QtWidgets.QFormLayout()
            form.addRow('Name', self._name)
            layout = QtWidgets.QVBoxLayout(self)
            layout.addLayout(form)
            layout.addWidget(self._summary)
            layout.addWidget(buttons)

            self._name.textChanged.connect(self._update)
            self._update()

        def name(self) -> str:
            return self._name.text().strip()

        def _update(self):
            name = self.name()
            if not name:
                self._summary.setText('Give the asset a name.')
                self._create.setEnabled(False)
                return
            if not is_valid_name(name):
                self._summary.setText(
                    'Letters, digits and underscores, starting with a letter.')
                self._create.setEnabled(False)
                return
            target = plan_for(self._node, name)
            lines = [
                f'Type: <b>{target.type_name}</b>',
                f'File: {OTLS_DIR}/{target.path.name}',
            ]
            if target.existing:
                lines.append(
                    f'{target.name} already exists ({", ".join(target.existing)}); '
                    f'this creates version {target.version}.')
            if target.name != name:
                lines.append(
                    f'Same name as the existing {target.name}, so its spelling is kept.')
            self._summary.setText('<br>'.join(lines))
            self._create.setEnabled(True)

    return CreateDialog


def _create_dialog(node, parent=None):
    return _dialog_class()(node, parent)
