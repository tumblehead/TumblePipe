"""Creating, editing and deleting assets and shots, and the per-entity
actions behind their menus.

:class:`EntityOps` holds every write the browser makes to an asset or shot
— the creation dialog (assets, shots, categories, sequences, Multis, Roots
and recipes), the Edit dialog, Delete, an empty bucket's removal, the
description sidecar, Duplicate, Generate Master — and the actions that open
something about one (Departments…, the Database Editor, the todos, the Farm
Submit grid, an import node). The catalog routes container, recipe and node
ids before anything reaches here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from tumbletrove.asset_browser.api.errors import CatalogInitError, ConfigError
from tumbletrove.asset_browser.api.types import (
    Asset, AssetDetail, CreationField,
)

from tumblepipe.pipe.houdini.ui.report import (
    report_failure, reporting, run_in_background, run_on_main_thread,
)

from tumblepipe.config import shot_batch

from . import creation_check
from . import edits as edits
from . import nodes as nodes
from . import recipes as recipes
from .libraries import TAG_KEY as LIBRARY_TAG_KEY
from . import uris as uris
from .scene import open_submit_jobs_dialog

log = logging.getLogger(__name__)

#: The Entity Editor's entry in the creation lists. It opens the editor
#: rather than the browser's generic form; see
#: :meth:`EntityOps.creation_fields`.
ENTITY_EDITOR = "entity_editor"
#: Import Shots from CSV… (#32): a file dialog and the import's plan, not
#: the generic form; see :meth:`EntityOps.import_shots_csv`.
IMPORT_SHOTS_CSV = "import_shots_csv"
PLAY_SEQUENCE = "play_sequence"


def stage_roots_after_apply() -> str:
    """The Entity Editor's ``stage_roots`` after an Apply: the trigger the
    Database Editor's saves go through too (``pipe.root_staging.
    sync_after_edit``), for every shot whose Root or render settings the
    Apply changed. Failures are logged; returns the footer's summary."""
    from tumblepipe.pipe.root_staging import sync_after_edit
    result = sync_after_edit()
    for uri, why in result.failures:
        log.warning("Staging Roots: %s: %s", uri, why)
    return result.summary()


# The edit card's Rename and Delete ask through these (#62); the tests
# replace them, so the flows run headless.

def ask_text(title: str, label: str, text: str = "") -> str | None:
    """A line of text from the artist, None on Cancel."""
    import hou
    from PySide6.QtWidgets import QInputDialog
    value, ok = QInputDialog.getText(hou.qt.mainWindow(), title, label, text=text)
    return value if ok else None


def confirm(title: str, text: str) -> bool:
    """Whether the artist confirms a destructive step (Cancel is the
    default button)."""
    import hou
    from PySide6.QtWidgets import QMessageBox
    answer = QMessageBox.question(
        hou.qt.mainWindow(), title, text,
        QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
    )
    return answer == QMessageBox.Yes


def tell(text: str) -> None:
    """Why something was refused."""
    import hou
    hou.ui.displayMessage(text, severity=hou.severityType.Warning)


if TYPE_CHECKING:
    from .projects import PipelineProjectRegistry
    from .clients import ClientPool
    from .dept_scope import DepartmentScope
    from .discovery import EntityIndex, ProjectBuckets
    from .houdini import ProjectActivator
    from .recipes import RecipeManager
    from .refresh import BrowserRefresh
    from .resolver import AssetResolver
    from .scene import SceneManager
    from .containers import ContainerManager
    from .workfiles import WorkfileManager


def only_project(names: list[str]) -> str:
    """The project to open without asking: the only one registered, else ''.

    The Entity Editor asked "Project:" from a one-item combo whenever the
    scope named no project, an extra dialog and click every time (#35).
    """
    return names[0] if len(names) == 1 else ""


def sequence_shots(entity_uris, sequence: str) -> list:
    """The shots of *sequence* among *entity_uris*, in name order: what
    Play Sequence plays (#26)."""
    return sorted(
        (
            uri for uri in entity_uris
            if len(uri.segments) == 3
            and uri.segments[0] == "shots" and uri.segments[1] == sequence
        ),
        key=lambda uri: uri.segments[2],
    )


def creation_scope(tags) -> str:
    """What a browser scope creates: ``assets``, ``shots``, ``group``,
    ``scene``, ``recipes``, ``nodes`` (which creates nothing: nodes ship
    with the package) or ``edits``, or ``""`` when it is not narrowed to
    one.

    *tags* are the browser's filter atoms: the active view's merged tags,
    or a sidebar node's own tag split on ``+``. The browser offers the
    same creation options in both places, the "+" card and the right-click
    menu of the node you selected. So the scope has to read every sidebar
    tag right. A category is an asset scope even without ``type:asset``,
    and a Multi's member drill (``group:<project>:<ctx>/<name>``) is the
    scope of its context.
    """
    tags = frozenset(tags)
    if "type:group" in tags:
        return "group"
    if "type:scene" in tags:
        return "scene"
    if recipes.TYPE_TAG in tags:
        return "recipes"
    if any(t.startswith(f"{LIBRARY_TAG_KEY}:") for t in tags):
        # Recipes are the one kind created straight into a library.
        return "recipes"
    if nodes.TYPE_TAG in tags:
        return "nodes"
    if edits.TYPE_TAG in tags:
        return "edits"
    assets = "type:asset" in tags
    shots = "type:shot" in tags
    for tag in tags:
        key, _, value = tag.partition(":")
        if key == "category":
            assets = True
        elif key == "sequence":
            shots = True
        elif key == "group":
            ctx = value.partition(":")[2].partition("/")[0]
            assets = assets or ctx == "assets"
            shots = shots or ctx == "shots"
    if assets != shots:
        return "assets" if assets else "shots"
    return ""


def asset_context(asset: Asset) -> str | None:
    """``'shots'`` or ``'assets'`` for an entity card, None for any other."""
    tags = asset.tags or ()
    if "type:shot" in tags:
        return "shots"
    if "type:asset" in tags:
        return "assets"
    return None


class EntityOps:
    """Entity writes and per-entity actions for the Pipeline catalog."""

    def __init__(
        self,
        catalog_id: str,
        *,
        registry: "PipelineProjectRegistry",
        clients: "ClientPool",
        resolver: "AssetResolver",
        activator: "ProjectActivator",
        buckets: "ProjectBuckets",
        index: "EntityIndex",
        dept_scope: "DepartmentScope",
        scene: "SceneManager",
        recipes: "RecipeManager",
        refresh: "BrowserRefresh",
        invalidate_cache: Callable[[], None],
        workfiles: "WorkfileManager | None" = None,
        containers: "ContainerManager | None" = None,
    ) -> None:
        self._catalog_id = catalog_id
        # A Multi's workfiles, for the Entity Editor's Multis tab.
        self._workfiles = workfiles
        self._containers = containers
        self._registry = registry
        self._clients = clients
        self._resolver = resolver
        self._activator = activator
        self._buckets = buckets
        self._index = index
        self._dept_scope = dept_scope
        self._scene = scene
        self._recipes = recipes
        self._refresh = refresh
        self._invalidate_cache = invalidate_cache
        # One Entity Editor per project, raised when opened again.
        self._entity_editors: dict = {}

    def collection_from_assets_option(self):
        """The shelf's "Create Root from Items…": ``new_scene``, whose
        :meth:`create` returns the new Root's collection id."""
        from tumbletrove.asset_browser.api.catalog import CreationOption
        return CreationOption("new_scene", "Create Root from Items…", "layers")

    def creation_options(self, tags=frozenset()):
        """What can be created in the scope *tags*.

        The browser shows this list on the grid's "+" card, the list's
        "+" row, both empty-space menus, and every sidebar node's
        right-click menu. Only the scope decides the list. A category
        offered "New Shot..." when only ``type:*`` tags were read.
        "New Category..." and "New Sequence..." are listed here, not
        only in the sidebar, so the views can reach them too.
        """
        from tumbletrove.asset_browser.api.catalog import CreationOption
        asset = CreationOption("new_asset", "New Asset...", "plus")
        category = CreationOption("new_category", "New Category...", "folder")
        shot = CreationOption("new_shot", "New Shot...", "plus")
        editor = CreationOption(ENTITY_EDITOR, "Entity Editor...", "list")
        sequence = CreationOption("new_sequence", "New Sequence...", "folder")
        multi = CreationOption("new_group", "New Multi...", "users")
        root = CreationOption("new_scene", "New Root...", "layers")
        csv_import = CreationOption(
            IMPORT_SHOTS_CSV, "Import Shots from CSV...", "file-input",
        )
        recipe = CreationOption(recipes.CREATE_OPTION, "New Recipe...", "puzzle")
        edit = CreationOption(edits.CREATE_OPTION, "New Edit...", edits.ICON)
        scope = creation_scope(tags)
        if scope == "group":
            return [multi]
        if scope == "scene":
            return [root]
        if scope == "recipes":
            return [recipe]
        if scope == "nodes":
            # Nodes ship with the package.
            return []
        if scope == "edits":
            return [edit]
        if scope == "assets":
            return [asset, editor, category, multi, root]
        if scope == "shots":
            options = [shot, editor, sequence, csv_import, multi, root]
            # On one sequence (its sidebar row, or the view narrowed to
            # it): play its shots in the editor (#26). An action, not a
            # creation, so it leads the list.
            if any(t.startswith("sequence:") for t in tags):
                options.insert(0, CreationOption(PLAY_SEQUENCE, "Play Sequence", "play"))
            return options
        # Unscoped (a project's own row, the catalog's root): New Edit...
        # too, or a project's first edit (the Edits section is there only
        # once it has one) could be made only in the editor.
        return [asset, shot, editor, recipe, edit, multi, root]

    def node_drop_creation_option(self, tags=frozenset()):
        """Nodes dragged from a network editor onto the grid save a recipe.

        The browser asks this, then runs New
        Recipe… exactly as if picked from the "+" card, and
        :meth:`create` reads ``hou.selectedNodes()`` — the drop
        is the gesture, the selection is the data. Any pipeline scope
        will do: the form binds the project from *tags* or the single
        registered one. Older hosts never call this, and there New
        Recipe… stays on the "+" menu.
        """
        return recipes.CREATE_OPTION

    def _resolve_project_from_tags(self, tags):
        """Extract project name from tags like 'project:growth'."""
        for t in tags:
            if t.startswith("project:"):
                return t.split(":", 1)[1]
        # Single project fallback
        projects = list(self._registry.all())
        if len(projects) == 1:
            return projects[0].name
        return ""

    def creation_fields(self, option_id, tags=frozenset()):
        from tumbletrove.asset_browser.api.catalog import CreationField
        projects = [p.name for p in self._registry.all()]
        default_proj = self._resolve_project_from_tags(tags)

        if option_id == recipes.CREATE_OPTION:
            default_library = next(
                (t.partition(":")[2] for t in tags
                 if t.startswith(f"{LIBRARY_TAG_KEY}:")), "")
            return self._recipes.creation_fields(
                default_proj, projects, default_library)

        if option_id == edits.CREATE_OPTION:
            fields = [CreationField("name", "Name", required=True)]
            if len(projects) > 1:
                fields.append(CreationField(
                    "project", "Project",
                    field_type="dropdown",
                    choices=tuple(projects),
                    default=default_proj,
                ))
            return fields

        if option_id == PLAY_SEQUENCE:
            # No form: the editor opens on the sequence's shots.
            seq = next(
                (t.partition(":")[2] for t in tags if t.startswith("sequence:")), "",
            )
            if seq:
                self.play_sequence(default_proj, seq)
            return []

        if option_id == IMPORT_SHOTS_CSV:
            # Like the Entity Editor: no form. The file dialog is modal, so
            # it opens once the browser's callback has returned.
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.import_shots_csv(default_proj))
            return []

        if option_id == ENTITY_EDITOR:
            # The browser asks for an option's fields only once the artist
            # has picked it (the "+" card, a menu), on the GUI thread, and
            # shows no form and creates nothing when the list is empty. So
            # this is the moment to open the editor instead of the form,
            # on the Assets tab from an assets scope and Shots otherwise.
            kind = "assets" if creation_scope(tags) == "assets" else "shots"
            prefix = "category:" if kind == "assets" else "sequence:"
            group = next(
                (t.partition(":")[2] for t in tags if t.startswith(prefix)),
                "",
            )
            self.open_entity_editor(default_proj, kind, group)
            return []

        if option_id == "new_asset":
            fields = [
                CreationField("name", "Name", required=True),
            ]
            # Category dropdown — strict (no free-text). A new category
            # is its own option, ``new_category``, offered in every asset
            # scope.
            cats = (
                self._buckets.categories(default_proj)
                if default_proj else self._buckets.all_categories()
            )
            # When invoked from a category row's right-click menu the
            # caller passes a ``category:<name>`` tag; lock the field so
            # the user sees the bound context but can't redirect it.
            # Tags now carry the original-case category (matching the
            # URI segments, e.g. ``entity:/assets/CHAR/...``); the
            # case-insensitive fallback only recovers stale lowercase
            # tags persisted by old presets / drag payloads.
            default_cat = ""
            for t in tags:
                if t.startswith("category:"):
                    tag_val = t.split(":", 1)[1]
                    if tag_val in cats:
                        default_cat = tag_val
                    else:
                        default_cat = next(
                            (c for c in cats if c.lower() == tag_val.lower()),
                            tag_val,
                        )
                    break
            fields.append(CreationField(
                "category", "Category",
                field_type="dropdown",
                choices=tuple(cats),
                default=default_cat,
                allow_new=False,
                readonly=bool(default_cat),
            ))
            if len(projects) > 1:
                fields.append(CreationField(
                    "project", "Project",
                    field_type="dropdown",
                    choices=tuple(projects),
                    default=default_proj,
                ))
            return fields

        if option_id == "new_shot":
            fields = [
                CreationField("name", "Name", required=True),
            ]
            seqs = (
                self._buckets.sequences(default_proj)
                if default_proj else self._buckets.all_sequences()
            )
            default_seq = ""
            for t in tags:
                if t.startswith("sequence:"):
                    default_seq = t.split(":", 1)[1]
                    break
            fields.append(CreationField(
                "sequence", "Sequence",
                field_type="dropdown",
                choices=tuple(seqs),
                default=default_seq,
                allow_new=False,
                readonly=bool(default_seq),
            ))
            fields.append(CreationField(
                "frame_start", "Frame Start",
                field_type="int", default="1001",
            ))
            fields.append(CreationField(
                "frame_end", "Frame End",
                field_type="int", default="1100",
            ))
            if len(projects) > 1:
                fields.append(CreationField(
                    "project", "Project",
                    field_type="dropdown",
                    choices=tuple(projects),
                    default=default_proj,
                ))
            return fields

        if option_id == "new_category":
            # The only place free-text category entry survives. Offered
            # as "New Category..." in any asset scope, both on "+" and on
            # a sidebar node's menu. Creates an empty category-only entity
            # (``entity:/assets/<name>``) — tumblepipe supports
            # parent-only entities, so no first asset is required.
            fields = [
                CreationField("category", "Category", required=True),
            ]
            if len(projects) > 1:
                fields.append(CreationField(
                    "project", "Project",
                    field_type="dropdown",
                    choices=tuple(projects),
                    default=default_proj,
                ))
            return fields

        if option_id == "new_sequence":
            # Symmetric with new_category: creates an empty
            # sequence-only entity (``entity:/shots/<name>``). Shot
            # frame ranges live on individual shot entities, not the
            # sequence parent, so no frame fields here.
            fields = [
                CreationField("sequence", "Sequence", required=True),
            ]
            if len(projects) > 1:
                fields.append(CreationField(
                    "project", "Project",
                    field_type="dropdown",
                    choices=tuple(projects),
                    default=default_proj,
                ))
            return fields

        if option_id == "new_group":
            # A Multi is set up in the Entity Editor: New Multi... opens it
            # on its Multis tab and asks for a new pending Multi there, in
            # the context the artist is in. A Multis section under Assets
            # or Shots binds the context; anywhere else narrowed to one
            # context picks it. It used to default to "shots" everywhere,
            # so "New Multi" under Assets made a shot Multi.
            bound_ctx = next(
                (t.partition(":")[2] for t in tags
                 if t.startswith("multi_context:")),
                "",
            )
            if bound_ctx not in ("shots", "assets"):
                bound_ctx = ""
            scope = creation_scope(tags)
            default_ctx = bound_ctx or (
                scope if scope in ("shots", "assets") else "shots"
            )
            # Picked on a category or sequence row: offer that one whole.
            prefix = "category:" if default_ctx == "assets" else "sequence:"
            group = next(
                (t.partition(":")[2] for t in tags if t.startswith(prefix)),
                "",
            )
            self.open_new_multi(default_proj, default_ctx, group)
            return []

        if option_id == "new_scene":
            fields = [
                CreationField("name", "Name", required=True),
            ]
            if len(projects) > 1:
                fields.append(CreationField(
                    "project", "Project",
                    field_type="dropdown",
                    choices=tuple(projects),
                    default=default_proj,
                ))
            return fields

        return []

    @staticmethod
    def _entity_paths(client) -> set[tuple[str, ...]]:
        """Every entity of the client's project, as URI segment tuples."""
        return {
            tuple(uri.segments)
            for uri in client.config.list_entity_uris(None, closure=True)
        }

    def validate_creation(self, option_id, fields):
        """Why the creation form's values would be refused, per field.

        TumbleTrove asks this on the GUI thread as the artist types
        (``Catalog.validate_creation``), and keeps OK disabled while there
        is a reason (#29). The rule is ``creation_check``, the one
        :meth:`create` refuses by. A project not loaded yet finds nothing:
        :meth:`create` still has the last word.
        """
        if (
            option_id not in ("new_scene", edits.CREATE_OPTION)
            and option_id not in creation_check.OPTIONS
        ):
            return {}
        proj_name = str(fields.get("project") or "")
        if not proj_name:
            projects = list(self._registry.all())
            proj_name = projects[0].name if len(projects) == 1 else ""
        if not proj_name or not self._clients.is_ready(proj_name):
            return {}
        if option_id == edits.CREATE_OPTION:
            # New Edit: the rule create_edit refuses by.
            from tumblepipe.pipe import edits as pipe_edits
            name = str(fields.get("name") or "").strip()
            if not name:
                return {}
            problem = pipe_edits.name_problem(
                name, pipe_edits.list_edits(self._clients.get(proj_name)),
            )
            return {"name": problem} if problem else {}
        if option_id == "new_scene":
            # New Root: the one refusal its create has, a Root of that name.
            name = str(fields.get("name") or "").strip()
            proj = self._registry.get(proj_name)
            if not name or proj is None:
                return {}
            self._activator.activate(proj)
            from tumblepipe.config import scene as scn_mod
            problem = creation_check.root_problem(
                name, scn_mod.get_scene_by_uri(uris.scene(name)) is not None,
            )
            return {"name": problem} if problem else {}
        client = self._clients.get(proj_name)
        return creation_check.creation_problems(
            option_id, fields, self._entity_paths(client),
        )

    def create(self, option_id, fields):
        """Create what the creation dialog describes; the new card's id or
        None. A failure raises: the browser reports it (TumbleTrove >= 0.48)."""
        import hou

        # A recipe saved into a library: no project to activate.
        if option_id == recipes.CREATE_OPTION:
            target = self._recipes.create_target(str(fields.get("project", "")))
            if target is not None:
                return self._recipes.create(target, fields)

        # Resolve project
        projects = list(self._registry.all())
        proj_name = fields.get("project", "")
        if not proj_name and len(projects) == 1:
            proj_name = projects[0].name
        proj = self._registry.get(proj_name)
        if proj is None:
            hou.ui.displayMessage(f"Unknown project: {proj_name}")
            return None

        self._activator.activate(proj)

        # A recipe is files under the project share, not a config
        # entity — no Client needed, and none is built for it.
        if option_id == recipes.CREATE_OPTION:
            return self._recipes.create(proj, fields)

        try:
            client = self._clients.get(proj_name)
        except CatalogInitError as err:
            hou.ui.displayMessage(str(err))
            return None

        if option_id == edits.CREATE_OPTION:
            return self._create_edit(proj_name, client, fields)

        # The dialog asks validate_creation as the artist types (#29);
        # this refuses by the same rule (creation_check), so the two
        # cannot disagree. A bucket name belongs to one kind only (card
        # ids are ``PROJECT/BUCKET/NAME`` and the kind is read back from
        # whether BUCKET is a category), new names are letters and digits
        # only, and nothing that exists is made twice.
        def _refused() -> bool:
            problems = creation_check.creation_problems(
                option_id, fields, self._entity_paths(client),
            )
            if problems:
                hou.ui.displayMessage("\n".join(problems.values()))
            return bool(problems)

        # Parent-only entity creation: ``new_category`` and
        # ``new_sequence`` register a 2-segment URI (no asset/shot name)
        # so the bucket exists in the sidebar even with no children
        # yet. tumblepipe supports this natively — see
        # ``ensure_sequence`` in tools/csv_shot_import.py.
        if option_id == "new_category":
            category = fields.get("category", "").strip()
            if not category:
                hou.ui.displayMessage("Category is required.")
                return None
            if _refused():
                return None
            client.config.add_entity(uris.entity_category(category), {})
            self._index.invalidate(shots=False)
            return None  # No card to select — empty category

        if option_id == "new_sequence":
            sequence = fields.get("sequence", "").strip()
            if not sequence:
                hou.ui.displayMessage("Sequence is required.")
                return None
            if _refused():
                return None
            client.config.add_entity(uris.entity_sequence(sequence), {})
            self._index.invalidate(assets=False)
            return None  # No card to select — empty sequence

        name = fields.get("name", "").strip()
        if not name:
            hou.ui.displayMessage("Name is required.")
            return None

        if option_id == "new_asset":
            category = fields.get("category", "").strip()
            if not category:
                hou.ui.displayMessage("Category is required.")
                return None
            if _refused():
                return None
            entity_uri = uris.entity_asset(category, name)
            client.config.add_entity(entity_uri, {"name": name})
            self._index.invalidate(shots=False)
            return uris.asset_id_for(proj_name, entity_uri)

        if option_id == "new_shot":
            sequence = fields.get("sequence", "").strip()
            if not sequence:
                hou.ui.displayMessage("Sequence is required.")
                return None
            if _refused():
                return None
            frame_start = int(fields.get("frame_start", "1001"))
            frame_end = int(fields.get("frame_end", "1100"))
            entity_uri = uris.entity_shot(sequence, name)
            client.config.add_entity(
                entity_uri,
                {"frame_start": frame_start, "frame_end": frame_end},
            )
            # A shot made in a sequence a Multi lists joins that Multi.
            from tumblepipe.config.groups import place_new_members
            place_new_members([entity_uri])
            self._index.invalidate(assets=False)
            return uris.asset_id_for(proj_name, entity_uri)

        if option_id == "new_group":
            context = fields.get("context", "shots").strip()
            from tumblepipe.config import groups as grp_mod
            group_uri = uris.group_in_context(context, name)
            if grp_mod.get_group(group_uri) is not None:
                hou.ui.displayMessage(
                    f"Multi '{name}' already exists in {context}."
                )
                return None
            # A Multi covers its context's whole department pool from
            # birth. Coverage is what gives the Multi card its
            # department rows and what redirects a member's rows to
            # the Multi's workfile; a Multi that covers nothing has
            # no rows, opens nothing, and reads as a folder of shots.
            # The Entity Editor's New Multi does the same. (The browser's
            # New Multi... opens the editor; this stays for scripts.)
            from tumblepipe.config.department import list_departments
            pool = [
                d.name for d in list_departments(
                    context, include_generated=False,
                )
            ]
            grp_mod.add_group(context, name, [], pool)
            hou.ui.setStatusMessage(
                f"Created Multi: {name} ({context}, covers "
                f"{len(pool)} departments)",
                severity=hou.severityType.Message,
            )
            return None  # Groups aren't assets — no card to select

        if option_id == "new_scene":
            from tumblepipe.config import scene as scn_mod
            problem = creation_check.root_problem(
                name, scn_mod.get_scene_by_uri(uris.scene(name)) is not None,
            )
            if problem:
                hou.ui.displayMessage(problem)
                return None
            scn_mod.add_scene(name)
            hou.ui.setStatusMessage(
                f"Created Root: {name}",
                severity=hou.severityType.Message,
            )
            # The Root card's id, which is also its collection id: the
            # shelf's "Create Root from Items…" adds the shelf's assets to
            # it (``collection_from_assets_option``). It used to return
            # None, so that menu entry made an empty Root.
            return f"scene:{proj_name}:{name}"

        return None

    def open_entity_editor(
        self, project_name: str, kind: str = "shots", group: str = "",
        name: str = "",
    ) -> None:
        """Open (or raise) *project_name*'s Entity Editor on its *kind* tab
        (``"assets"``, ``"shots"`` or ``"multis"``, where *group* is the
        Multi's context), showing *group* with *name*
        selected. The project is asked for when several are registered and
        the scope names none. The window, or None when no project was
        chosen.

        The editor is not modal: a modal ``exec()`` here would run inside
        the browser's own callback and stall every other one until it
        closed (the publish window froze the browser that way).
        """
        import hou
        from PySide6.QtWidgets import QInputDialog

        from tumblepipe.config.groups import place_new_members
        from tumblepipe.pipe import entity_files
        from tumblepipe.pipe.usd.instances import has_published_rig

        from .entity_editor import EntityEditorSource, EntityEditorWindow

        if not project_name:
            names = sorted(p.name for p in self._registry.all())
            project_name = only_project(names)
        if not project_name:
            project_name, ok = QInputDialog.getItem(
                hou.qt.mainWindow(), "Entity Editor", "Project:", names, 0, False,
            )
            if not ok:
                return None
        proj = self._registry.get(project_name)
        if proj is None:
            hou.ui.displayMessage(f"Unknown project: {project_name}")
            return None
        window = self._entity_editors.get(project_name)
        if window is not None:
            try:
                window.focus(kind, group, name)
                window.show()
                window.raise_()
                window.activateWindow()
                return window
            except RuntimeError:
                pass    # closed: its Qt object is gone
        self._activator.activate(proj)
        client = self._clients.get(project_name)

        def on_project(func):
            # Retire, rename and the files check work on the
            # ACTIVE project; another project may have been activated since.
            def call(*args):
                self._activator.activate(proj)
                return func(*args)
            return call

        def rename_blocker(uri) -> str:
            if not entity_files.has_entity_files(uri):
                return ""
            return (
                "Has workfiles or exports on disk, so it can't be renamed: "
                "they would stay under the old name. Use Duplicate… on its "
                "card, then retire the old one."
            )

        def multi_rename_blocker(uri) -> str:
            # A Multi's workfiles sit under groups:/<context>/<name>.
            from tumblepipe.api import api
            folder = api.storage.resolve(uri)
            if not any(p.is_file() for p in folder.rglob("*")):
                return ""
            return (
                "Has workfiles on disk, so it can't be renamed: they would "
                "stay under the old name. Retire it and add a new Multi."
            )

        on_applied = self.editor_applied

        def place(written) -> None:
            place_new_members(written)
            # A Multi whose workfile is open, its Timeline just applied:
            # lay the session out now, not on the next open.
            try:
                from tumblepipe.pipe.houdini import multi_timeline as session
            except ImportError:
                return
            for uri in written:
                if uri.purpose == "groups":
                    session.apply_if_open(uri)

        def channel_exports(uri) -> list[str]:
            # The Layers editor keeps these: their renders read the exports.
            from tumblepipe.config.channels import list_channels
            from tumblepipe.pipe.render_layers import was_exported
            return [
                channel for channel in list_channels(uri)
                if channel != "default" and was_exported(uri, channel)
            ]

        def selected_prims() -> list[str]:
            # Use Selection: the Scene Graph Tree and the viewers share it.
            for pane in hou.ui.paneTabs():
                if pane.type() != hou.paneTabType.SceneViewer:
                    continue
                getter = getattr(pane, "currentSceneGraphSelection", None)
                if getter is None:
                    continue
                try:
                    paths = [str(p) for p in getter()]
                except Exception as exc:  # noqa: BLE001  not a LOP viewer
                    log.debug("Use Selection: %s: %s", pane.name(), exc)
                    continue
                if paths:
                    return paths
            return []

        def count_prims(uri, pattern) -> int | None:
            # Counted on the open stage, only when it is this shot's: the
            # farm's matcher on another shot's stage would mislead.
            from tumblepipe.pipe.houdini.scene_context import scene_context
            from tumblepipe.pipe.usd.render_passes import expand_pattern, top_most
            try:
                context = scene_context()
                if context is None or context.entity_uri != uri:
                    return None
                display = hou.node("/stage").displayNode()
                stage = display.stage() if display is not None else None
                if stage is None:
                    return None
                return len(top_most(expand_pattern(stage, pattern)))
            except Exception:  # noqa: BLE001  a stage that will not compose
                return None

        def multi_id(uri) -> str:
            return f"group:{project_name}:{'/'.join(uri.segments)}"

        def multi_workfiles(uri) -> dict:
            if self._containers is None:
                return {}
            from .types import latest_version
            return {
                department: latest_version(versions)
                for department, versions in
                self._containers.group_department_versions(multi_id(uri)).items()
                if versions
            }

        def open_multi_workfile(uri, department) -> None:
            if self._workfiles is not None:
                self._workfiles.open_group_workfile(multi_id(uri), department)

        def new_multi_workfile(uri, department, done) -> None:
            if self._workfiles is None:
                done()
                return

            def made() -> None:
                done()
                self._refresh.detail()

            self._workfiles.new_group_from_template(multi_id(uri), department, made)

        window = EntityEditorWindow(
            EntityEditorSource(
                project=project_name, config=client.config,
                retire=on_project(entity_files.delete_entity),
                rename=on_project(entity_files.rename_entity),
                rename_blocker=on_project(rename_blocker),
                on_applied=on_applied,
                stage_roots=on_project(stage_roots_after_apply),
                rigged=on_project(has_published_rig),
                multi_rename_blocker=on_project(multi_rename_blocker),
                place=on_project(place),
                multi_workfiles=on_project(multi_workfiles),
                open_multi_workfile=open_multi_workfile,
                new_multi_workfile=new_multi_workfile,
                channel_exports=on_project(channel_exports),
                selected_prims=selected_prims,
                count_prims=on_project(count_prims),
                overlaps=on_project(self._overlaps),
                repack=on_project(self.repack_timeline),
            ),
            parent=hou.qt.mainWindow(),
            kind=kind,
        )
        self._entity_editors[project_name] = window
        window.focus(kind, group, name)
        window.show()
        return window

    def import_shots_csv(self, project_name: str, path: str = "") -> int:
        """Import Shots from CSV… (#32): pick a sheet, show what importing
        it does (the CLI dry run's lines), and write that on Import. The
        shots made; 0 when cancelled or refused.

        ``path`` skips the file dialog (scripts, tests).
        """
        import hou
        from tumblepipe.tools import csv_shot_import as tool

        projects = list(self._registry.all())
        if not project_name and len(projects) == 1:
            project_name = projects[0].name
        proj = self._registry.get(project_name)
        if proj is None:
            hou.ui.displayMessage("Pick a project first: Import Shots from CSV imports into one.")
            return 0
        qt = getattr(hou, "qt", None)  # absent outside a Houdini GUI
        parent = qt.mainWindow() if qt is not None else None
        if not path:
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(
                parent, f"Import Shots from CSV — {project_name}", "",
                "CSV sheets (*.csv);;All files (*)",
            )
            if not path:
                return 0
        self._activator.activate(proj)
        client = self._clients.get(project_name)
        try:
            sheet = tool.plan_sheet(Path(path), client.config)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            # NoHeaderRow is a ValueError: the sheet's layout, said plainly.
            hou.ui.displayMessage(f"Could not read {Path(path).name}:\n\n{error}")
            return 0
        if self.confirm_csv_import(project_name, path, sheet, parent) is not True:
            return 0
        created = tool.write_plan(client.config, sheet.plan)
        self._invalidate_cache()
        self._index.invalidate(assets=False)
        self._refresh.sidebar()
        self._refresh.grid()
        hou.ui.setStatusMessage(
            f"Imported {len(created)} shot{'s' if len(created) != 1 else ''} "
            f"from {Path(path).name}",
            severity=hou.severityType.Message,
        )
        return len(created)

    @staticmethod
    def confirm_csv_import(project_name, path, sheet, parent) -> bool:
        """The plan dialog; True on Import. Tests swap it."""
        from PySide6.QtWidgets import QDialog
        from . import csv_import
        dialog = csv_import.make_dialog(project_name, str(path), sheet, parent)
        return dialog.exec() == QDialog.Accepted

    def open_new_multi(
        self, project_name: str, context: str, group: str = "",
    ) -> None:
        """The browser's **New Multi...**: the project's Entity Editor on
        its Multis tab of ``context``, asking for a new Multi there,
        offered to start with ``group`` (the row it was picked on).

        Asked once the browser's callback has returned: the name dialog
        is modal, and a modal ``exec()`` inside the callback would stall
        the browser until it closed.
        """
        from PySide6.QtCore import QTimer

        window = self.open_entity_editor(project_name, "multis", context)
        if window is not None:
            QTimer.singleShot(0, lambda: window.new_multi(context, group))

    def open_entity_editor_for(self, asset_id: str) -> None:
        """An asset, shot or Multi card's **Open in Entity Editor…**: its
        project's editor, on its tab and group, with it selected."""
        from . import containers
        container = containers.parse(asset_id)
        if isinstance(container, containers.GroupContainer):
            self.open_entity_editor(
                container.project_name, "multis", container.context,
                container.path.rsplit("/", 1)[-1],
            )
            return
        parts = self._resolver.split(asset_id)
        uri = self._resolver.uri_for(asset_id)
        if parts is None or uri is None:
            return
        project_name, group, name = parts
        kind = uri.segments[0] if uri.segments else None
        if kind not in ("assets", "shots"):
            return  # the Entity Editor has no tab for it (an edit)
        self.open_entity_editor(project_name, kind, group, name)

    def _frame_seed(self, props: dict, key: str, asset_id: str) -> int:
        """Seed a frame field from the entity's resolved view.

        :meth:`edit` decides what the artist changed by comparing the
        submitted value against this same resolved view, so whatever is
        seeded here comes back as a real pin the moment someone presses OK.
        A guessed seed would therefore write a frame range nobody chose onto
        the entity — and it would do it silently, because the guess and the
        artist's "I didn't touch that field" look identical downstream.
        A missing or non-integer frame value stops the dialog instead.
        """
        if key not in props:
            raise ConfigError(
                self._catalog_id,
                f"{asset_id}: nothing in the inheritance chain sets {key}, "
                f"so there is no frame range to edit",
            )
        value = props[key]
        # bool is a subclass of int; a checkbox value is not a frame number.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(
                self._catalog_id,
                f"{asset_id}: {key} is {value!r}, which is not a frame number",
            )
        return value

    def edit_fields(self, asset_id: str) -> list[CreationField]:
        """The Edit dialog of an asset (read-only) or shot (its frame range)."""
        parts = self._resolver.split(asset_id)
        if parts is None:
            return []
        project_name, second, third = parts
        uri = self._resolver.uri_for(asset_id)
        if uri is None:
            return []
        client = self._clients.get(project_name)
        try:
            props = client.config.get_properties(uri) or {}
        except Exception as exc:
            raise ConfigError(
                self._catalog_id,
                f"failed to read properties for {asset_id}: {exc}",
                cause=exc,
            ) from exc

        is_shot = str(uri).startswith("entity:/shots/")
        if is_shot:
            return [
                CreationField(
                    key="project", label="Project",
                    field_type="text", readonly=True,
                    initial=project_name, required=False,
                ),
                CreationField(
                    key="sequence", label="Sequence",
                    field_type="text", readonly=True,
                    initial=second, required=False,
                ),
                CreationField(
                    key="name", label="Name",
                    field_type="text", readonly=True,
                    initial=third, required=False,
                ),
                CreationField(
                    key="frame_start", label="Frame Start",
                    field_type="int",
                    initial=self._frame_seed(props, "frame_start", asset_id),
                ),
                CreationField(
                    key="frame_end", label="Frame End",
                    field_type="int",
                    initial=self._frame_seed(props, "frame_end", asset_id),
                ),
            ]
        return [
            CreationField(
                key="project", label="Project",
                field_type="text", readonly=True,
                initial=project_name, required=False,
            ),
            CreationField(
                key="category", label="Category",
                field_type="text", readonly=True,
                initial=second, required=False,
            ),
            CreationField(
                key="name", label="Name",
                field_type="text", readonly=True,
                initial=third, required=False,
            ),
        ]

    def edit(self, asset_id: str, fields: dict) -> bool:
        """Apply the Edit dialog: a shot's frame range, only what changed."""
        uri = self._resolver.uri_for(asset_id)
        if uri is None:
            return False
        parts = self._resolver.split(asset_id)
        if parts is None:
            return False
        project_name = parts[0]
        client = self._clients.get(project_name)
        # Writing an entity's own properties creates the entity when it is
        # missing, so an Edit reached with an id that names no real entity
        # would register one as a side effect (see EntityNotRegistered).
        # Refuse before anything is written; the host reports the raise.
        self._resolver.registered_uri_for(asset_id)
        # The dialog's int fields only hand back ints; anything else is a
        # bug and raises into the host's Edit boundary.
        submitted = {
            key: int(fields[key])
            for key in ("frame_start", "frame_end")
            if key in fields
        }
        if not submitted:
            return False
        # Shared timelines this edit makes overlap are warned about (#31).
        proj = self._registry.get(project_name)
        if proj is not None:
            self._activator.activate(proj)
        before = self._overlaps()
        try:
            # The dialog always resubmits every field it showed, and
            # :meth:`edit_fields` seeds those fields from the RESOLVED view — so a
            # field the artist never touched arrives holding an *inherited*
            # value. Since own properties are now stored verbatim, writing it
            # back would pin it: the entity would stop following its parent for
            # a value nobody chose, and a later re-time of the sequence would
            # silently skip this shot. Write only what actually changed.
            resolved = client.config.get_properties(uri) or {}
            writable = {
                key: value
                for key, value in submitted.items()
                if key not in resolved or resolved[key] != value
            }
            if not writable:
                return True
            # Merge into the entity's OWN overrides. Writing `writable` alone
            # replaced the node's whole property dict, so editing a shot's
            # frame range in the browser also wiped its name, scene ref,
            # channels and department assignment.
            own = client.config.get_own_properties(uri) or {}
            own.update(writable)
            client.config.set_own_properties(uri, own)
        except Exception as exc:
            raise ConfigError(
                self._catalog_id,
                f"failed to write properties for {asset_id}: {exc}",
                cause=exc,
            ) from exc
        self._invalidate_cache()
        self._warn_new_overlaps(before)
        return True

    def _overlaps(self) -> dict:
        """The active project's shared-timeline overlaps, for a before and
        after; {} when they cannot be read (the edit still goes ahead)."""
        from tumblepipe.config import multi_timeline
        try:
            return multi_timeline.overlapping_multis()
        except Exception:  # noqa: BLE001  a warning, never a blocker
            log.exception("Reading shared timelines")
            return {}

    def _warn_new_overlaps(self, before: dict, parent=None) -> list[str]:
        """Offer Repack for the Multis whose shots an edit made overlap;
        never repacks unasked. The Multis repacked."""
        from tumblepipe.config import multi_timeline
        found = multi_timeline.new_overlaps(before, self._overlaps())
        if not found:
            return []
        return self.offer_repack(parent, found, self.repack_timeline)

    @staticmethod
    def offer_repack(parent, found, repack) -> list[str]:
        """The warning (``timeline_repack.offer_repack``); tests swap it."""
        from .timeline_repack import offer_repack
        return offer_repack(parent, found, repack)

    def repack_timeline(self, group_uri) -> None:
        """Repack a Multi of the active project, and its open workfile."""
        from tumblepipe.config import multi_timeline
        multi_timeline.repack(group_uri)
        self._invalidate_cache()
        try:
            from tumblepipe.pipe.houdini import multi_timeline as session
        except ImportError:
            return
        session.apply_if_open(group_uri)

    def delete(self, asset_id: str) -> bool:
        """Delete the asset or shot, its folders archived with it."""
        proj = self._resolver.project_for(asset_id)
        if proj is None:
            return False
        self._activator.activate(proj)
        uri = self._resolver.registered_uri_for(asset_id)
        # The entity's folders go to _deleted/ with it: left in place, a new
        # entity of the same name would pick up all of the old work.
        from tumblepipe.pipe.entity_files import delete_entity
        try:
            delete_entity(uri)
        except Exception as exc:
            raise ConfigError(
                self._catalog_id, f"failed to delete {asset_id}: {exc}", cause=exc,
            ) from exc
        self._invalidate_cache()
        return True

    def bucket_child_count(
        self, project_name: str, kind: str, bucket_name: str,
    ) -> int:
        """Entities a category (``kind="category"``) or sequence
        (``"sequence"``) still holds — what the browser shows before it
        offers to delete the bucket. An unknown kind raises."""
        if kind == "category":
            return self._index.count(project_name, "assets", bucket_name)
        if kind == "sequence":
            return self._index.count(project_name, "shots", bucket_name)
        raise ValueError(f"unknown bucket kind: {kind!r}")

    def delete_bucket(
        self, project_name: str, kind: str, bucket_name: str,
    ) -> tuple[bool, str]:
        """Remove an empty category/sequence parent entity.

        ``kind`` is ``"category"`` or ``"sequence"``. Refuses if the
        bucket still has children (the UI surfaces the returned
        message). The bucket name comes from the row's label so it
        carries the original case — don't lowercase it again here.
        Returns ``(ok, message)``.
        """
        client = self._clients.get(project_name)
        if kind == "category":
            child_count = self.bucket_child_count(project_name, kind, bucket_name)
            uri = uris.entity_category(bucket_name)
            child_word = "asset" if child_count == 1 else "assets"
            label = "category"
        elif kind == "sequence":
            child_count = self.bucket_child_count(project_name, kind, bucket_name)
            uri = uris.entity_sequence(bucket_name)
            child_word = "shot" if child_count == 1 else "shots"
            label = "sequence"
        else:
            return False, f"unknown bucket kind: {kind!r}"
        if child_count > 0:
            return (
                False,
                f"{label.capitalize()} '{bucket_name}' still contains "
                f"{child_count} {child_word}. Remove them first.",
            )
        if client.config.get_properties(uri) is None:
            # Bucket was inferred from URIs that have all been removed
            # already — nothing left to delete on disk, but tell the
            # caller to refresh.
            self._index.invalidate()
            return True, f"{label.capitalize()} '{bucket_name}' was already empty."
        try:
            client.config.remove_entity(uri)
        except Exception as exc:
            return False, f"failed to remove {label} '{bucket_name}': {exc}"
        if kind == "category":
            self._index.invalidate(shots=False)
        else:
            self._index.invalidate(assets=False)
        return True, f"Deleted {label} '{bucket_name}'."

    def description_path(self, asset_id: str) -> Path | None:
        """Return the path to ``description.txt`` for an asset/shot."""
        entity_dir = self._resolver.entity_dir(asset_id)
        return None if entity_dir is None else entity_dir / "description.txt"

    def read_description(self, asset_id: str) -> str:
        """The description sidecar's text; ``""`` when none was written."""
        path = self.description_path(asset_id)
        if path is None or not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    def write_description(self, asset_id: str, text: str) -> None:
        """Write the description sidecar for an asset/shot.

        Raises :class:`ConfigError` if the asset id is unresolvable, or
        :class:`OSError` if the write fails.
        """
        path = self.description_path(asset_id)
        if path is None:
            raise ConfigError(
                self._catalog_id,
                f"cannot resolve description path for {asset_id}",
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.strip() + "\n", encoding="utf-8")

    def edit_description(self, asset_id: str) -> None:
        """Open a multiline text dialog to edit the description sidecar."""
        from PySide6.QtWidgets import QInputDialog
        import hou
        current = self.read_description(asset_id)
        parent = hou.qt.mainWindow()
        text, ok = QInputDialog.getMultiLineText(
            parent,
            "Edit Description",
            f"Description for {asset_id}:",
            current,
        )
        if not ok:
            return
        self.write_description(asset_id, text)
        hou.ui.setStatusMessage(
            f"Description updated for {asset_id}",
            severity=hou.severityType.Message,
        )
        # Drop discovery caches so the next browse sees the edit,
        # and poke the asset browser detail panel to re-fetch.
        self._index.invalidate()
        self._refresh.detail()

    def duplicate_asset(self, asset_id: str) -> None:
        """Make a new asset from this one's latest workfiles.

        Creates the entity (with the source's own properties) here, then
        copies each department's latest workfile to it in a background
        hython (``pipe.houdini.duplicate_workfiles``), so the open scene is
        never touched. Nothing is published.
        """
        import copy

        import hou
        from PySide6.QtWidgets import QInputDialog

        from tumblepipe.pipe import duplicate
        from tumblepipe.pipe.entity_files import has_entity_files
        from tumblepipe.pipe.houdini import duplicate_workfiles

        from .houdini import session_nc_type

        proj = self._resolver.project_for(asset_id)
        if proj is None:
            return
        self._activator.activate(proj)
        source_uri = self._resolver.registered_uri_for(asset_id)
        if source_uri is None:
            return
        category, source_name = source_uri.segments[1], source_uri.segments[-1]
        # The suggestion follows the name rule even when the source, named
        # before it, does not (old_tree -> oldtreeCopy).
        suggested = "".join(c for c in source_name if c.isascii() and c.isalnum())
        new_name, ok = QInputDialog.getText(
            hou.qt.mainWindow(), "Duplicate asset",
            f"Name for the copy of {source_name}:", text=f"{suggested}Copy",
        )
        new_name = new_name.strip() if ok else ""
        if not new_name:
            return
        problem = shot_batch.name_problem("asset", new_name)
        if problem:
            hou.ui.displayMessage(f"{problem}.")
            return
        client = self._clients.get(proj.name)
        target_uri = uris.entity_asset(category, new_name)
        if client.config.get_properties(target_uri) is not None:
            hou.ui.displayMessage(f"Asset '{new_name}' already exists in '{category}'.")
            return
        if has_entity_files(target_uri):
            hou.ui.displayMessage(
                f"Files for an asset named '{new_name}' are still on disk in "
                f"'{category}'. Pick another name, or move them out of the way.",
            )
            return

        plan = duplicate.plan_duplicate(
            source_uri, self._dept_scope.pool("assets"),
        )
        properties = copy.deepcopy(client.config.get_own_properties(source_uri) or {})
        properties["name"] = new_name
        client.config.add_entity(target_uri, properties)
        self._index.invalidate(shots=False)
        new_id = uris.asset_id_for(proj.name, target_uri)
        if not plan:
            hou.ui.setStatusMessage(
                f"Created {new_name}; {source_name} has no workfiles to copy.",
            )
            self._refresh.card(new_id)
            return

        action = f"Duplicating {source_name} to {new_name}"
        hou.ui.setStatusMessage(
            f"{action}: copying {len(plan)} workfile(s) in the background…",
        )

        def _finished(run: duplicate_workfiles.DuplicateRun) -> None:
            self._invalidate_cache()
            self._refresh.card(new_id)
            if run.ok:
                hou.ui.setStatusMessage(
                    f"Duplicated {source_name} to {new_name} "
                    f"({len(run.results)} workfile(s)); publish it from its own "
                    "workfiles.",
                )
                return
            detail = "\n".join(
                f"{r['department']}: {r['error']}" for r in run.failed
            ) or f"hython exited with {run.returncode}"
            hou.ui.displayMessage(
                f"{action} did not finish:\n\n{detail}\n\nLog: {run.log_path}",
                severity=hou.severityType.Warning,
            )

        nc_type = session_nc_type()
        run_in_background(
            action,
            lambda: duplicate_workfiles.run_duplicate(
                source_uri, target_uri, plan, nc_type,
            ),
            _finished,
        )

    def generate_master_scene(self, asset_id: str) -> None:
        """Build the entity's master scene (``pipe.houdini.master_scene``):
        every department's latest workfile merged into one hip, once the
        artist confirms — it replaces the open scene."""
        import hou
        from tumblepipe.pipe.houdini import master_scene

        proj = self._resolver.project_for(asset_id)
        if proj is None:
            return
        self._activator.activate(proj)
        entity_uri = self._resolver.registered_uri_for(asset_id)
        if entity_uri is None:
            return
        name = entity_uri.segments[-1]
        workfiles = master_scene.department_workfiles(entity_uri)
        if not workfiles:
            hou.ui.displayMessage(
                f"{name} has no department workfiles to merge.",
                severity=hou.severityType.Warning,
            )
            return
        master_hip = master_scene.master_scene_path(entity_uri)
        choice = hou.ui.displayMessage(
            f"Generate master scene for {name}?\n\n"
            f"Merging {len(workfiles)} departments into:\n"
            f"{master_hip}\n\n"
            f"This will open a new scene.",
            buttons=("Generate", "Cancel"),
            title="Generate Master Scene",
        )
        if choice != 0:
            return
        failed = master_scene.build_master_scene(master_hip, workfiles, name)
        if failed:
            # The master then ships without them, and "saved" alone would
            # read as if everything landed.
            hou.ui.displayMessage(
                f"Master scene saved: {master_hip}\n\nBut these departments "
                f"could not be merged and are missing from it: "
                f"{', '.join(failed)}. See the log for why.",
                severity=hou.severityType.Warning,
            )
        else:
            hou.ui.setStatusMessage(
                f"Master scene saved: {master_hip}",
                severity=hou.severityType.Message,
            )

    def play_shots(self, asset_ids: list[str]) -> None:
        """Open the shots back to back in the editor, as an untitled edit
        (``pipe.review_playlist.play_shots``), in their own project."""
        from tumblepipe.pipe import review_playlist

        projects = [self._resolver.project_for(aid) for aid in asset_ids]
        if any(p is None for p in projects):
            raise ValueError("A shot to play belongs to no registered project")
        if len({p.name for p in projects}) != 1:
            raise ValueError("The shots to play are of more than one project")

        def _work() -> None:
            # It blocks: the project's Client builds.
            uris = [self._resolver.uri_for_ready(aid) for aid in asset_ids]
            malformed = [aid for aid, uri in zip(asset_ids, uris) if uri is None]
            if malformed:
                raise ValueError(f"Not a shot: {', '.join(malformed)}")
            self._activator.activate(projects[0])
            review_playlist.play_shots(uris)

        run_in_background("Play", _work, lambda _: None)

    def editor_applied(self) -> None:
        """The Entity Editor wrote the database: drop what the browser
        cached and show it again. The detail panels too: Apply changes
        what they print (channels, frame range, Root), and they kept the
        old text until something else refreshed them (#40)."""
        # The Multi membership cache too: Apply can change who is in one.
        self._invalidate_cache()
        self._index.invalidate()
        self._refresh.sidebar()
        self._refresh.grid()
        self._refresh.detail()

    def play_shot_uris(self, project_name: str, uris: list) -> None:
        """Open the shots *uris* of *project_name* back to back in the
        editor, in the order given (a shots Multi's Play, #26)."""
        from tumblepipe.pipe import review_playlist

        proj = self._registry.get(project_name)
        if proj is None:
            raise ValueError(f"{project_name} is not a registered project")
        uris = list(uris)
        if not uris:
            raise ValueError("No shots to play")

        def _work() -> None:
            self._clients.get(project_name)  # it blocks: the Client builds
            self._activator.activate(proj)
            review_playlist.play_shots(uris)

        run_in_background("Play", _work, lambda _: None)

    def play_sequence(self, project_name: str, sequence: str) -> None:
        """Open every shot of *sequence* in the editor, in name order (the
        sequence row's Play Sequence, #26). A sequence with no shot says so."""
        from tumblepipe.pipe import review_playlist

        proj = self._registry.get(project_name)
        if proj is None:
            raise ValueError(f"{project_name} is not a registered project")

        def _work() -> int:
            client = self._clients.get(project_name)  # it blocks
            shots = sequence_shots(
                client.config.list_entity_uris(None, closure=True), sequence,
            )
            if shots:
                self._activator.activate(proj)
                review_playlist.play_shots(shots)
            return len(shots)

        def _done(count) -> None:
            if not count:
                import hou
                hou.ui.setStatusMessage(
                    f"Sequence {sequence} has no shots to play",
                    severity=hou.severityType.Warning,
                )

        run_in_background("Play Sequence", _work, _done)

    # ── Edits (see edits.py) ─────────────────────────────

    def _edit_project(self, asset_id: str):
        """The edit an edit card's id names, and its registered project."""
        ref = edits.parse_id(asset_id)
        if ref is None:
            raise ValueError(f"Not an edit: {asset_id}")
        proj = self._registry.get(ref.project)
        if proj is None:
            raise ValueError(f"Edit {ref.name} belongs to no registered project")
        return ref, proj

    def open_edit(self, asset_id: str, *, published: bool = False) -> None:
        """Open the edit in the editor (``pipe.review_playlist.open_edit``),
        in its own project: its newest working version (one never saved
        opens empty, ready for its first Save), or with ``published`` its
        latest published version, read-only."""
        from tumblepipe.pipe import edits as pipe_edits
        from tumblepipe.pipe import review_playlist

        ref, proj = self._edit_project(asset_id)

        def _work() -> None:
            # It blocks: the project's Client builds, and the published
            # versions are listed on the file server.
            version = None
            if published:
                versions = pipe_edits.published_versions(
                    ref.name, self._clients.get(ref.project),
                )
                if not versions:
                    raise ValueError(f"Edit {ref.name} has no published version")
                version = versions[-1]
            self._activator.activate(proj)
            review_playlist.open_edit(ref.uri, version)

        run_in_background(
            "Play Published" if published else "Open in Editor",
            _work, lambda _: None,
        )

    def render_edit(self, asset_id: str) -> None:
        """Open Farm Submit on its Edits tab with this edit's Render Edit
        ticked (#28), in the edit's own project. The row offers the
        edit's latest published version first."""
        from tumblepipe.farm.submit import grid as farm_grid

        ref, proj = self._edit_project(asset_id)
        self._activator.activate(proj)
        open_submit_jobs_dialog([ref.uri], [ref.name], farm_grid.EDITS)

    def set_master_edit(self, asset_id: str, master: bool) -> None:
        """Make the edit the project's master edit (``master``), or leave
        the project without one; the cards follow."""
        from tumblepipe.pipe import edits as pipe_edits

        ref, _proj = self._edit_project(asset_id)

        def _work() -> None:
            pipe_edits.set_master_edit(
                ref.name if master else None, self._clients.get(ref.project),
            )

        def _done(_result) -> None:
            self._invalidate_cache()
            self._refresh.grid()
            self._refresh.detail()

        run_in_background(
            "Set as Master Edit" if master else "Clear Master Edit",
            _work, _done,
        )

    def show_edit_on_timeline(self, asset_id: str) -> None:
        """Show the edit on Houdini's timeline (#61,
        ``pipe.houdini.edit_timeline``): the playbar spans it and each clip
        gets a bookmark. Its newest document is read off the main thread,
        in its own project; the playbar is set on the main thread."""
        from tumblepipe.pipe import edit_timeline

        ref, _proj = self._edit_project(asset_id)

        def _work():
            return edit_timeline.read_edit(ref.name, self._clients.get(ref.project))

        def _done(edit) -> None:
            import hou
            from tumblepipe.pipe.houdini import edit_timeline as session
            summary = session.show_document(edit)
            hou.ui.setStatusMessage(
                summary.split("\n", 1)[0], severity=hou.severityType.Message,
            )
            if "\n" in summary:
                tell(summary)

        run_in_background("Show on Houdini Timeline", _work, _done)

    def _edits_changed(self) -> None:
        """An edit was made, renamed or deleted: the Edits section (its
        count, whether it is there at all), the grids and the detail
        panels follow."""
        self._invalidate_cache()
        self._refresh.sidebar()
        self._refresh.grid()
        self._refresh.detail()

    def _create_edit(self, proj_name: str, client, fields) -> str | None:
        """New Edit...: the edit, with nothing saved yet (the editor opens it
        empty, for its first Save); its card's id, or None when refused."""
        from tumblepipe.pipe import edits as pipe_edits

        name = str(fields.get("name") or "").strip()
        problem = pipe_edits.name_problem(name, pipe_edits.list_edits(client))
        if problem:
            tell(problem)
            return None
        pipe_edits.create_edit(name, client)
        # The browser re-populates the sidebar and re-queries after a
        # create; the edit scan must not answer from before it.
        self._invalidate_cache()
        return edits.EditRef(proj_name, name).asset_id

    def rename_edit(self, asset_id: str) -> None:
        """Rename Edit...: ask for the new name and rename it
        (``pipe.edits.rename_edit``), the master edit staying master. A
        refusal (the name rule, a name taken, an edit with files on disk)
        says why."""
        from tumblepipe.pipe import edits as pipe_edits

        ref, _proj = self._edit_project(asset_id)
        new = ask_text("Rename Edit", f"New name for the edit {ref.name}:", ref.name)
        new = (new or "").strip()
        if not new or new == ref.name:
            return
        try:
            pipe_edits.rename_edit(ref.name, new, self._clients.get(ref.project))
        except ValueError as exc:
            tell(str(exc))
            return
        self._edits_changed()

    def delete_edit(self, asset_id: str) -> None:
        """Delete Edit...: refused for the master edit; otherwise, once
        confirmed, the edit goes and its folders move to ``_deleted/``
        (``pipe.edits.delete_edit``), as an asset's or shot's do."""
        from tumblepipe.pipe import edits as pipe_edits
        from tumblepipe.pipe.entity_files import ARCHIVE_DIR, ArchiveError

        ref, _proj = self._edit_project(asset_id)
        client = self._clients.get(ref.project)
        problem = pipe_edits.delete_problem(ref.name, pipe_edits.master_edit(client))
        if problem:
            tell(problem)
            return
        if not confirm(
            "Delete Edit",
            f"Delete the edit '{ref.name}'?\n\n"
            "Its saved and published versions and rendered movies are not "
            f"erased: they move to the {ARCHIVE_DIR} folder of their storage "
            "roots, where they can be recovered.",
        ):
            return
        try:
            pipe_edits.delete_edit(ref.name, client)
        except (ValueError, ArchiveError) as exc:
            tell(str(exc))
            return
        self._edits_changed()

    def edit_departments(self, asset_id: str) -> None:
        """Choose which departments this shot or asset uses."""
        from .departments import EntityDepartmentsDialog
        EntityDepartmentsDialog(
            asset_id,
            resolver=self._resolver, activator=self._activator,
            index=self._index, refresh=self._refresh,
            invalidate_cache=self._invalidate_cache,
        ).exec()

    def department_turned_off(self, asset_id: str, dept: str) -> bool | None:
        """Whether ``dept`` is turned off on the shot ``asset_id`` (#64);
        None when the switch does not apply (not a shot of a project)."""
        proj = self._resolver.project_for(asset_id)
        entity_uri = self._resolver.uri_for(asset_id)
        if proj is None or entity_uri is None:
            return None
        if entity_uri.purpose != "entity" or entity_uri.segments[:1] != ["shots"]:
            return None
        self._activator.activate(proj)
        from tumblepipe.config.department import is_department_disabled
        return is_department_disabled(entity_uri, dept)

    def set_department_off(self, asset_id: str, dept: str, off: bool) -> None:
        """Turn ``dept`` off on the shot ``asset_id`` (``off``), or back on.

        Reversible and file-safe: the department's workfiles and exports
        stay where they are. Its layers leave (or rejoin) the shot's staged
        build at the next build; import_shot in an open scene follows on
        its next cook.
        """
        proj = self._resolver.project_for(asset_id)
        # A write path: never write onto an entity the config does not hold.
        entity_uri = self._resolver.registered_uri_for(asset_id)
        if proj is None or entity_uri is None:
            return
        self._activator.activate(proj)
        from tumblepipe.config.department import set_department_disabled
        if not set_department_disabled(entity_uri, dept, off):
            return
        self._invalidate_cache()
        self._refresh.card(asset_id)
        self._refresh.detail()

    def open_database_editor(self, asset_id: str) -> None:
        """Open the pipeline DatabaseWindow with the entity pre-selected.

        Reached from two threads, which is why the guard is here rather than
        at one call site: the card menu (GUI thread) and ``execute_action``,
        which the catalog contract runs on a worker. Everything below is GUI
        work — a modal, then a QMainWindow via the launcher — and Qt objects
        are thread-affine. Constructing them off the GUI thread corrupts
        memory rather than raising, so it surfaces as Houdini vanishing with
        no crash log. See *Qt and threads* in docs/development.md.
        """
        from tumbletrove.common.gui import is_main_thread
        if not is_main_thread():
            run_on_main_thread(
                reporting(
                    f"Opening {asset_id} in the Database Editor",
                    lambda: self.open_database_editor(asset_id),
                ),
            )
            return

        proj = self._resolver.project_for(asset_id)
        if proj is not None:
            self._activator.activate(proj)
        uri = self._resolver.uri_for(asset_id)
        if uri is None:
            import hou
            hou.ui.displayMessage(
                f"Could not resolve a database entity for '{asset_id}'.",
                severity=hou.severityType.Warning,
            )
            return
        # Delegate to the shared singleton launcher so the shelf tool and
        # this action open the same window. The launcher surfaces any
        # error to the user instead of failing silently.
        from tumblepipe.config_editor import open_database_editor
        open_database_editor(uri)

    def shot_has_direct_scene_ref(self, asset_id: str) -> bool:
        from tumblepipe.config import scene as scene_mod
        uri = self._resolver.uri_for(asset_id)
        if uri is None:
            return False
        return scene_mod.get_scene_ref(uri) is not None

    def clear_shot_scene_ref(self, asset_id: str) -> None:
        from tumblepipe.config import scene as scene_mod
        uri = self._resolver.uri_for(asset_id)
        if uri is None:
            return
        scene_mod.set_scene_ref(uri, None)
        # Its root layer still names the Root until it is regenerated.
        from tumblepipe.pipe.root_staging import sync_roots
        summary = sync_roots([uri]).summary()
        if summary:
            import hou
            hou.ui.setStatusMessage(
                f"Cleared Root; {summary}", severity=hou.severityType.Message
            )
        self._invalidate_cache()
        self._refresh.detail()

    def submit_jobs(self, assets: list[Asset]) -> None:
        """Open the Farm Submit grid for ``assets`` with their Render ticked.

        All assets must share a project and a context. Activates the
        project before opening the dialog so ``tumblepipe.api.default_client``
        and the department lookups resolve against the right install.
        """
        if not assets:
            return
        # All from the same project — enforced by the menu's selection
        # filter, but double-check defensively.
        proj = self._resolver.project_for(assets[0].id)
        if proj is None:
            raise ValueError(f"no registered project owns {assets[0].id}")
        self._activator.activate(proj)

        # The menu only offers cards of the right-clicked card's context,
        # so mixed contexts here are a bug.
        contexts = {asset_context(a) for a in assets}
        if len(contexts) != 1 or None in contexts:
            raise ValueError(f"Submit Jobs needs one context, got {contexts!r}")
        context = next(iter(contexts))

        # Entity URIs, parallel to the display names.
        entity_uris: list = []
        names: list[str] = []
        for a in assets:
            uri = self._resolver.uri_for(a.id)
            if uri is None:
                raise ValueError(f"{a.id} is not a PROJECT/BUCKET/NAME id")
            entity_uris.append(uri)
            names.append(a.name or a.id.split("/")[-1])
        # The browser path has no "opened from" entity, but the loaded
        # workfile's department is still the one the artist is working in,
        # so it seeds the render department the same way the scene quick
        # action does. None when no pipeline scene is open.
        scene_ctx = self._scene.get_loaded_scene_context()
        department = (
            scene_ctx.department_name if scene_ctx is not None else None
        )
        open_submit_jobs_dialog(
            entity_uris, names, context, department=department,
        )

    def import_asset_to_scene(self, detail: AssetDetail) -> None:
        """Create a th::import_asset::1.0 LOP in /stage for the asset.

        ``execute_action`` is invoked on a worker thread, but ``hou.node``
        / ``createNode`` are not thread-safe — dispatch to the GUI loop.
        """

        from .drops import entity_uri_for
        name = detail.name.replace(" ", "_")
        asset_id = detail.id
        entity_uri = entity_uri_for(detail)

        def _do_import():
            try:
                import hou
                stage = hou.node("/stage")
                if stage is None or entity_uri is None:
                    # Both are silent no-ops that look exactly like a dead
                    # menu entry, so say which one it was.
                    hou.ui.displayMessage(
                        f"Cannot import {asset_id}: "
                        + ("this scene has no /stage network."
                           if stage is None else
                           "it has no entity URI to import."),
                        severity=hou.severityType.Warning,
                    )
                    return
                from tumblepipe.pipe.houdini.lops import import_asset
                node = import_asset.create(stage, name)
                node.set_entity_uri(uris.parse(entity_uri))
                node.execute()
                raw = node.native()
                raw.setDisplayFlag(True)
                raw.setSelected(True, clear_all_selected=True)
            except Exception as exc:
                report_failure(f"Importing {asset_id}", exc)

        run_on_main_thread(_do_import)

    def manage_todos(self, asset) -> None:
        """Open the per-asset todos dialog."""
        from tumbletrove import asset_browser
        import hou
        from tumbletrove.asset_browser.ui.todos_dialog import TodosDialog
        mgr = asset_browser.get_todos()
        if mgr is None:
            return
        self._refresh.follow_todos(mgr)
        dlg = TodosDialog(
            mgr, self._catalog_id, asset.id, asset.name, parent=hou.qt.mainWindow(),
        )
        dlg.show()
        dlg.raise_()
