"""Playing shots in Cinedex: a selection of shot cards, a Multi's shots,
a sequence, and the toolbar's Open Timeline (a sequence, else the
project's master edit, else every shot), each in its own project.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from tumblepipe.pipe.houdini.ui.report import run_in_background, run_on_main_thread

if TYPE_CHECKING:
    from .clients import ClientPool
    from .houdini import ProjectActivator
    from .projects import PipelineProjectRegistry
    from .resolver import AssetResolver

log = logging.getLogger(__name__)


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


def project_shots(entity_uris) -> list:
    """Every shot among *entity_uris*, in name order (then sequence, for
    two of one name): what Play in Cinedex on a flat shot list's Shots
    section plays (``config.flat_entities``), the order that list shows."""
    return sorted(
        (
            uri for uri in entity_uris
            if len(uri.segments) == 3 and uri.segments[0] == "shots"
        ),
        key=lambda uri: (uri.segments[2], uri.segments[1]),
    )


def timeline_sequence(selected_uri, view_tags) -> str | None:
    """The sequence Open Timeline plays: the selected card's when it is a
    shot (``entity:/shots/<sequence>/<shot>``), else the sequence the view
    is narrowed to (its ``sequence:<name>`` tag, a sequence's sidebar row),
    else ``None`` (the master edit, else every shot)."""
    if (
        selected_uri is not None and len(selected_uri.segments) == 3
        and selected_uri.segments[0] == "shots"
    ):
        return selected_uri.segments[1]
    return next(
        (t.partition(":")[2] for t in view_tags or ()
         if t.startswith("sequence:") and t.partition(":")[2]),
        None,
    )


class Playback:
    """Opening shots in the editor, from the cards, sequences and toolbar."""

    def __init__(
        self,
        *,
        registry: "PipelineProjectRegistry",
        clients: "ClientPool",
        resolver: "AssetResolver",
        activator: "ProjectActivator",
    ) -> None:
        self._registry = registry
        self._clients = clients
        self._resolver = resolver
        self._activator = activator

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
        sequence row's Play in Cinedex, #26). A sequence with no shot says so."""
        from tumblepipe.pipe import review_playlist

        proj = self._registry.get(project_name)
        if proj is None:
            raise ValueError(f"{project_name} is not a registered project")

        def _work() -> int:
            shots = self._sequence_shots(project_name, sequence)
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

    def play_all_shots(self, project_name: str) -> None:
        """Open every shot of *project_name* in the editor, in name order
        (``project_shots``): Play in Cinedex on a flat shot list's Shots
        section, which has no sequence rows. A project with no shot says so."""
        from tumblepipe.pipe import review_playlist

        proj = self._registry.get(project_name)
        if proj is None:
            raise ValueError(f"{project_name} is not a registered project")

        def _work() -> int:
            client = self._clients.get(project_name)  # it blocks: the Client builds
            shots = project_shots(client.config.list_entity_uris(None, closure=True))
            if shots:
                self._activator.activate(proj)
                review_playlist.play_shots(shots)
            return len(shots)

        def _done(count) -> None:
            if not count:
                import hou
                hou.ui.setStatusMessage(
                    f"{project_name} has no shots to play",
                    severity=hou.severityType.Warning,
                )

        run_in_background("Play Shots", _work, _done)

    def _sequence_shots(self, project_name: str, sequence: str) -> list:
        """The shots of *sequence* in *project_name*, in name order: what
        Play in Cinedex on a sequence and Open Timeline play. Blocks while
        the project's Client builds."""
        client = self._clients.get(project_name)
        return sequence_shots(
            client.config.list_entity_uris(None, closure=True), sequence,
        )

    def open_timeline(
        self, selected_id: str | None, session_project: str | None,
        done_cb: Callable[[], None] | None = None,
        view_tags=frozenset(),
    ) -> None:
        """The toolbar's Open Timeline: play a whole sequence in the editor,
        the sequence of the shot the browser has selected (its pane's card),
        else the sequence the view is narrowed to (*view_tags*, a sequence's
        sidebar row), with exactly the shots the sequence's Play in Cinedex
        plays (``timeline_sequence``, ``_sequence_shots``); with neither,
        the project's master edit, else every shot of the project
        (``pipe.review_playlist.timeline_arguments``). The project is the
        selected card's, else the view's, else the open scene's, else the
        only registered one. The outcome goes to Houdini's status line, a
        failure too (no message box, the button has no other place to say
        it)."""
        from tumblepipe.pipe import review_playlist

        def _say(text: str, error: bool = False, warning: bool = False) -> None:
            def _show() -> None:
                import hou
                hou.ui.setStatusMessage(
                    text,
                    severity=hou.severityType.Error if error
                    else hou.severityType.Warning if warning
                    else hou.severityType.Message,
                )
            run_on_main_thread(_show)

        def _work() -> None:
            try:
                proj = None if selected_id is None else self._resolver.project_for(selected_id)
                uri = None if proj is None else self._resolver.uri_for_ready(selected_id)
                if proj is None:
                    view_project = next(
                        (t.partition(":")[2] for t in view_tags or ()
                         if t.startswith("project:")), "",
                    )
                    proj = self._registry.get(view_project) if view_project else None
                if proj is None and session_project:
                    proj = self._registry.get(session_project)
                if proj is None:
                    every = list(self._registry.all())
                    if len(every) != 1:
                        raise ValueError(
                            "select a shot or open a scene to say which project"
                        )
                    proj = every[0]
                self._clients.get(proj.name)  # it blocks: the Client builds
                sequence = timeline_sequence(uri, view_tags)
                shots: list = []
                if sequence is not None:
                    shots = self._sequence_shots(proj.name, sequence)
                    if not shots:
                        # As the sequence's own Play in Cinedex says it.
                        _say(f"Sequence {sequence} has no shots to play", warning=True)
                        return
                self._activator.activate(proj)
                review_playlist.open_timeline(shots)
                _say(
                    f"Opened Cinedex on sequence {sequence}" if shots
                    else "Opened Cinedex on the project's timeline"
                )
            except Exception as exc:  # noqa: BLE001 - reported on the status line
                log.exception("Open Timeline failed")
                _say(f"Open Timeline failed: {exc}", error=True)
            finally:
                if callable(done_cb):
                    run_on_main_thread(done_cb)

        import threading
        threading.Thread(target=_work, name="th-bg: Open Timeline", daemon=True).start()
