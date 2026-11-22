"""The entities one th::export_layer writes its layer to (#91).

A department layer authored in one test shot (render settings, an even
lighting setup) can be exported to several shots or assets at once: the
layer is cooked once, then each target gets its own version of it, shifted
so the test shot's first frame lands on the target's first frame.

The node's Targets menu picks them:

* ``entity``: the node's own entity alone, as every export before #91.
* ``chosen``: the entities listed on the node (picked with Browse).
* ``all_shots`` / ``all_assets``: every shot (or asset) of the project,
  read when the export runs, so shots made later are included.

No ``hou`` here: the node reads its parms and hands them in.
"""

from __future__ import annotations

from typing import Callable, Iterable

from tumblepipe.util.uri import Uri

ENTITY = 'entity'
CHOSEN = 'chosen'
ALL_SHOTS = 'all_shots'
ALL_ASSETS = 'all_assets'

MODES = (
    (ENTITY, 'This entity'),
    (CHOSEN, 'Chosen'),
    (ALL_SHOTS, 'All shots'),
    (ALL_ASSETS, 'All assets'),
)


def parse_chosen(raw: str) -> list[Uri]:
    """The entity URIs of the node's Targets list (whitespace-separated),
    each once, in order. A word that is not an entity URI is left out."""
    out: list[Uri] = []
    seen: set[str] = set()
    for word in raw.split():
        try:
            uri = Uri.parse(word)
        except Exception:
            continue
        if uri.purpose != 'entity' or len(uri.segments) < 2:
            continue
        if str(uri) in seen:
            continue
        seen.add(str(uri))
        out.append(uri)
    return out


def format_chosen(uris: Iterable[Uri]) -> str:
    """The Targets list text for ``uris``: :func:`parse_chosen` reads it
    back as the same list."""
    return ' '.join(str(uri) for uri in parse_chosen(' '.join(map(str, uris))))


def resolve(
    mode: str,
    chosen: list[Uri],
    source: Uri,
    list_all: Callable[[str], list[Uri]],
) -> list[Uri]:
    """The targets ``mode`` names. ``list_all(context)`` lists every shot
    (``'shots'``) or asset (``'assets'``) of the project. An unknown mode is
    the node's own entity, as a node saved before the menu existed."""
    if mode == CHOSEN:
        return parse_chosen(' '.join(map(str, chosen)))
    if mode == ALL_SHOTS:
        return list(list_all('shots'))
    if mode == ALL_ASSETS:
        return list(list_all('assets'))
    return [source]


def problems(
    source: Uri,
    targets: list[Uri],
    exists: Callable[[Uri], bool],
    fps_of: Callable[[Uri], int | None],
) -> list[str]:
    """Why the export cannot write to ``targets``; empty when it can.

    The layer is cooked once, in the source entity's scene: a target must
    be the same kind (shot or asset) and play at the same fps, or the
    copy it gets would be wrong for it. A target must also exist: writing
    a version manufactures whatever entity it names.
    """
    if not targets:
        return ['No targets: pick at least one entity, or set Targets to This entity.']
    out = []
    kind = source.segments[0] if source.segments else ''
    other = [t for t in targets if not t.segments or t.segments[0] != kind]
    if other:
        out.append(
            f'Targets must be {kind}, like the node\'s own entity: '
            + ', '.join(map(str, other))
        )
    missing = [t for t in targets if t not in other and not exists(t)]
    if missing:
        out.append('Targets that do not exist: ' + ', '.join(map(str, missing)))
    source_fps = fps_of(source)
    off = [
        t for t in targets
        if t not in other and t not in missing and fps_of(t) != source_fps
    ]
    if off:
        out.append(
            f'Targets at another fps than the node\'s own entity ({source_fps}): '
            + ', '.join(f'{t} ({fps_of(t)})' for t in off)
        )
    return out


def frame_shift(source_first: int | None, target_first: int | None) -> int:
    """How far a target's copy moves: the source's first frame lands on the
    target's. 0 when either has no frame range (a static asset)."""
    if source_first is None or target_first is None:
        return 0
    return int(target_first) - int(source_first)
