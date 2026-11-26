"""th::import_rigs follows the shot stage: the pure half.

Magnus (rfe): artists added rigs in th::animate that did not match the USD
assets on the stage. Rows were filled once by the animation template and
never synced again; a character maps to its stage prim only by name
(``<asset>``, ``<asset>0`` ..), so an asset added or removed after the
workfile was made, a row added by hand, Instances typed on a row, or copies
with gaps in their names (Roommate0, Roommate2) all came apart silently.

With Rig Sync on Follow Stage (the default) the stage entering th::animate
is the source of truth and the rows are derived from it
(:func:`plan_stage_sync`): one row per animatable asset on the stage that
has a SOP rig, its characters named after the stage copies, the artist's
channel and version pins and Skip kept. Adding a rig the other way puts
the asset on the stage first, through a th::import_assets th::animate
manages (:func:`plan_asset_add`).

What never happens on its own:

- a character with keys is never dropped. A keyed character the stage no
  longer has stays on its row (the rig makes it, at the origin with no
  lookdev, and the copies warning says so); a row whose asset left the
  stage stays while any of its characters is keyed. The one exception
  moves the keys rather than dropping them: an asset animated as one copy
  (X) that the stage now has several of (X0, X1 ..) gets X's keys moved
  to X0 (``moved_keys``, applied by ``animate_sync.move_keys``), unless
  X0 has keys of its own: keys are never overwritten;
- a row that does not follow the stage (its stage names empty: added by
  hand, or on the node before Follow Stage existed) is held, never
  dropped, while its asset is not on the stage: the comment names it as
  not on the stage, and Add Held to Stage puts it there;
- the shot's scene (Root) is never written: an Instances edit on a row
  whose asset the Root gives goes to Set Scene Copies, which asks first.

Plain values only; the Houdini half is ``sops.import_rigs`` and
``animate_sync``. ``tests/test_rig_stage_sync.py`` holds the rules.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace

from tumblepipe.util.uri import Uri

#: Rig Sync menu tokens (parm ``rig_sync``).
FOLLOW = 'follow'
MANUAL = 'manual'
#: ``sync_version`` a node has once it synced under these rules; 0 is a
#: node from before them (migrated on its first sync).
SYNC_VERSION = 1


def instance_names(asset_uri: Uri, instances: int) -> list[str]:
    """``import_rig.instance_names`` without importing the Houdini module:
    the asset name for one copy, else ``<asset>0`` .. ``<asset><N-1>``."""
    base_name = asset_uri.segments[-1]
    if instances == 1:
        return [base_name]
    return [f'{base_name}{index}' for index in range(instances)]


def parse_names(raw: str) -> tuple[str, ...]:
    """A row's ``names#`` parm as names: space separated (entity names have
    no spaces), duplicates dropped, order kept."""
    return tuple(dict.fromkeys(raw.split()))


def format_names(names) -> str:
    return ' '.join(names)


@dataclass(frozen=True)
class StageAsset:
    """An animatable asset on the stage: its copies' prim names (stage
    order), and the channel its SOP rig comes from (None: no SOP rig; with
    ``lop_rig`` its newest rig is a LOP rig the SOP rig nodes cannot load)."""
    asset: Uri
    channel: str | None
    names: tuple[str, ...]
    lop_rig: bool = False


@dataclass(frozen=True)
class RigRow:
    """One th::import_rigs row. ``names`` are the stage copies the row
    follows (empty: a row that does not follow the stage, its characters
    ``instance_names``); ``skip`` leaves the row out of the import."""
    asset: Uri
    channel: str
    version: str = 'latest'
    instances: int = 1
    names: tuple[str, ...] = ()
    skip: bool = False

    @property
    def characters(self) -> tuple[str, ...]:
        """The characters the row's rig import makes."""
        if self.names:
            return self.names
        return tuple(instance_names(self.asset, self.instances)) if self.instances > 0 else ()

    @property
    def follows(self) -> bool:
        return bool(self.names)


@dataclass(frozen=True)
class SyncPlan:
    """What a sync makes of a node's rows.

    ``rows`` the node's rows after it; ``held`` the rows kept though their
    asset is not on the stage; ``kept_names`` ``(asset, name)`` keyed
    characters kept on a row though the stage has no such copy;
    ``moved_keys`` ``(old, new)`` character keys to move (one copy became
    several: X -> X0); ``beside`` ``(old, new)`` such a move not made
    because ``new`` has keys or Animate data of its own; ``notes`` lines for the node comment; ``changed``
    whether ``rows`` differ from the rows given."""
    rows: tuple[RigRow, ...]
    held: tuple[RigRow, ...] = ()
    kept_names: tuple[tuple[Uri, str], ...] = ()
    moved_keys: tuple[tuple[str, str], ...] = ()
    beside: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    changed: bool = False


def _short(uris) -> str:
    return ', '.join(uri.segments[-1] for uri in uris)


def _one_to_many(row: RigRow, names: list[str], keyed) -> tuple[str, str] | None:
    """``(X, X0)`` when ``row`` made the one character X (the asset name),
    the stage now has several copies, X0 among them, and no X, and X
    carries keys: X's keys belong to X0, the copy the Duplicate LOP left
    where X was."""
    base = row.asset.segments[-1]
    if row.characters != (base,) or base in names or base not in keyed:
        return None
    target = f'{base}0'
    if len(names) < 2 or target not in names:
        return None
    return base, target


def plan_stage_sync(stage_assets, current_rows, keyed_characters=(), *,
                    move_keys: bool = True, hold=()) -> SyncPlan:
    """The rows a th::import_rigs on Follow Stage holds for ``stage_assets``
    (``StageAsset``, stage order), given its ``current_rows`` (``RigRow``)
    and the characters a Scene Animate of its dive holds keys for.

    - every stage asset with a SOP rig gets exactly one row, its ``names``
      the stage copies (plus any keyed character of the row the stage
      lacks, kept at the end) and Instances their count;
    - a row keeps its place, channel, version and Skip; a new asset's row
      comes at the end, on the rig's channel and 'latest';
    - a row whose asset is off the stage (or lost its SOP rig) stays,
      unchanged, when it does not follow the stage or any of its
      characters is keyed (``held``), and goes otherwise;
    - a second row of the same asset goes (one row per asset);
    - ``move_keys`` (the default): X with keys becoming X0, X1.. moves X's
      keys to X0 (``moved_keys``) instead of keeping X; never onto keys:
      when X0 has keys of its own, or X is in ``hold`` (a Scene Animate
      keeps something of X0 already), X is kept beside it and a note says
      why.

    Idempotent: planning ``rows`` again changes nothing (a planned move
    applied leaves X with no keys, so nothing moves again).
    """
    keyed = set(keyed_characters)
    hold = set(hold)
    by_asset: dict[Uri, StageAsset] = {}
    for stage_asset in stage_assets:
        by_asset.setdefault(stage_asset.asset, stage_asset)

    rows, held, kept_names, moved, notes = [], [], [], [], []
    beside = []
    seen: set[Uri] = set()
    duplicates = []
    for row in current_rows:
        if row.asset in seen:
            duplicates.append(row.asset)
            continue
        seen.add(row.asset)
        stage_asset = by_asset.get(row.asset)
        if stage_asset is None or stage_asset.channel is None:
            if not row.follows or any(name in keyed for name in row.characters):
                rows.append(row)
                held.append(row)
            continue
        names = list(stage_asset.names)
        for name in row.characters:
            if name in names or name not in keyed:
                continue
            move = _one_to_many(row, names, keyed) if move_keys else None
            if move is not None:
                if move[1] not in keyed and move[0] not in hold:
                    moved.append(move)
                    continue
                beside.append(move)
            names.append(name)
            kept_names.append((row.asset, name))
        rows.append(replace(row, instances=len(names), names=tuple(names)))
    for stage_asset in stage_assets:
        if stage_asset.asset in seen or stage_asset.channel is None:
            continue
        seen.add(stage_asset.asset)
        rows.append(RigRow(
            asset=stage_asset.asset, channel=stage_asset.channel,
            version='latest', instances=len(stage_asset.names),
            names=tuple(stage_asset.names),
        ))

    unrigged = [a.asset for a in stage_assets if a.channel is None and not a.lop_rig]
    lop_rigs = [a.asset for a in stage_assets if a.channel is None and a.lop_rig]
    if held:
        notes.append(
            'Not on the stage (held): ' + _short(row.asset for row in held)
            + '. Add Held to Stage puts them on it, or remove the rows.')
    if kept_names:
        notes.append(
            'Keyed, not on the stage (kept): '
            + ', '.join(name for _asset, name in kept_names) + '.')
    if moved:
        notes.append(
            'Keys moved to the first copy (one copy became several): '
            + ', '.join(f'{a} -> {b}' for a, b in moved) + '.')
    for old, new in beside:
        notes.append(
            f'{old} kept beside {new}: {new} already has keys or Animate data '
            f'of its own, so {old}\'s keys were not moved onto it. Copy what {new} needs with Copy '
            f'Animation, then remove {old}\'s keys.')
    if duplicates:
        notes.append('Second row dropped: ' + _short(dict.fromkeys(duplicates)) + '.')
    if unrigged:
        notes.append('Animatable, no rig published: ' + _short(unrigged))
    if lop_rigs:
        notes.append('LOP rig (th::import_layer brings it in): ' + _short(lop_rigs))
    rows = tuple(rows)
    return SyncPlan(
        rows=rows, held=tuple(held), kept_names=tuple(kept_names),
        moved_keys=tuple(moved), beside=tuple(beside), notes=tuple(notes),
        changed=rows != tuple(current_rows),
    )


def migration_note(current_rows, plan: SyncPlan) -> str:
    """The one-time note when a node from before Follow Stage syncs first:
    '' when its rows were what the stage gives (it follows silently), else
    what it did and what it held."""
    if not plan.held and [(r.asset, r.instances) for r in current_rows] == [
        (r.asset, r.instances) for r in plan.rows
    ]:
        return ''
    parts = ['Rig Sync is now Follow Stage: the rows follow the shot stage.']
    if plan.held:
        parts.append(
            f'{_short(row.asset for row in plan.held)} '
            f'{"is" if len(plan.held) == 1 else "are"} not on the stage and '
            'kept; Add Held to Stage puts them on it.')
    return ' '.join(parts)


# --------------------------------------------------------------------------- #
# Adding a character the other way
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AddPlan:
    """What Add Characters does with the picks.

    ``imports``: assets to add to the th::import_assets th::animate
    manages (off the stage, not in the shot's scene, not imported there
    yet); ``unskip``: picks whose row is skipped (it comes back);
    ``rows_only``: assets to give a row with no stage asset behind it
    (adding assets is off), held until they are on the stage; ``in_scene``:
    picks the shot's scene lists that the stage lacks (re-import the shot
    instead of importing a second copy); ``already``: picks with a live row
    already; ``unrigged``: picks with no SOP rig."""
    imports: tuple[Uri, ...] = ()
    unskip: tuple[Uri, ...] = ()
    rows_only: tuple[Uri, ...] = ()
    in_scene: tuple[Uri, ...] = ()
    already: tuple[Uri, ...] = ()
    unrigged: tuple[Uri, ...] = ()


def plan_asset_add(picked, *, on_stage, scene_assets=(), managed=(), rows=(),
                   skipped=(), rigged=lambda uri: True,
                   add_assets: bool = True) -> AddPlan:
    """``AddPlan`` for ``picked`` (asset URIs, the picker's order).

    ``on_stage`` the assets on the stage; ``scene_assets`` those the shot's
    scene (Root) lists; ``managed`` the managed import's rows; ``rows`` the
    node's rows' assets, ``skipped`` those skipped; ``rigged(uri)`` whether
    the asset has a SOP rig. ``unskip`` is every rigged pick whose row is
    skipped; each other pick is in exactly one other list (one on the stage
    with a skipped row is in ``unskip`` only). Never imports
    an asset already on the stage, listed by the scene or imported by the
    managed node, so applying the plan and planning again imports nothing.
    """
    on_stage, scene_assets = set(on_stage), set(scene_assets)
    managed, rows, skipped = set(managed), set(rows), set(skipped)
    lists = {key: [] for key in ('imports', 'unskip', 'rows_only', 'in_scene',
                                 'already', 'unrigged')}
    for asset in dict.fromkeys(picked):
        if not rigged(asset):
            lists['unrigged'].append(asset)
            continue
        if asset in skipped:
            # Picked to animate: its row comes back, wherever the asset is.
            lists['unskip'].append(asset)
        if asset in on_stage:
            if asset not in skipped:
                lists['already'].append(asset)
        elif asset in managed:
            # Imported by the managed node already, not composed yet.
            lists['already'].append(asset)
        elif asset in scene_assets:
            lists['in_scene'].append(asset)
        elif add_assets:
            lists['imports'].append(asset)
        elif asset in rows:
            lists['already'].append(asset)
        else:
            lists['rows_only'].append(asset)
    return AddPlan(**{key: tuple(value) for key, value in lists.items()})


def add_text(plan: AddPlan) -> str:
    """The status line after Add Characters."""
    parts = []
    if plan.imports:
        parts.append('Added to the stage: ' + _short(plan.imports) + '.')
    if plan.unskip:
        parts.append('No longer skipped: ' + _short(plan.unskip) + '.')
    if plan.rows_only:
        parts.append('Rig rows only (Add Assets for Rigs is off, held until '
                     'on the stage): ' + _short(plan.rows_only) + '.')
    if plan.in_scene:
        parts.append("In the shot's scene but not on the stage (re-import the "
                     'shot): ' + _short(plan.in_scene) + '.')
    if plan.already:
        parts.append('Already in: ' + _short(plan.already) + '.')
    if plan.unrigged:
        parts.append('No rig published: ' + _short(plan.unrigged) + '.')
    return ' '.join(parts) or 'Added nothing.'


# --------------------------------------------------------------------------- #
# Instances edits
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CopiesSource:
    """Where an asset's stage copies come from: ``kind`` 'managed' (the
    th::import_assets th::animate manages), 'scene' (the shot's Root),
    'department' (a department's publish, ``department`` its name) or
    'unknown'."""
    kind: str
    department: str = ''


def copies_source(asset: Uri, *, managed=(), scene_assets=(), prim_inputs=()) -> CopiesSource:
    """Where to change ``asset``'s copy count: the managed import's row
    when it imports the asset, else the shot's scene when it lists it,
    else the department whose publish brought it (the last ``inputs``
    entry of its prim metadata naming one)."""
    if asset in set(managed):
        return CopiesSource('managed')
    if asset in set(scene_assets):
        return CopiesSource('scene')
    departments = [
        entry.get('department') for entry in prim_inputs
        if isinstance(entry, dict) and entry.get('department')
    ]
    if departments:
        return CopiesSource('department', departments[-1])
    return CopiesSource('unknown')


def refused_copies_text(asset: Uri, source: CopiesSource) -> str:
    name = asset.segments[-1]
    if source.kind == 'department':
        return (f'{name} comes from the {source.department} department\'s '
                f'publish: change its copies in {source.department}, then '
                'press Add Rigs from Scene here.')
    return (f'{name}: where its stage copies come from is not known here; '
            'change them where it is imported, then press Add Rigs from Scene.')


# --------------------------------------------------------------------------- #
# The stage's fingerprint (cook-time drift warning)
# --------------------------------------------------------------------------- #


def stage_signature(animatable) -> str:
    """A digest of the animatable assets on a stage, ``(asset, names)``
    pairs in any order: equal stages give equal digests. The sync keeps
    it; a cook comparing a different one warns the stage moved on."""
    digest = hashlib.sha1()
    for asset, names in sorted((str(a), tuple(n)) for a, n in animatable):
        digest.update(f'{asset}={",".join(names)}\n'.encode('utf-8'))
    return digest.hexdigest()


DRIFT_MESSAGE = (
    'The shot stage changed since th::import_rigs last synced its rows: '
    'press Add Rigs from Scene on it (Sync Now), or re-import the shot.'
)


@dataclass
class SyncResult:
    """What ``ImportRigs.sync`` did: the plan (None when there was no stage
    to follow, ``why`` says so) and the notes for the comment."""
    plan: SyncPlan | None = None
    notes: list[str] = field(default_factory=list)
    why: str = ''

    @property
    def changed(self) -> bool:
        return self.plan is not None and self.plan.changed
