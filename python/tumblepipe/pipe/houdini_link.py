"""How the editor (Cinedex) reaches the Houdini session that started it.

An artist opens Cinedex from Houdini (Open Timeline, Play in Cinedex, Live
view), then picks Open in Houdini on a clip. That workfile should open in
the Houdini they came from, not in a new one. So a Houdini session that
starts the editor listens on ``127.0.0.1`` (a port the system picks) and
puts the address and a token in the editor's environment
(:func:`child_env`): :data:`ENDPOINT_ENV` and :data:`TOKEN_ENV`. The Rust
side is ``th_pipe::houdini_link``, which decides where a workfile opens:
the session that has it open, else this one, else a new Houdini.

A call is one line of JSON-RPC 2.0, its params carrying the token; the
answer is one line back (:func:`answer_line`):

- ``ping`` → ``{"pid"}``;
- ``open_workfile`` ``{"path", "entity", "department"}`` → ``{"outcome":
  "opened" | "already_open" | "cancelled"}``, from the registered opener,
  run on Houdini's main thread; a refusal (:class:`NotHere`, the workfile
  is not this session's to open) is -32002, which sends the editor on to a
  new Houdini; any other failure is -32000 with its message, which the
  editor shows and stops at.

A wrong or missing token is -32001: the port is someone else's now, and the
editor takes this session for gone. Nothing listens until a session
registers an opener (``tumblepipe.startup``, graphical sessions only) and
starts an editor: without Houdini (Desktop's Open Cinedex, the tests)
:func:`child_env` is empty and the editor has no link.

Without ``hou``, so it can be tested on its own.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socketserver
import sys
import threading
from typing import Callable

log = logging.getLogger(__name__)

#: Where this session listens, as ``127.0.0.1:<port>``.
ENDPOINT_ENV = 'TH_HOUDINI_ENDPOINT'
#: The token the editor's calls carry.
TOKEN_ENV = 'TH_HOUDINI_TOKEN'

#: The outcomes ``open_workfile`` answers.
OPENED = 'opened'
ALREADY_OPEN = 'already_open'
CANCELLED = 'cancelled'
OUTCOMES = frozenset({OPENED, ALREADY_OPEN, CANCELLED})

BAD_TOKEN = -32001
NOT_HERE = -32002
REFUSED = -32000
NO_METHOD = -32601
BAD_PARAMS = -32602
PARSE = -32700

#: How long a call waits for the main thread: the artist may sit on the
#: save prompt (the editor waits as long, ``th_pipe::houdini_link``).
ANSWER_WITHIN = 30 * 60.0
#: How long the editor may take to send its line.
READ_WITHIN = 5.0
#: The longest call line read.
MAX_LINE = 64 * 1024

#: ``opener(path, entity, department) -> outcome``, run on the main thread.
Opener = Callable[[str, str, str], str]
#: Runs a function on the main thread, soon (it returns at once).
MainThread = Callable[[Callable[[], None]], None]


class NotHere(Exception):
    """This session is not the one to open the workfile (another project's,
    say): the editor opens it elsewhere."""


def same_file(a: str, b: str) -> bool:
    """Whether two spellings of a path name one file, as two programs on
    one machine spell it: either slash, a trailing one or not, any case on
    Windows (``th_pipe::desktop::same_file_key``)."""
    def key(path: str) -> str:
        slashed = path.replace('\\', '/').rstrip('/')
        return slashed.lower() if sys.platform == 'win32' else slashed
    return key(a) == key(b)


def _reply(call_id, result=None, error: tuple[int, str] | None = None) -> str:
    message: dict = {'jsonrpc': '2.0', 'id': call_id}
    if error is None:
        message['result'] = result
    else:
        message['error'] = {'code': error[0], 'message': error[1]}
    return json.dumps(message)


def answer_line(line: str, token: str, open_workfile: Callable[[str, str, str], str]) -> str:
    """The answer to the call ``line``, for a session whose token is
    ``token``; ``open_workfile`` opens (it is called on this thread, and
    raises :class:`NotHere` or any other failure)."""
    try:
        call = json.loads(line)
    except ValueError:
        return _reply(None, error=(PARSE, 'not JSON'))
    if not isinstance(call, dict):
        return _reply(None, error=(PARSE, 'not a call'))
    call_id = call.get('id')
    params = call.get('params')
    if not isinstance(params, dict) or not isinstance(params.get('token'), str) \
            or not secrets.compare_digest(params['token'].encode(), token.encode()):
        return _reply(call_id, error=(BAD_TOKEN, 'wrong token'))
    method = call.get('method')
    if method == 'ping':
        return _reply(call_id, {'pid': os.getpid()})
    if method != 'open_workfile':
        return _reply(call_id, error=(NO_METHOD, f'no method {method!r}'))
    args = [params.get(key) for key in ('path', 'entity', 'department')]
    if not all(isinstance(a, str) for a in args) or not args[0]:
        return _reply(call_id, error=(BAD_PARAMS, 'open_workfile needs a path, an entity and a department'))
    try:
        outcome = open_workfile(*args)
    except NotHere as exc:
        return _reply(call_id, error=(NOT_HERE, str(exc) or 'not this Houdini'))
    except Exception as exc:  # noqa: BLE001 - said to the artist, in the editor
        log.exception('Opening %s for the editor failed', args[0])
        return _reply(call_id, error=(REFUSED, str(exc) or type(exc).__name__))
    if outcome not in OUTCOMES:
        return _reply(call_id, error=(REFUSED, f'unknown outcome {outcome!r}'))
    return _reply(call_id, {'outcome': outcome})


def on_main_thread(main_thread: MainThread, func: Callable[[], str], within: float = ANSWER_WITHIN) -> Callable[[], str]:
    """``func`` as a call that runs it with ``main_thread`` and waits up
    to ``within`` seconds for what it returns or raises."""
    def call() -> str:
        done = threading.Event()
        box: dict = {}

        def run() -> None:
            try:
                box['value'] = func()
            except BaseException as exc:  # noqa: BLE001 - raised again below
                box['error'] = exc
            finally:
                done.set()

        main_thread(run)
        if not done.wait(within):
            raise TimeoutError('Houdini was busy and did not get to it')
        if 'error' in box:
            raise box['error']
        return box['value']
    return call


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        link: Link = self.server.link  # type: ignore[attr-defined]
        self.connection.settimeout(READ_WITHIN)
        try:
            line = self.rfile.readline(MAX_LINE).decode('utf-8', 'replace')
        except OSError:
            return
        self.connection.settimeout(None)
        answer = answer_line(line, link.token, link.open_workfile)
        try:
            self.wfile.write((answer + '\n').encode('utf-8'))
        except OSError:
            pass  # the editor went away while it waited


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False


class Link:
    """A listening endpoint that opens workfiles with ``opener`` on the
    main thread (``main_thread``)."""

    def __init__(self, opener: Opener, main_thread: MainThread):
        self.token = secrets.token_hex(32)
        self._opener = opener
        self._main_thread = main_thread
        self._server = _Server(('127.0.0.1', 0), _Handler)
        self._server.link = self  # type: ignore[attr-defined]
        threading.Thread(
            target=self._server.serve_forever, name='th: editor link', daemon=True,
        ).start()

    @property
    def endpoint(self) -> str:
        host, port = self._server.server_address[:2]
        return f'{host}:{port}'

    def open_workfile(self, path: str, entity: str, department: str) -> str:
        return on_main_thread(self._main_thread, lambda: self._opener(path, entity, department))()

    def env(self) -> dict[str, str]:
        return {ENDPOINT_ENV: self.endpoint, TOKEN_ENV: self.token}

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


_lock = threading.Lock()
_registered: tuple[Opener, MainThread] | None = None
_link: Link | None = None


def register(opener: Opener, main_thread: MainThread) -> None:
    """Let editors this session starts open workfiles here: ``opener``
    opens one (on the main thread, as ``main_thread`` runs it) and says
    which of :data:`OUTCOMES` it came to. Listening starts with the first
    editor (:func:`child_env`)."""
    global _registered
    with _lock:
        _registered = (opener, main_thread)


def child_env() -> dict[str, str]:
    """The environment an editor this session starts gets to reach it:
    empty when nothing registered (no Houdini GUI here), or the endpoint
    could not start (the editor then opens workfiles as before)."""
    global _link
    with _lock:
        if _registered is None:
            return {}
        if _link is None:
            try:
                _link = Link(*_registered)
            except OSError:
                log.exception('The editor link could not start')
                return {}
        return _link.env()


def _reset() -> None:
    """Forget the registration and stop listening (tests)."""
    global _registered, _link
    with _lock:
        if _link is not None:
            _link.close()
        _registered, _link = None, None
