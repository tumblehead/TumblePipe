"""The shot slate: which shot the camera you look through is on.

A small see-through chip under the bottom-right corner of the camera's frame,
in any Scene Viewer looking through a camera of a TumblePipe workfile::

    010/0020   43/96   animation v0003   multishot anim_010     [-] [x]

The shot and the frame's place in it (``43/96``), amber in the pre / post
roll, red outside the shot. Three sizes, remembered: full, mini (shot and
place) and a dot (tucked away; click it to bring the chip back). [-] / [+]
and [x] on the chip, a double-click swaps full and mini. Settings > Projects
> Behaviour > "Show the shot in the camera view" turns it off
(``PipelinePrefs.shot_slate``).

Which shot (first match):

1. the camera node's user data ``th_shot`` (a shot URI pinned on it)
2. its own Import Shot LOP: a ``lopimportcam``'s ``loppath``, or a
   ``cameraimport`` up the camera node's inputs (``<lop>:<prim>``), walked
   up the LOP's inputs
3. a member of the open Multi named in the camera node's name (the
   template's ``anim_camera_<seq>_<shot>``)
4. the workfile, when it is a shot's
5. on a shared-timeline Multi, the member under the playhead

On a shared timeline every member is moved by its offset
(``config.multi_timeline``), so the place in the shot is the playhead minus
the member's offset.

Other packages draw their own cameras through :func:`register_camera_source`
(TumbleRig's camera rig, whose Animate camera is not an /obj node).

Nothing here cooks a node. The workfile context, the group and the shot
ranges are read once per hip (and every few seconds after, the timeline
starts can move); outside a TumblePipe workfile the slate draws nothing.
"""

from __future__ import annotations

import time

USER_KEY = 'th_shot'
TIMER_NAME = 'tumblepipe_shot_slate_timer'
TICK_MS = 250
_REFRESH_S = 3.0
MODES = ('full', 'mini', 'dot')
STATE_COLOURS = {'in': (230, 230, 230), 'preroll': (0xf2, 0xc4, 0x6e), 'postroll': (0xf2, 0xc4, 0x6e),
                 'out': (0xeb, 0x7a, 0x6a), 'unknown': (0xf2, 0xc4, 0x6e)}

_WF = {'hip': None, 't': 0.0, 'ctx': None}
_SHOTS = {}
_CAM = {}
_CAM_TTL = 1.0
_PREFS = {'on': None, 'mode': None}
_SOURCES = []
_CHIPS = {}
_PLACED = set()


def _log(msg):
    print('[tumblepipe shot slate] ' + msg)


# -- pure: what the slate says (no hou) -------------------------------------------

def frame_state(start, end, roll_start, roll_end, frame):
    """'in', 'preroll', 'postroll' or 'out' of ``frame`` against a shot's
    range (all in the same frames); None without a range."""
    if start is None or end is None:
        return None
    if start <= frame <= end:
        return 'in'
    if start - roll_start <= frame < start:
        return 'preroll'
    if end < frame <= end + roll_end:
        return 'postroll'
    return 'out'


def place_text(state, start, end, frame):
    """The place of ``frame`` in the shot: '43/96', 'preroll 6',
    'postroll 4', 'out of shot 1001-1096'."""
    if state is None:
        return f'{frame}'
    return {'in': f'{frame - start + 1}/{end - start + 1}',
            'preroll': f'preroll {start - frame}',
            'postroll': f'postroll {frame - end}',
            'out': f'out of shot {start}-{end}'}[state]


def shot_label(uri: str) -> str:
    """'entity:/shots/010/0020' -> '010/0020'."""
    segs = [s for s in uri.split(':', 1)[-1].split('/') if s]
    return '/'.join(segs[1:]) if len(segs) > 2 else (segs[-1] if segs else uri)


def member_in_name(name: str, members) -> str | None:
    """The member of a Multi named in a camera node's name
    (``anim_camera_010_0020`` -> entity:/shots/010/0020); the longest match."""
    best = None
    for m in members:
        segs = [s for s in str(m).split(':', 1)[-1].split('/') if s]
        if len(segs) < 3:
            continue
        key = f'{segs[1]}_{segs[2]}'
        if key in name and (best is None or len(key) > best[0]):
            best = (len(key), str(m))
    return best[1] if best else None


def segments(s, mode='full'):
    """The slate as (text, rgb, bold) parts for a size."""
    if not s:
        return []
    out = [(s['shot'], (245, 245, 245), True),
           (s['pos'], STATE_COLOURS.get(s['state'], STATE_COLOURS['in']), s['state'] != 'in')]
    if mode == 'full':
        for t in (s.get('ver'), s.get('note')):
            if t:
                out.append((t, (0xa6, 0xa6, 0xa6), False))
    return out


# -- preferences ----------------------------------------------------------------------

def enabled() -> bool:
    if _PREFS['on'] is None:
        _load_prefs()
    return bool(_PREFS['on'])


def mode() -> str:
    if _PREFS['mode'] is None:
        _load_prefs()
    return _PREFS['mode']


def _load_prefs():
    try:
        from tumblepipe.asset_browser.prefs import load_prefs
        p = load_prefs()
        _PREFS['on'] = bool(p.shot_slate)
        _PREFS['mode'] = p.shot_slate_size if p.shot_slate_size in MODES else 'full'
    except Exception as e:
        _log(f'prefs: {e!r}')
        _PREFS['on'], _PREFS['mode'] = True, 'full'


def _save(**kw):
    try:
        from tumblepipe.asset_browser.prefs import load_prefs, save_prefs
        p = load_prefs()
        for k, v in kw.items():
            setattr(p, k, v)
        save_prefs(p)
    except Exception as e:
        _log(f'save prefs: {e!r}')


def set_enabled(on: bool, save=True):
    """The settings switch (Settings > Projects > Behaviour)."""
    _PREFS['on'] = bool(on)
    if save:
        _save(shot_slate=bool(on))
    if not on:
        hide_all()


def set_mode(m: str):
    _PREFS['mode'] = m if m in MODES else 'full'
    _save(shot_slate_size=_PREFS['mode'])
    for c in list(_CHIPS.values()):
        try:
            c.relayout()
        except RuntimeError:
            pass


# -- the pipeline side --------------------------------------------------------------

def workfile():
    """The open hip's {'uri', 'kind' ('shot' | 'group' | 'other'),
    'department', 'version', 'group', 'slots'} or None (not a TumblePipe
    workfile). ``slots`` is the shared timeline's placed members (empty
    without one), read with the rest so a tick reads no frame ranges."""
    import hou
    hip = hou.hipFile.path()
    now = time.monotonic()
    if _WF['hip'] == hip and now - _WF['t'] < _REFRESH_S:
        return _WF['ctx']
    ctx = None
    try:
        from tumblepipe.pipe.houdini.scene_context import scene_context
        c = scene_context()
        if c is not None:
            raw = str(c.entity_uri)
            kind = 'shot' if raw.startswith('entity:/shots/') else \
                'group' if raw.startswith('groups:/shots/') else 'other'
            group, placed = None, []
            if kind == 'group':
                from tumblepipe.config.groups import get_group
                from tumblepipe.config.multi_timeline import slots
                group = get_group(c.entity_uri)
                placed = slots(group) if group is not None else []
            ctx = {'uri': raw, 'kind': kind, 'department': c.department_name,
                   'version': c.version_name, 'group': group, 'slots': placed}
    except Exception as e:
        _log(f'workfile context: {e!r}')
    # the shot ranges follow edits made since the last read
    _SHOTS.clear()
    if _WF['hip'] != hip:
        _CAM.clear()
    _WF.update(hip=hip, t=now, ctx=ctx)
    return ctx


def forget():
    _WF.update(hip=None, t=0.0, ctx=None)
    _SHOTS.clear()
    _CAM.clear()


def shot_range(uri: str):
    """(start, end, roll_start, roll_end) of a shot, or Nones."""
    if uri in _SHOTS:
        return _SHOTS[uri]
    r = (None, None, 0, 0)
    try:
        from tumblepipe.config.timeline import get_frame_range
        from tumblepipe.util.uri import Uri
        fr = get_frame_range(Uri.parse(uri))
        if fr is not None:
            r = (fr.start_frame, fr.end_frame, fr.start_roll, fr.end_roll)
    except Exception as e:
        _log(f'frame range of {uri}: {e!r}')
    _SHOTS[uri] = r
    return r


def _offset(wf, uri):
    """The member's offset on the open Multi's shared timeline (0 without),
    like ``multi_timeline.time_offset`` but from the slots read with the
    workfile."""
    if not wf or not wf.get('slots'):
        return 0
    try:
        from tumblepipe.util.uri import Uri
        member = Uri.parse(uri)
        for slot in wf['slots']:
            if slot.member == member:
                return int(slot.offset)
    except Exception:
        pass
    return 0


def _member_at(wf, frame):
    """The shared-timeline member under ``frame`` (rolls included)."""
    if not wf or not wf.get('slots'):
        return None
    try:
        for slot in wf['slots']:
            fr = slot.shared_range
            if fr.start_frame - fr.start_roll <= frame <= fr.end_frame + fr.end_roll:
                return str(slot.member)
    except Exception as e:
        _log(f'shared timeline: {e!r}')
    return None


def _import_shot_uri(node):
    if 'import_shot' not in node.type().name():
        return None
    try:
        from tumblepipe.pipe.houdini.lops.import_shot import ImportShot
        u = ImportShot(node).get_entity_uri()
        return str(u) if u is not None else None
    except Exception:
        p = node.parm('entity')
        v = p.evalAsString() if p is not None else ''
        return v if v.startswith('entity:/shots/') else None


def lop_shot(lop):
    """The shot of the first Import Shot at or above ``lop``."""
    todo, seen = [lop], set()
    while todo and len(seen) < 200:
        n = todo.pop(0)
        if n is None or n.path() in seen:
            continue
        seen.add(n.path())
        u = _import_shot_uri(n)
        if u:
            return u
        todo += [i for i in n.inputs() if i is not None]
    return None


def _source_lop(node):
    """The LOP a camera node reads: a lopimportcam's loppath, or a Camera
    Import up the node's inputs."""
    import hou
    if node.type().name() == 'lopimportcam' and node.parm('loppath') is not None:
        return node.node(node.parm('loppath').evalAsString())
    todo, seen = list(node.inputs()) if hasattr(node, 'inputs') else [], set()
    while todo and len(seen) < 200:
        n = todo.pop(0)
        if n is None or n.path() in seen:
            continue
        seen.add(n.path())
        if n.type().name() == 'cameraimport' and n.parm('camera') is not None:
            lp = n.parm('camera').evalAsString().split(':', 1)[0]
            return hou.node(lp) if lp else None
        todo += [i for i in n.inputs() if i is not None]
    return None


def camera_shot(node):
    """(shot URI, how it was found) for a camera node, or (None, None)."""
    import hou
    if node is None:
        return None, None
    key = (node.path(), hou.hipFile.path())
    hit = _CAM.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < _CAM_TTL:
        return hit[1]
    res = (None, None)
    try:
        own = node.userData(USER_KEY)
        if own:
            res = (own, 'pinned on the camera')
        else:
            lop = _source_lop(node)
            u = lop_shot(lop) if lop is not None else None
            if u:
                res = (u, 'its Import Shot')
            else:
                wf = workfile()
                if wf and wf['kind'] == 'group' and wf['group'] is not None:
                    m = member_in_name(node.name(), wf['group'].members)
                    if m:
                        res = (m, 'its name')
                elif wf and wf['kind'] == 'shot':
                    res = (wf['uri'], 'the workfile')
    except Exception as e:
        _log(f'camera shot: {e!r}')
    _CAM[key] = (now, res)
    return res


def set_camera_shot(node, uri):
    """Pin a camera to a shot (None: back to finding it)."""
    if uri:
        node.setUserData(USER_KEY, str(uri))
    elif node.userData(USER_KEY) is not None:
        node.destroyUserData(USER_KEY)
    _CAM.clear()


def slate(node, frame=None):
    """What the slate says for camera ``node`` at ``frame`` (default the
    playhead), or None: {'shot', 'pos', 'state', 'ver', 'note', 'uri', 'how'}."""
    import hou
    if not enabled():
        return None
    wf = workfile()
    if wf is None:
        return None
    f = int(round(hou.frame() if frame is None else frame))
    uri, how = camera_shot(node)
    if uri is None:
        uri = _member_at(wf, f)
        how = 'the playhead (shared timeline)' if uri else None
    ver = ' '.join(x for x in (wf['department'], wf['version']) if x)
    group = wf['uri'].rsplit('/', 1)[-1] if wf['kind'] == 'group' else ''
    note = f'multishot {group}' if group else ''
    if uri is None:
        if wf['kind'] != 'group':
            return None
        return {'shot': group, 'pos': 'shot not known', 'state': 'unknown', 'ver': ver, 'note': '',
                'uri': wf['uri'], 'how': None}
    start, end, rs, re_ = shot_range(uri)
    own = f - _offset(wf, uri)          # the shot's own frame
    st = frame_state(start, end, rs, re_, own)
    return {'shot': shot_label(uri), 'pos': place_text(st, start, end, own), 'state': st or 'in',
            'ver': ver, 'note': note, 'uri': uri, 'how': how}


# -- cameras ------------------------------------------------------------------------

def register_camera_source(fn):
    """Let a package place the slate for cameras the slate can't see itself.

    ``fn(scene_viewer)`` answers for one viewer: None (not mine: the slate
    looks for an /obj camera itself), False (mine, no slate here), or
    ``{'node': <camera node to find the shot from>, 'rect': (x0, y0, x1,
    y1) the frame in the viewport widget, 'widget': <the viewport widget>}``.
    """
    if fn not in _SOURCES:
        _SOURCES.append(fn)


def unregister_camera_source(fn):
    if fn in _SOURCES:
        _SOURCES.remove(fn)


def gate_rect(vp, widget, xform, focal, aperture, resx, resy, pixelaspect=1.0):
    """A camera's frame in viewport-widget coordinates (x0, y0, x1, y1, y
    down), its corners projected through the viewport."""
    import hou
    rows = xform.asTupleOfTuples()
    X, Y, Z = (hou.Vector3(rows[i][:3]) for i in range(3))
    P = hou.Vector3(xform.extractTranslates())
    aspect = resx * max(pixelaspect, 1e-4) / max(resy, 1)
    hw = 0.5 * aperture / max(focal, 1e-3)
    hh = hw / aspect
    dpr = float(widget.devicePixelRatioF())
    vx, vy, _vw, _vh = vp.size()
    H = widget.height() * dpr
    pts = []
    for sx, sy in ((-1, -1), (1, 1)):
        x, y = vp.mapToScreen(P + X * (sx * hw) + Y * (sy * hh) - Z)
        pts.append(((vx + x) / dpr, (H - (vy + y)) / dpr))
    (x0, y1), (x1, y0) = pts
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


_VP_WIDGETS = {}
_VP_SEARCH_EVERY = 1.0


def _viewport_widget(sv):
    """The Scene Viewer's RE_Window (one Qt widget holds all its viewports):
    the biggest visible one whose centre is inside the viewer's own screen
    area. Cached per viewer; the search walks the main window's widgets, so
    while it finds nothing it runs at most once a second."""
    import hou
    from PySide6 import QtCore, QtWidgets
    try:
        area = sv.qtScreenGeometry()
        w, last = _VP_WIDGETS.get(sv.name(), (None, 0.0))
        try:
            if w is not None and w.isVisible() and area.contains(
                    w.mapToGlobal(QtCore.QPoint(w.width() // 2, w.height() // 2))):
                return None if w.window().isMinimized() else w
        except RuntimeError:
            pass
        now = time.monotonic()
        if w is None and now - last < _VP_SEARCH_EVERY:
            return None
        best, best_area = None, 0
        for c in hou.qt.mainWindow().findChildren(QtWidgets.QWidget, 'RE_Window'):
            if not c.isVisible():
                continue
            centre = c.mapToGlobal(QtCore.QPoint(c.width() // 2, c.height() // 2))
            if area.contains(centre) and c.width() * c.height() > best_area:
                best, best_area = c, c.width() * c.height()
        _VP_WIDGETS[sv.name()] = (best, now)
        return best
    except Exception:
        return None


def _obj_camera(sv, widget):
    """{'node', 'rect', 'widget'} for a viewer looking through an /obj camera."""
    import hou
    vp = sv.curViewport()
    cam = vp.camera()
    if cam is None or not isinstance(cam, hou.ObjNode):
        return None
    ev = lambda n, d: float(cam.parm(n).eval()) if cam.parm(n) is not None else d
    rect = gate_rect(vp, widget, cam.worldTransform(), ev('focal', 50.0), ev('aperture', 41.4214),
                     int(ev('resx', 1920)), int(ev('resy', 1080)), ev('aspect', 1.0))
    return {'node': cam, 'rect': rect, 'widget': widget}


# -- the chip -----------------------------------------------------------------------

def _chip_class():
    import hou
    from PySide6 import QtCore, QtGui, QtWidgets
    Qt = QtCore.Qt

    class Chip(QtWidgets.QWidget):
        """The slate chip: frameless, see-through, clickable."""

        def __init__(self):
            super().__init__(hou.qt.mainWindow())
            self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setAttribute(Qt.WA_ShowWithoutActivating)
            self.setMouseTracking(True)
            self.slate = None
            self.scale = 1.0
            self.anchor = QtCore.QPoint()     # global: the frame's bottom-right corner
            self.room_below = True
            self.hover = None
            self.boxes = {}

        def fonts(self):
            f = QtGui.QFont(self.font())
            f.setPixelSize(max(8, int(10 * self.scale)))
            fb = QtGui.QFont(f)
            fb.setBold(True)
            return f, fb

        def relayout(self):
            s = self.scale
            m = mode()
            if m == 'dot':
                w = h = int(12 * s)
                self.boxes = {'dot': QtCore.QRectF(0, 0, w, h)}
            else:
                f, fb = self.fonts()
                fm, fmb = QtGui.QFontMetricsF(f), QtGui.QFontMetricsF(fb)
                segs = segments(self.slate, m)
                tw = sum((fmb if b else fm).horizontalAdvance(t) for t, _c, b in segs) + 9 * s * (len(segs) - 1)
                bw = 14 * s
                h = int(fm.height() + 6 * s)
                x = 8 * s + tw + 6 * s
                self.boxes = {'size': QtCore.QRectF(x, (h - bw) / 2, bw, bw),
                              'close': QtCore.QRectF(x + bw + 2 * s, (h - bw) / 2, bw, bw)}
                w = int(x + 2 * bw + 2 * s + 5 * s)
            gap = int(6 * s)
            y = self.anchor.y() + gap if self.room_below else self.anchor.y() - h - gap
            # right-aligned under the frame: the viewport's own prompt line
            # ("Left mouse tumbles...") runs from the left there
            g = QtCore.QRect(self.anchor.x() - w, y, w, h)
            if self.geometry() != g:
                self.setGeometry(g)
            self.update()

        def paintEvent(self, e):
            if not self.slate:
                return
            s = self.scale
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            hot = self.hover is not None
            m = mode()
            if m == 'dot':
                col = QtGui.QColor(*STATE_COLOURS.get(self.slate['state'], STATE_COLOURS['in']))
                col.setAlpha(230 if hot else 110)
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                p.drawEllipse(self.rect().adjusted(2, 2, -2, -2))
                p.end()
                return
            # see-through, opaque while the mouse is on it
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(0x1e, 0x1e, 0x1e, 225 if hot else 115))
            p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 5 * s, 5 * s)
            f, fb = self.fonts()
            fm = QtGui.QFontMetricsF(f)
            x, base = 8 * s, (self.height() - fm.height()) / 2 + fm.ascent()
            alpha = 255 if hot else 205
            for t, rgb, bold in segments(self.slate, m):
                p.setFont(fb if bold else f)
                p.setPen(QtGui.QColor(*rgb, alpha))
                p.drawText(QtCore.QPointF(x, base), t)
                x += QtGui.QFontMetricsF(fb if bold else f).horizontalAdvance(t) + 9 * s
            for key, r in self.boxes.items():
                on = self.hover == key
                if on:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QtGui.QColor(255, 255, 255, 40))
                    p.drawRoundedRect(r, 3 * s, 3 * s)
                p.setPen(QtGui.QPen(QtGui.QColor(230, 230, 230, 230 if on else (150 if hot else 90)), 1.2 * s))
                c, k = r.center(), 3.2 * s
                if key == 'close':
                    p.drawLine(QtCore.QPointF(c.x() - k, c.y() - k), QtCore.QPointF(c.x() + k, c.y() + k))
                    p.drawLine(QtCore.QPointF(c.x() - k, c.y() + k), QtCore.QPointF(c.x() + k, c.y() - k))
                else:
                    p.drawLine(QtCore.QPointF(c.x() - k, c.y()), QtCore.QPointF(c.x() + k, c.y()))
                    if m == 'mini':
                        p.drawLine(QtCore.QPointF(c.x(), c.y() - k), QtCore.QPointF(c.x(), c.y() + k))
            p.end()

        def _hit(self, pos):
            for key, r in self.boxes.items():
                if r.adjusted(-2, -2, 2, 2).contains(pos):
                    return key
            return 'body' if self.rect().contains(pos.toPoint()) else None

        def mouseMoveEvent(self, e):
            h = self._hit(e.position())
            if h != self.hover:
                self.hover = h
                sl = self.slate or {}
                self.setToolTip({'size': 'Smaller' if mode() == 'full' else 'Bigger',
                                 'close': 'Tuck away (click the dot to bring it back)',
                                 'dot': 'The shot: click to show it',
                                 'body': f"{sl.get('uri', '')}  (from {sl.get('how') or '?'})"}.get(h, ''))
                self.update()

        def enterEvent(self, e):
            self.hover = 'body'
            self.update()

        def leaveEvent(self, e):
            self.hover = None
            self.update()

        def mousePressEvent(self, e):
            h = self._hit(e.position())
            if h == 'close':
                set_mode('dot')
            elif h == 'size':
                set_mode('mini' if mode() == 'full' else 'full')
            elif h == 'dot':
                set_mode('mini')

        def mouseDoubleClickEvent(self, e):
            if self._hit(e.position()) == 'body':
                set_mode('mini' if mode() == 'full' else 'full')

    return Chip


def showing(viewer_name) -> str | None:
    """The chip's size where it shows for a viewer ('full' / 'mini' /
    'dot'), or None: a package drawing next to the frame can keep clear."""
    c = _CHIPS.get(viewer_name)
    try:
        return mode() if c is not None and c.isVisible() else None
    except RuntimeError:
        return None


def place(viewer_name, widget, rect, s, scale=1.0):
    """Show slate ``s`` for a viewer under its frame ``rect``."""
    from PySide6 import QtCore
    if not s or widget is None:
        return
    chip = _CHIPS.get(viewer_name)
    if chip is None:
        chip = _CHIPS[viewer_name] = _chip_class()()
    _x0, _y0, x1, y1 = rect
    chip.slate, chip.scale = s, scale
    gr = widget.mapToGlobal(QtCore.QPoint(0, 0))
    chip.room_below = y1 + 30 * scale < widget.height()
    chip.anchor = QtCore.QPoint(gr.x() + int(min(x1, widget.width() - 4)), gr.y() + int(min(y1, widget.height() - 4)))
    chip.relayout()
    if chip.isHidden():
        chip.show()
    chip.update()
    _PLACED.add(viewer_name)


def hide_all():
    for c in list(_CHIPS.values()):
        try:
            c.hide()
        except RuntimeError:
            pass


def _scale(widget=None):
    """Houdini's own UI size."""
    import hou
    try:
        return float(hou.ui.globalScaleFactor())
    except Exception:
        return 1.0


def _app_active():
    """Houdini is the front application: the chips are windows of their own
    and would float over other apps otherwise."""
    from PySide6 import QtCore, QtGui
    try:
        return QtGui.QGuiApplication.applicationState() == QtCore.Qt.ApplicationActive
    except Exception:
        return True


def tick():
    """Place the chip in every Scene Viewer looking through a camera."""
    import hou
    _PLACED.clear()
    try:
        if enabled() and _app_active() and workfile() is not None:
            for t in hou.ui.paneTabs():
                if t.type() != hou.paneTabType.SceneViewer or not t.isCurrentTab():
                    continue
                spec = None
                for fn in list(_SOURCES):
                    try:
                        spec = fn(t)
                    except Exception as e:
                        _log(f'camera source: {e!r}')
                        spec = None
                    if spec is not None:
                        break
                if spec is False:
                    continue
                if spec is None:
                    widget = _viewport_widget(t)
                    spec = _obj_camera(t, widget) if widget is not None else None
                if not spec:
                    continue
                s = slate(spec['node'])
                if s:
                    place(t.name(), spec['widget'], spec['rect'], s, spec.get('scale') or _scale(spec['widget']))
    except Exception as e:
        _log(f'tick: {e!r}')
    for name in list(_CHIPS):
        if name in _PLACED:
            continue
        try:
            if not _CHIPS[name].isHidden():
                _CHIPS[name].hide()
        except RuntimeError:
            _CHIPS.pop(name, None)


def start():
    """Run the slate's timer (TumblePipe's startup). One per session, found
    again by name, so a second call does nothing."""
    import hou
    from PySide6 import QtCore
    main = hou.qt.mainWindow()
    if main is None or main.findChild(QtCore.QTimer, TIMER_NAME) is not None:
        return
    t = QtCore.QTimer(main)
    t.setObjectName(TIMER_NAME)
    t.setInterval(TICK_MS)
    t.timeout.connect(tick)
    t.start()
