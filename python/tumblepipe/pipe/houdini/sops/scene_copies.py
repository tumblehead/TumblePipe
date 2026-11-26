"""Set Scene Copies on th::import_rigs / th::import_rig: write the rig
import's copy count into the shot's scene, so the stage holds the copies the
rig animates.

th::import_rig names its characters ``<asset>`` (one copy) or ``<asset>0``
.. ``<asset><N-1>`` and th::animate lands each on the shot stage's prim of
that name. Only stage copies carry lookdev, and import_shot's Duplicate LOP
makes them from the instance count of the asset in the shot's scene (its
Root, edited in the Entity Editor). With Instances set by hand on the rig
import (David: Roommate 3 against a scene listing 1), the extra characters
landed at the origin with no lookdev. Discover reads the counts from the
stage; nothing wrote a count chosen on the node back to the scene.

The action (one click, after a confirm naming the shot, its Root, each
asset and its old -> new count, since every department reads the Root):

1. writes the count into the Root the shot uses, through
   ``config.scene`` (the store the Entity Editor's Apply writes): the
   Root's own entry for the asset when it has one, else a new own entry
   overriding the parent Root's (the pipeline takes a Root's own entry
   over a parent's), never a parent Root other shots inherit;
2. stages it the way the Entity Editor's Apply does
   (``pipe.root_staging.sync_roots`` for the shot: the Root is exported,
   the shot gets a root layer or build only if it needs one);
3. re-imports the th::import_shot that feeds the th::animate, so the stage
   holds the new copies;
4. runs Discover on th::import_rigs (Import on a th::import_rig on its
   own), so the characters land on the stage copies.

The pure half (which assets, which counts, what the Root becomes, what the
dialog says) takes plain values and is tested without Houdini
(``tests/test_scene_copies.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

from tumblepipe.util.uri import Uri
from tumblepipe.config import scene as scene_config
from tumblepipe.config.scene import AssetEntry
from tumblepipe.pipe.houdini.sops.import_rig import copies_message

TITLE = 'Set Scene Copies'
#: The button's label on th::import_rigs and th::import_rig.
BUTTON = 'Set Scene Copies'


def wanted_copies(asset_uri: Uri, instances: int, on_stage: list[str]) -> int | None:
    """The count the shot's scene should give ``asset_uri`` for a rig import
    of ``instances`` copies to land on stage copies (``on_stage``, from
    ``import_rig.stage_copies``): ``instances`` when the import makes more
    characters than the stage has copies; None when it makes the stage's
    copies (no warning), none at all, or fewer than the stage has (Discover
    then imports one per stage copy; shrinking the scene is not this
    action's to offer)."""
    if instances <= 0 or not copies_message(asset_uri, instances, on_stage):
        return None
    if instances <= len(on_stage):
        return None
    return instances


def _same_asset(stored: str, asset_uri: Uri) -> bool:
    try:
        return Uri.parse(stored) == asset_uri
    except Exception:
        return False


def scene_entry(own, inherited, asset_uri: Uri):
    """``(entry, parent)``: the entry the shot's Root gives ``asset_uri``,
    as the pipeline picks it (``scene_build.root_assets``): the Root's own
    first entry, else the closest parent Root's (``parent`` its URI);
    ``(None, None)`` when the Root has none. ``own`` is the Root's
    ``AssetEntry`` list, ``inherited`` ``(AssetEntry, parent_uri)`` pairs,
    closest first (``config.scene.get_inherited_assets``)."""
    for entry in own:
        if _same_asset(entry.asset, asset_uri):
            return entry, None
    for entry, parent in inherited:
        if _same_asset(entry.asset, asset_uri):
            return entry, parent
    return None, None


@dataclass(frozen=True)
class CopiesChange:
    """One asset's count in the shot's Root: ``old`` as the Root gives it
    now, ``new`` the rig import's. ``inherited_from`` is the parent Root
    the current entry comes from (None: the Root's own)."""
    asset: Uri
    old: int
    new: int
    channel: str = scene_config.DEFAULT_CHANNEL
    inherited_from: Uri | None = None

    @property
    def writes(self) -> bool:
        return self.old != self.new


def plan_copies(own, inherited, wanted):
    """``(changes, refused)`` for setting the shot's Root to ``wanted``
    (``(asset_uri, count)`` pairs, the first per asset counting).

    A change per asset the Root lists (its own entry or a parent's), old
    and new count, ``writes`` False when it already says so (the stage is
    then only stale: a re-import and Discover fix it). ``refused`` is
    ``(asset_uri, why)`` per asset the Root does not list at all: its stage
    copies, if any, come from elsewhere, and adding an asset to a shot is
    the Entity Editor's decision, not a count fix.
    """
    changes, refused, seen = [], [], set()
    for asset_uri, count in wanted:
        if asset_uri in seen:
            continue
        seen.add(asset_uri)
        entry, parent = scene_entry(own, inherited, asset_uri)
        if entry is None:
            refused.append((
                asset_uri,
                (f"{asset_uri.segments[-1]} is not in the shot's scene: add it "
                 'in the Entity Editor (its stage copies come from elsewhere).'),
            ))
            continue
        changes.append(CopiesChange(
            asset=asset_uri, old=entry.instances, new=count,
            channel=entry.channel, inherited_from=parent,
        ))
    return changes, refused


def apply_copies(own, changes) -> list[AssetEntry]:
    """The Root's own asset list with ``changes`` written: an asset's first
    own entry takes the new count (its channel and place kept); an asset
    only a parent Root lists gets an own entry at the end, on the parent's
    channel, which the pipeline takes over the parent's. Every other entry
    is kept as it is."""
    result = list(own)
    for change in changes:
        if not change.writes:
            continue
        for index, entry in enumerate(result):
            if _same_asset(entry.asset, change.asset):
                result[index] = AssetEntry(entry.asset, change.new, entry.channel)
                break
        else:
            result.append(AssetEntry(str(change.asset), change.new, change.channel))
    return result


def _path(uri: Uri) -> str:
    """A Root or shot as the Entity Editor names it: ``outdoor/kitchen``,
    ``010/020``."""
    segments = list(uri.segments)
    if uri.purpose == 'entity' and segments[:1] == ['shots']:
        segments = segments[1:]
    return '/'.join(segments)


def copies_title(changes) -> str:
    """The question in a line: 'Set the shot's scene to 3 copies of
    Roommate?', or the assets' names for several."""
    writes = [change for change in changes if change.writes]
    if len(writes) == 1:
        change = writes[0]
        noun = 'copy' if change.new == 1 else 'copies'
        return (f"Set the shot's scene to {change.new} {noun} of "
                f'{change.asset.segments[-1]}?')
    names = ', '.join(change.asset.segments[-1] for change in writes)
    return f"Set the shot's scene copies of {names}?"


def copies_question(shot_uri: Uri, scene_uri: Uri, changes, other_shots=()) -> str:
    """The confirm dialog's text: the shot, its Root, each asset's old ->
    new count (and the parent Root an override replaces), who else reads
    the Root, and what happens after. Only ``changes`` that write."""
    writes = [change for change in changes if change.writes]
    lines = [
        copies_title(writes),
        '',
        f'Shot: {_path(shot_uri)}',
        f'Scene (Root): {_path(scene_uri)}',
    ]
    for change in writes:
        line = f'    {change.asset.segments[-1]}: {change.old} -> {change.new}'
        if change.inherited_from is not None:
            line += (f' (from parent Root {_path(change.inherited_from)}; '
                     f'{_path(scene_uri)} gets its own entry)')
        lines.append(line)
    lines.append('')
    others = [uri for uri in other_shots if uri != shot_uri]
    if others:
        shown = ', '.join(_path(uri) for uri in others[:5])
        if len(others) > 5:
            shown += f' and {len(others) - 5} more'
        lines.append(
            f'The Root is also the scene of {len(others)} other '
            f"shot{'s' if len(others) != 1 else ''} ({shown}): "
            'they get these copies too.')
    lines.append(
        'Every department that imports the shot reads its scene. After the '
        'write the Root is staged, the shot is re-imported and Add Rigs from '
        'Scene maps the characters onto the stage copies.')
    return '\n'.join(lines)


# --------------------------------------------------------------------------- #
# The project database
# --------------------------------------------------------------------------- #


def write_copies_in(store, scene_uri: Uri, changes) -> bool:
    """Write ``changes`` into the Root ``scene_uri`` of ``store``, read again
    now (not the list the dialog was built from); False when that changes
    nothing. Through ``config.scene.set_scene_assets_in``, the call the
    Entity Editor's Root edits make."""
    scene = scene_config.get_scene_by_uri_in(store, scene_uri)
    if scene is None:
        raise ValueError(f'Root {_path(scene_uri)} is no longer in the project')
    assets = apply_copies(scene.assets, changes)
    if assets == list(scene.assets):
        return False
    scene_config.set_scene_assets_in(store, scene_uri, assets)
    return True


def root_users_in(store, scene_uri: Uri) -> list[Uri]:
    """The shots whose scene is ``scene_uri`` (set on them or inherited)."""
    from tumblepipe.config.entities import is_terminal_entity

    users = []
    for shot_uri in store.list_entity_uris(Uri.parse('entity:/shots'), closure=True):
        if not is_terminal_entity(store, shot_uri):
            continue
        ref, _ = scene_config.get_inherited_scene_ref_in(store, shot_uri)
        if ref == scene_uri:
            users.append(shot_uri)
    return users


class CopiesRefused(Exception):
    """Nothing to write, or nowhere to write it; the message says why."""


def set_scene_copies(shot_uri: Uri, wanted, *, store, confirm, restage,
                     reimport, rediscover) -> str:
    """Set the shot's Root to ``wanted`` (``(asset_uri, count)``), then bring
    the stage and the rig imports in line. Returns the status line.

    ``confirm(title, text)`` asks before any write; ``restage(shot_uri)``
    stages the Root (its summary line); ``reimport()`` re-imports the shot
    stage (a note, '' when done cleanly, None when it could not);
    ``rediscover()`` runs Discover or Import (the warnings left). Raises
    ``CopiesRefused`` when there is nothing to do or nowhere to write it,
    before anything changes.
    """
    if not wanted:
        raise CopiesRefused(
            'No rig import makes more characters than the shot stage has '
            'copies: there is no count to write. Where the stage has more '
            'copies than the rig import, press Add Rigs from Scene.')
    scene_uri, _ = scene_config.get_inherited_scene_ref_in(store, shot_uri)
    if scene_uri is None:
        raise CopiesRefused(
            f'Shot {_path(shot_uri)} has no scene (Root): give it one in the '
            'Entity Editor.')
    scene = scene_config.get_scene_by_uri_in(store, scene_uri)
    if scene is None:
        raise CopiesRefused(
            f'The Root of shot {_path(shot_uri)}, {_path(scene_uri)}, is not '
            'in the project: set it in the Entity Editor.')
    changes, refused = plan_copies(
        scene.assets, scene_config.get_inherited_assets_in(store, scene_uri), wanted)
    if not changes:
        raise CopiesRefused('\n'.join(why for _uri, why in refused))
    writes = [change for change in changes if change.writes]
    notes = []
    if writes:
        question = copies_question(
            shot_uri, scene_uri, writes, root_users_in(store, scene_uri))
        if refused:
            question += '\n\nNot changed:\n' + '\n'.join(why for _uri, why in refused)
        if not confirm(TITLE, question):
            return 'Set Scene Copies: cancelled, nothing written.'
        write_copies_in(store, scene_uri, writes)
        notes.append(f'{_path(scene_uri)}: ' + ', '.join(
            f'{c.asset.segments[-1]} {c.old} -> {c.new}' for c in writes))
    else:
        notes.append(f'{_path(scene_uri)} already lists ' + ', '.join(
            f'{c.new} {c.asset.segments[-1]}' for c in changes))
    notes += [why for _uri, why in refused]
    staged = restage(shot_uri)
    if staged:
        notes.append(staged)
    reimported = reimport()
    if reimported is None:
        notes.append(
            'no th::import_shot feeds the th::animate: re-import the shot '
            'stage, then press Add Rigs from Scene')
        return 'Set Scene Copies: ' + '; '.join(notes)
    if reimported:
        notes.append(reimported)
    left = rediscover()
    if left:
        notes.append(f'still: {left}')
    return 'Set Scene Copies: ' + '; '.join(notes)


# --------------------------------------------------------------------------- #
# Houdini
# --------------------------------------------------------------------------- #


def _confirm(title: str, text: str) -> bool:
    import hou

    if not hou.isUIAvailable():
        # No one to ask: every department reads the Root, so a headless
        # press writes nothing (call set_scene_copies with a confirm instead).
        print(f'{title}: no UI to confirm the scene write, nothing written.')
        return False
    choice = hou.ui.displayMessage(
        text, buttons=('Set Scene', 'Cancel'), default_choice=1,
        close_choice=1, title=title, severity=hou.severityType.Warning,
    )
    return choice == 0


def _restage(shot_uri: Uri) -> str:
    """The Entity Editor Apply's staging, for this shot."""
    import logging
    from tumblepipe.pipe.root_staging import sync_roots

    result = sync_roots([shot_uri])
    for uri, why in result.failures:
        logging.getLogger(__name__).warning('Staging %s: %s', uri, why)
    return result.summary()


def shot_import_for(native, stage_path: str = ''):
    """The th::import_shot (as an ImportShot) that makes the stage a rig
    import reads: up the inputs of the th::animate ``native`` sits in, or
    of the LOP ``stage_path`` names (it, when it is one); None."""
    from tumblepipe.pipe.houdini import shot_start
    from tumblepipe.pipe.houdini.lops.import_shot import ImportShot

    if stage_path:
        lop_node = native.node(stage_path)
        if lop_node is None:
            return None
        if 'import_shot' in lop_node.type().name():
            return ImportShot(lop_node)
        return shot_start._import_shot_above(lop_node)
    animate = shot_start.animate_node(native)
    if animate is None:
        return None
    return shot_start._import_shot_above(animate)


def _reimport(import_shot):
    """Re-import ``import_shot`` so the stage holds the Root's new copies;
    a note when its pinned version keeps the old ones, None without one."""
    import hou
    from tumblepipe.pipe.houdini import playbar

    if import_shot is None:
        return None
    with playbar.update_mode(hou.updateMode.Manual):
        import_shot.execute()
    version = import_shot.get_version_name()
    if version not in ('latest', 'current'):
        return (f'{import_shot.path()} is pinned to {version}, which keeps '
                'the copies it was built with: set its Version to latest')
    return ''


def _rig_rows_wanted(stage, rows):
    from tumblepipe.pipe.houdini.sops import import_rig

    wanted = []
    for asset_uri, instances in rows:
        on_stage = import_rig.stage_copies(
            asset_uri, import_rig.stage_children(stage, asset_uri))
        count = wanted_copies(asset_uri, instances, on_stage)
        if count is not None:
            wanted.append((asset_uri, count))
    return wanted


def _run(native, shot_import, wanted, rediscover) -> None:
    """Shared tail of both buttons: the shot, the write, the status line."""
    import hou
    from tumblepipe.api import api
    from tumblepipe.pipe.houdini import nodes as ns
    from tumblepipe.pipe.houdini.lops import animate_ghost

    if shot_import is None:
        raise ns.NodeError(
            f'{native.path()}: no th::import_shot feeds the stage, so there is '
            'no shot whose scene to set.')
    shot_uri = shot_import.get_entity_uri()
    if shot_uri is None:
        raise ns.NodeError(
            f'{shot_import.path()}: the th::import_shot names no shot.')
    try:
        status = set_scene_copies(
            shot_uri, wanted, store=api.config, confirm=_confirm,
            restage=_restage, reimport=lambda: _reimport(shot_import),
            rediscover=rediscover,
        )
    except CopiesRefused as refused:
        raise ns.NodeError(f'{native.path()}: {refused}') from None
    print(status)
    if hou.isUIAvailable():
        hou.ui.setStatusMessage(status, severity=(
            hou.severityType.Warning if 'still:' in status
            else hou.severityType.Message))
    animate_ghost.refresh_ghost(native)


def from_import_rigs(kwargs) -> None:
    """th::import_rigs' Set Scene Copies button: every row whose import
    makes more characters than the stage has copies."""
    from tumblepipe.pipe.houdini.sops.import_rigs import ImportRigs

    native = kwargs['node']
    node = ImportRigs(native)
    stage = node.get_stage()
    rows = [(asset_uri, instances)
            for asset_uri, _channel, _version, instances in node.get_row_imports()]
    _run(native, shot_import_for(native, node.parm('stage_path').eval()),
         _rig_rows_wanted(stage, rows), node.discover)


def _import_rigs_around(native):
    """The th::import_rigs whose dive holds the th::import_rig ``native``."""
    ancestor = native.parent()
    while ancestor is not None:
        name = ancestor.type().name().lower().split('::')
        if len(name) >= 2 and name[0] == 'th' and name[1] == 'import_rigs':
            return ancestor
        ancestor = ancestor.parent()
    return None


def from_import_rig(kwargs) -> None:
    """th::import_rig's Set Scene Copies button: its own asset and count.
    Afterwards it imports again; inside a th::import_rigs, that node's
    Discover runs instead, once this callback has returned (Discover
    rebuilds the dive this node sits in)."""
    import hou
    from tumblepipe.pipe.houdini import nodes as ns
    from tumblepipe.pipe.houdini.sops import import_rig
    from tumblepipe.pipe.houdini.sops.import_rigs import ImportRigs

    native = kwargs['node']
    node = import_rig.ImportRig(native)
    stage = import_rig.shot_stage(native)
    if stage is None:
        raise ns.NodeError(
            f'{native.path()}: not inside a th::animate, so there is no '
            'shot stage to match.')
    asset_uri = node.require_entity_uri()
    outer = _import_rigs_around(native)

    def rediscover():
        if outer is None:
            return node.execute() or ''
        path = outer.path()

        def _discover():
            found = hou.node(path)
            if found is not None:
                import_rig.announce_warning(ImportRigs(found).discover())
        if hou.isUIAvailable():
            import hdefereval
            hdefereval.executeDeferred(_discover)
        else:
            _discover()
        return ''

    _run(native, shot_import_for(native),
         _rig_rows_wanted(stage, [(asset_uri, node.get_instances())]),
         rediscover)
