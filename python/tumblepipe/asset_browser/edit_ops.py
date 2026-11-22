"""An edit card's actions (see edits.py): open it in the editor, render
it, make it the master edit, show it on Houdini's timeline, and create,
rename or delete it, each in the edit's own project.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from tumblepipe.pipe.houdini.ui.report import run_in_background

from . import edits as edits
from .entity_ops import ask_text, confirm, tell
from .scene import open_submit_jobs_dialog

if TYPE_CHECKING:
    from .clients import ClientPool
    from .houdini import ProjectActivator
    from .projects import PipelineProjectRegistry
    from .refresh import BrowserRefresh


class EditOps:
    """The writes and actions behind an edit card's menu and New Edit..."""

    def __init__(
        self,
        *,
        registry: "PipelineProjectRegistry",
        clients: "ClientPool",
        activator: "ProjectActivator",
        refresh: "BrowserRefresh",
        invalidate_cache: Callable[[], None],
    ) -> None:
        self._registry = registry
        self._clients = clients
        self._activator = activator
        self._refresh = refresh
        self._invalidate_cache = invalidate_cache

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

    def create_edit(self, proj_name: str, client, fields) -> str | None:
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
