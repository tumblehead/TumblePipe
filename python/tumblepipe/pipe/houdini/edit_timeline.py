"""An edit on the session's timeline (#61): the playbar and its bookmarks.

``pipe.edit_timeline`` says what to show (the edit's span, a bookmark per
clip); here it is applied to the session. Read-only for the project: only
the playbar and the session's bookmarks change.

The bookmarks are tagged with ``EDIT_KEY`` naming the edit, so a re-run
(of any edit) replaces the ones an import made and leaves the artist's own
and a Multi's (``multi_timeline.MEMBER_KEY``) alone. Like a Multi's, they
are *temporary* (Houdini does not save them in the hip): the edit owns the
cut, and a bookmark dragged on the playbar must not read as a clip that
moved. Re-run the import to see them again after reopening the scene.
"""

from __future__ import annotations

import hou

from tumblepipe.pipe import edit_timeline as logic
from tumblepipe.pipe.edit_timeline import EDIT_KEY, EditDocument, TimelinePlan

# Alternating, so neighbouring clips read apart on the playbar.
_COLORS = (hou.Color((0.35, 0.75, 0.45)), hou.Color((0.75, 0.45, 0.8)))


def edit_bookmarks() -> list:
    """The session's bookmarks an edit import made."""
    return [
        bookmark for bookmark in hou.anim.bookmarks()
        if logic.is_ours(bookmark.metadata(EDIT_KEY, None))
    ]


def apply_plan(plan: TimelinePlan, edit_name: str) -> int:
    """Set the playbar to ``plan`` and replace the edit bookmarks with its
    own; the number made. The playhead moves to the edit's first frame
    unless it is already inside it."""
    stale = edit_bookmarks()
    if stale:
        hou.anim.removeBookmarks(tuple(stale))
    hou.playbar.setFrameRange(plan.first, plan.last)
    hou.playbar.setPlaybackRange(plan.first, plan.last)
    if not plan.first <= hou.frame() <= plan.last:
        hou.setFrame(plan.first)
    for index, spec in enumerate(plan.bookmarks):
        bookmark = hou.anim.newBookmark(spec.name, spec.start, spec.end)
        bookmark.setColor(_COLORS[index % len(_COLORS)])
        bookmark.setComment(spec.comment)
        bookmark.setMetadata(EDIT_KEY, edit_name)
        bookmark.setTemporary(True)
    return len(plan.bookmarks)


def scene_fps() -> int:
    return int(round(hou.fps()))


def show_document(edit: EditDocument) -> str:
    """Show the read edit ``edit`` on the timeline; a one-line summary (the
    fps warning appended when its rate was converted)."""
    plan = logic.plan(edit.document, edit.start_frame, scene_fps(), edit.name)
    made = apply_plan(plan, edit.name)
    kind = 'published' if edit.published else 'working'
    summary = (
        f'Edit {edit.name} ({kind} {edit.version}) on the timeline: '
        f'frames {plan.first}-{plan.last}, {made} clip bookmark'
        f'{"" if made == 1 else "s"}.'
    )
    return f'{summary}\n\n{plan.warning}' if plan.warning else summary


def show_edit(name: str, client=None) -> str:
    """Read edit ``name`` (its newest document) and show it on the timeline;
    the summary. ValueError when it has nothing to show."""
    return show_document(logic.read_edit(name, client))


def _tell(summary: str) -> None:
    warned = '\n\n' in summary
    if hou.isUIAvailable():
        hou.ui.displayMessage(
            summary, title='Import Edit to Timeline',
            severity=hou.severityType.Warning if warned else hou.severityType.Message,
        )
    else:
        print(summary)


def import_edit_dialog() -> None:
    """The shelf tool: pick one of the project's edits (the master edit
    first and preselected) and show it on the timeline."""
    from tumblepipe.pipe import edits
    from tumblepipe.pipe.houdini.ui.report import reporting

    def _run() -> None:
        names = edits.list_edits()
        if not names:
            hou.ui.displayMessage(
                'This project has no edits yet. Make one in the asset '
                "browser's Edits section and save it in the editor.",
                title='Import Edit to Timeline',
            )
            return
        try:
            master = edits.master_edit()
        except ValueError:
            master = None
        if master in names:
            names = [master] + [name for name in names if name != master]
        labels = [f'{name}  (master)' if name == master else name for name in names]
        picked = hou.ui.selectFromList(
            labels, default_choices=(0,), exclusive=True,
            title='Import Edit to Timeline',
            message='Show which edit on the timeline?',
            column_header='Edit', clear_on_cancel=True,
        )
        if not picked:
            return
        _tell(show_edit(names[picked[0]]))

    reporting('Import Edit to Timeline', _run)()
