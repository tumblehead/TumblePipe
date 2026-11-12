"""Live view: show the shot an animator renders live in the editor, on the
master edit, looping the shots around it (designs/shot-dashboard-live-
playblast.md §5.3). Without Houdini, so it can be tested on its own.

Each running editor (``cinedex``) of a project says how to reach it in
``players/<project key>/<pid>.json`` under the live root
(``pipe.live.players_dir``): a port on 127.0.0.1 and a token. A call is a
line of JSON-RPC 2.0 (the token in its params), the answer a line back.
:func:`show` asks each player in turn to ``view_shot``; one on the master
edit shows it (and comes to the front without taking the keyboard, on
Windows, macOS and X11; a minimised one on X11 does take it; on Wayland
it asks the desktop for attention), or says
why it cannot (the edit has no clip of the shot: then no other player
could either); one on another edit says it is not the one to ask
(``NOT_HERE``) and the next is asked. One that does not answer is gone
when its process is (its file is cleared), else busy: its file stays, the
rest are asked, and when none of them decides no player is started (the
busy one may be on the master edit; a second would be one too many). When
none is on the master edit, a new player is started on it with the view
(``spawn``).
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

from tumblepipe.pipe import live

#: How long a player has to answer: a local call, and the editor answers
#: from its UI thread.
TIMEOUT = 6.0
#: How long connecting may take before the player is taken for gone.
CONNECT_TIMEOUT = 1.0
#: The error a player answers when it is not the one to ask (it is not on
#: the master edit); any other error is a refusal (th_pipe::player_rpc), but
#: for a wrong token: a file out of date with its player, passed over too.
NOT_HERE = -32002
BAD_TOKEN = -32001
#: The programs a running player is (``live._process_program``'s name):
#: the editor, and its name before it was Cinedex (TumblePipe 1.103 and
#: older), which an editor started from an older install still runs as.
PLAYER_PROGRAMS = frozenset({'cinedex', 'th_player'})


class LiveViewError(Exception):
    """No player could show the shot, and none could be started."""


class PlayerRefused(Exception):
    """A player answered with an error: ``code`` says which."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def endpoints(folder: Path) -> list[tuple[Path, dict]]:
    """The players that say how to reach them in ``folder``, newest first,
    with their files; files that do not read are left out."""
    found = []
    if not Path(folder).is_dir():
        return found
    for path in Path(folder).glob('*.json'):
        try:
            endpoint = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(endpoint.get('port'), int) or not isinstance(endpoint.get('token'), str):
                continue
            found.append((path.stat().st_mtime, path, endpoint))
        except (OSError, ValueError, AttributeError):
            continue
    found.sort(key=lambda item: item[0], reverse=True)
    return [(path, endpoint) for _, path, endpoint in found]


def call(endpoint: dict, method: str, params: dict, request_id: int = 1):
    """``method`` on the player at ``endpoint``: its result. Raises
    PlayerRefused for an error answer, OSError when it cannot be reached."""
    line = json.dumps({
        'jsonrpc': '2.0', 'id': request_id, 'method': method,
        'params': {**params, 'token': endpoint['token']},
    }) + '\n'
    with socket.create_connection(('127.0.0.1', endpoint['port']), timeout=CONNECT_TIMEOUT) as connection:
        connection.settimeout(TIMEOUT)
        connection.sendall(line.encode('utf-8'))
        reply = b''
        while not reply.endswith(b'\n'):
            chunk = connection.recv(65536)
            if not chunk:
                break
            reply += chunk
    try:
        answer = json.loads(reply.decode('utf-8'))
    except ValueError:
        raise OSError(f'the player answered no JSON: {reply[:200]!r}') from None
    if 'error' in answer:
        error = answer['error']
        raise PlayerRefused(int(error.get('code', 0)), str(error.get('message', 'refused')))
    return answer.get('result')


def player_running(pid) -> bool:
    """Whether process ``pid`` is a running player."""
    return live._process_program(pid) in PLAYER_PROGRAMS


def show(folder: Path, shot: str, frame: int | None, spawn, running=player_running) -> str:
    """Show ``shot`` (``entity:/shots/...``) at shot frame ``frame`` in a
    player of the project whose players say where they are in ``folder``;
    else ``spawn(shot, frame)`` starts one. What happened, in words.
    ``running(pid)`` tells a busy player from a gone one."""
    refusals, busy = [], []
    for path, endpoint in endpoints(folder):
        try:
            call(endpoint, 'view_shot', {'shot': shot, 'frame': frame})
            return 'shown in the open editor'
        except PlayerRefused as refusal:
            if refusal.code not in (NOT_HERE, BAD_TOKEN):
                raise LiveViewError(str(refusal)) from None
            refusals.append(str(refusal))
        except OSError as error:
            if running(endpoint.get('pid')):
                busy.append(f'pid {endpoint.get("pid")}: {error}')
                continue
            # Gone (killed, or it crashed before clearing its file).
            try:
                path.unlink()
            except OSError:
                pass
    if busy:
        raise LiveViewError('an open editor did not answer (' + '; '.join(busy) + '): try again when it is not busy')
    try:
        spawn(shot, frame)
    except (OSError, ValueError) as error:
        why = '; '.join(refusals)
        raise LiveViewError(f'{error}' + (f' (open editors: {why})' if why else '')) from None
    return 'starting the editor on the master edit'


def players_folder(project_path: str) -> Path:
    """This user's players folder for the project at ``project_path``."""
    return live.players_dir(live.live_root(), project_path)
