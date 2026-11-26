"""th::animate's rig rows follow the shot stage: the Houdini half.

The rules are ``sops.rig_sync`` (plain values, tested without Houdini).
This module reads what they need off the network and writes what they
decide back:

- ``read_stage_assets``: the animatable assets on a stage, their copies'
  prim names and the channel their SOP rig comes from;
- ``keyed_characters``: the characters a Scene Animate of a dive holds
  keys (any stored entry) for, which a sync never drops;
- ``move_keys``: one copy becoming several (X -> X0, X1 ..), X's keys move
  to X0 in every Scene Animate of the dive (``lops.animate_rename``), and
  its Characters and th::layout_keys rows follow; ``blocked_moves`` keeps
  X instead where a Scene Animate keeps something of X0 already;
- ``sync_downstream``: after th::import_shot or th::import_assets
  imports, each th::import_rigs on Follow Stage in a th::animate below it
  syncs, and imports again only when its rows changed; th::animate's
  Characters and th::layout_keys then add rows for new characters (they
  only ever add). While a ``resolver.deferred_refresh`` block is open (the
  scene-open refresh) stages may still compose moved layers, so nothing
  syncs then: ``scene_imports.refresh_scene_imports`` calls ``sync_all``
  once the block has ended;
- ``drift_message``: at cook time only a warning that the stage moved on
  since the last sync (a cook never edits the network);
- ``add_characters`` / ``stage_held`` / ``route_instances``: the other way,
  a rig added puts its asset on the stage through the th::import_assets
  th::animate manages (created just above it, its rows on 'latest'), and
  an Instances edit goes to where the copies come from.

The managed th::import_assets is an ordinary node in the workfile: it
publishes with the animation layer and reaches downstream departments
through the export's context.json, as any th::import_assets does. Nothing
here writes the shot's scene (Root); an Instances edit on a Root asset goes
to Set Scene Copies, which asks first and never writes headless.
"""

from __future__ import annotations

import json
import logging

from tumblepipe.util.uri import Uri
from tumblepipe.pipe.houdini.sops import rig_sync

logger = logging.getLogger(__name__)

ANIMATE_TYPE = 'th::animate'
IMPORT_RIGS_TYPE = 'th::import_rigs'
IMPORT_ASSETS_TYPE = 'th::import_assets'
#: User data on the th::import_assets a th::animate manages: its name.
MANAGED_KEY = 'th_rig_sync_managed'
#: User data on th::import_rigs: the stage digest at its last sync.
SIGNATURE_KEY = 'th_rig_sync_stage'
#: th::animate's toggle: adding a rig puts its asset on the stage.
ADD_ASSETS_PARM = 'add_assets_for_rigs'


def _type_is(node, type_name: str) -> bool:
    name = node.type().name()
    return name == type_name or name.startswith(type_name + '::')


def is_animate(node) -> bool:
    return _type_is(node, ANIMATE_TYPE)


def is_import_rigs(node) -> bool:
    return _type_is(node, IMPORT_RIGS_TYPE)


# --------------------------------------------------------------------------- #
# The stage
# --------------------------------------------------------------------------- #


def rig_status(asset_uri: Uri, preferred: str) -> tuple[str | None, bool]:
    """``(channel, lop_rig)``: the channel ``asset_uri``'s SOP rig comes
    from (``import_rigs.rig_channel``), else None, with ``lop_rig`` True
    when a channel's newest rig is a LOP rig the SOP rig nodes skip."""
    from tumblepipe.config.channels import list_channels
    from tumblepipe.pipe.houdini.sops import import_rig, import_rigs

    channel = import_rigs.rig_channel(asset_uri, preferred)
    if channel is not None:
        return channel, False
    for name in list_channels(asset_uri):
        version = import_rig.latest_rig_version(asset_uri, name)
        if version is not None and import_rig.is_lop_rig(asset_uri, name, version):
            return None, True
    return None, False


def _animatable(stage, untagged=None) -> dict:
    """``{asset_uri: (channel, prim names)}`` of the stage's animatable
    asset prims, in stage order."""
    from tumblepipe.config.channels import CHANNEL_KEY
    from tumblepipe.pipe.houdini import prim_metadata

    found = {}
    for info in prim_metadata.list_animatable_assets(stage.GetPseudoRoot(), untagged):
        metadata = info['metadata']
        try:
            asset_uri = Uri.parse(metadata['uri'])
        except Exception:
            continue
        channel, names = found.setdefault(
            asset_uri, (metadata.get(CHANNEL_KEY) or 'default', []))
        name = info['prim_path'].rsplit('/', 1)[-1]
        if name not in names:
            names.append(name)
    return found


def read_stage_assets(stage, animated=None) -> list[rig_sync.StageAsset]:
    """The animatable assets on ``stage`` as ``rig_sync.StageAsset``, by
    URI: their copies (``import_rig.stage_copies``, else the animatable
    prims' names) and their rig's channel. A prim imported before the
    animatable tag is judged by ``animated`` (``copies_are_animated``), as
    Discover does."""
    from tumblepipe.pipe.houdini.sops import import_rig
    from tumblepipe.pipe.usd.instances import copies_are_animated

    if stage is None:
        return []
    if animated is None:
        animated = copies_are_animated
    verdicts = {}

    def untagged(asset_uri):
        if asset_uri not in verdicts:
            verdicts[asset_uri] = animated(asset_uri)
        return verdicts[asset_uri]

    assets = []
    found = _animatable(stage, untagged)
    for asset_uri in sorted(found, key=str):
        channel, prim_names = found[asset_uri]
        names = import_rig.stage_copies(
            asset_uri, import_rig.stage_children(stage, asset_uri)) or prim_names
        rig_channel, lop_rig = rig_status(asset_uri, channel)
        assets.append(rig_sync.StageAsset(
            asset=asset_uri, channel=rig_channel, names=tuple(names),
            lop_rig=lop_rig))
    return assets


def stage_asset_inputs(stage, asset_uri: Uri) -> list:
    """The ``inputs`` of the metadata of ``asset_uri``'s first prim on
    ``stage`` (where its copies were published from); [] without one."""
    from tumblepipe.pipe.houdini import prim_metadata

    for info in prim_metadata.list_assets(stage.GetPseudoRoot()):
        if info['metadata'].get('uri') == str(asset_uri):
            inputs = info['metadata'].get('inputs') or []
            return inputs if isinstance(inputs, list) else []
    return []


def assets_on_stage(stage) -> set[Uri]:
    """Every asset with a prim on ``stage``, animatable or not."""
    from tumblepipe.pipe.houdini import prim_metadata

    found = set()
    if stage is None:
        return found
    for info in prim_metadata.list_assets(stage.GetPseudoRoot()):
        try:
            found.add(Uri.parse(info['metadata']['uri']))
        except Exception:
            continue
    return found


def signature(stage) -> str:
    """``rig_sync.stage_signature`` of ``stage``'s tagged animatable
    prims (an untagged prim is not judged here: a cook must not read the
    project per asset)."""
    found = _animatable(stage)
    return rig_sync.stage_signature(
        (asset_uri, names) for asset_uri, (_channel, names) in found.items())


def store_signature(native, stage) -> None:
    """Keep ``stage``'s signature on the th::import_rigs ``native``; set
    only when it differs (a set of the value held does not dirty)."""
    value = signature(stage)
    if native.userData(SIGNATURE_KEY) != value:
        native.setUserData(SIGNATURE_KEY, value)


def drift_message(dive, stage) -> str:
    """``rig_sync.DRIFT_MESSAGE`` when a th::import_rigs on Follow Stage in
    ``dive`` last synced against a different stage than ``stage``; ''
    otherwise, and never an error (a cook-time warning only)."""
    try:
        if dive is None or stage is None:
            return ''
        now = None
        for native in dive.children():
            if not is_import_rigs(native) or not follows(native):
                continue
            if native.parm('stage_path').eval():
                continue
            stored = native.userData(SIGNATURE_KEY)
            if not stored:
                continue
            if now is None:
                now = signature(stage)
            if stored != now:
                return rig_sync.DRIFT_MESSAGE
    except Exception:
        logger.debug('rig sync drift check failed', exc_info=True)
    return ''


def follows(native) -> bool:
    """Whether th::import_rigs ``native`` is on Follow Stage (an HDA from
    before Rig Sync has no parm: Manual, as it always was)."""
    parm = native.parm('rig_sync')
    return parm is not None and parm.eval() == rig_sync.FOLLOW


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #


def entry_characters(paths, folder: str) -> list[str]:
    """The characters with an entry (``<folder>/_<name>.char_Base``) among
    packed ``paths``."""
    prefix, suffix = f'{folder}/_', '.char_Base'
    found = []
    for path in paths:
        if path.startswith(prefix) and path.endswith(suffix):
            name = path[len(prefix):-len(suffix)]
            if name and '/' not in name and name not in found:
                found.append(name)
    return found


def keyed_characters(dive) -> set[str]:
    """The characters a Scene Animate in ``dive`` holds an entry for, on
    its base or on a layer: what Animate saved (keys, or a held pose)."""
    from tumblepipe.pipe.houdini.lops import animate_keys

    keyed = set()
    if dive is None:
        return keyed
    try:
        nodes = dive.allSubChildren()
    except Exception:
        return keyed
    for node in nodes:
        try:
            if not animate_keys.is_scene_animate(node):
                continue
            stored = animate_keys._stored(node)
            if stored is None:
                continue
            clip = animate_keys._paths(stored, animate_keys.CLIP)
            keyed.update(entry_characters(clip, animate_keys.CLIP))
            for layer in animate_keys.layer_names(clip):
                folder = f'{animate_keys.CLIP}/{layer}'
                keyed.update(entry_characters(
                    animate_keys._paths(stored, folder), folder))
        except Exception:
            logger.debug('reading keys of %s failed', node.path(), exc_info=True)
    return keyed


def scene_animates(dive) -> list:
    """The Scene Animates anywhere in ``dive``."""
    from tumblepipe.pipe.houdini.lops import animate_keys

    if dive is None:
        return []
    try:
        nodes = dive.allSubChildren()
    except Exception:
        return []
    return [node for node in nodes if animate_keys.is_scene_animate(node)]


def _stored_copy(node):
    """An editable copy of Scene Animate ``node``'s ``animation``; None
    when it keeps nothing."""
    import hou
    from tumblepipe.pipe.houdini.lops import animate_keys

    stored = animate_keys._stored(node)
    return hou.Geometry(stored) if stored is not None else None


def blocked_moves(dive, moves) -> set[str]:
    """The old names of ``moves`` (``(old, new)``) a Scene Animate of
    ``dive`` blocks: it keeps something of ``new`` (scene data Animate
    saved, which ``keyed_characters`` does not count as keys). Reads only."""
    from tumblepipe.pipe.houdini.lops import animate_keys, animate_rename

    blocked = set()
    if not moves:
        return blocked
    for node in scene_animates(dive):
        stored = animate_keys._stored(node)
        if stored is None:
            continue
        for old, new in moves:
            if old not in blocked and animate_rename.holds_character(stored, new):
                blocked.add(old)
    return blocked


def rename_character_rows(animate, dive, old: str, new: str) -> list[str]:
    """Rename character ``old`` to ``new`` on th::animate's Characters
    row and on each th::layout_keys row of ``dive`` (its Character and
    the character it copies onto, and what it copied); a list that names
    ``new`` already is left alone. The paths of the nodes changed."""
    from tumblepipe.pipe.houdini.lops import animate_invoke, animate_rename
    from tumblepipe.pipe.houdini.sops import layout_keys_rows

    changed = []
    if animate is not None and animate.parm(animate_invoke.MULTIPARM) is not None:
        count = animate.parm(animate_invoke.MULTIPARM).eval()
        names = [animate.evalParm(f'{animate_invoke.CHARACTER}{i}').strip()
                 for i in range(1, count + 1)]
        index = animate_rename.renamed_row(names, old, new)
        if index is not None:
            animate.parm(f'{animate_invoke.CHARACTER}{index + 1}').set(new)
            changed.append(animate.path())
    for child in (dive.children() if dive is not None else ()):
        if not _type_is(child, 'th::layout_keys'):
            continue
        node = layout_keys_rows.LayoutKeys(child)
        rows = node.get_rows()
        index = animate_rename.renamed_row(
            [row['target'] or row['character'] for row in rows], old, new)
        if index is None:
            continue
        row = index + 1
        node.parm(f'target{row}').set(new)
        if rows[index]['character'] == old:
            node.parm(f'character{row}').set(new)
        copied = node.copied()
        if old in copied and new not in copied:
            copied[new] = copied.pop(old)
            child.setUserData(layout_keys_rows._COPIED_KEY, json.dumps(copied))
        changed.append(child.path())
    return changed


def move_keys(native, moves) -> list[str]:
    """Apply ``moves`` (``(old, new)``, ``rig_sync.SyncPlan.moved_keys``)
    for th::import_rigs ``native``: in every Scene Animate of its dive,
    move what it keeps of ``old`` to ``new`` (``animate_rename``), then
    rename ``old``'s Characters and th::layout_keys rows. A Scene Animate
    keeping nothing of ``old`` is not written (a no-op sets no parm). Run
    from the sync only (a button, an import), never from a cook: a cook
    never edits the network. Returns the status notes."""
    from tumblepipe.pipe.houdini import shot_start
    from tumblepipe.pipe.houdini.lops import animate_rename

    dive = native.parent()
    animate = shot_start.animate_node(native)
    notes = []
    for old, new in moves:
        written = []
        for node in scene_animates(dive):
            stored = _stored_copy(node)
            if stored is None:
                continue
            if animate_rename.rename_character(stored, old, new) == animate_rename.MOVED:
                node.parm('animation').set(stored)
                written.append(node.name())
        rename_character_rows(animate, dive, old, new)
        if written:
            notes.append(f"{old}'s keys are now {new}'s (in {', '.join(written)}).")
    return notes


def undo_block(label: str):
    """One undo step for what runs in it (``hou.undos.group``); nothing
    where there is no undo (the test stub)."""
    import contextlib
    import hou

    undos = getattr(hou, 'undos', None)
    return undos.group(label) if undos is not None else contextlib.nullcontext()


# --------------------------------------------------------------------------- #
# The network
# --------------------------------------------------------------------------- #


def rigs_in(animate) -> list:
    """The th::import_rigs in ``animate``'s anim_dive."""
    dive = animate.node('anim_dive')
    if dive is None:
        return []
    return [child for child in dive.children() if is_import_rigs(child)]


def animates_below(node, limit: int = 400) -> list:
    """The th::animate nodes ``node`` feeds, through any outputs."""
    found, seen, todo = [], set(), list(node.outputs())
    while todo and len(seen) < limit:
        current = todo.pop(0)
        if current is None or current.path() in seen:
            continue
        seen.add(current.path())
        if is_animate(current):
            found.append(current)
        todo += list(current.outputs())
    return found


def after_rows_changed(animate) -> None:
    """New characters get rows on th::animate's Characters and on any
    th::layout_keys of its dive (both only ever add rows)."""
    from tumblepipe.pipe.houdini.lops import animate_invoke
    from tumblepipe.pipe.houdini.sops.layout_keys_rows import LayoutKeys

    try:
        animate_invoke.discover(animate)
    except Exception:
        logger.debug('Characters rows of %s', animate.path(), exc_info=True)
    dive = animate.node('anim_dive')
    for child in (dive.children() if dive is not None else ()):
        if not _type_is(child, 'th::layout_keys'):
            continue
        try:
            LayoutKeys(child).discover()
        except Exception:
            logger.debug('layout_keys rows of %s', child.path(), exc_info=True)


def sync_node(native) -> rig_sync.SyncResult | None:
    """Sync th::import_rigs ``native`` (on Follow Stage, Stage empty) and
    import again when its rows changed; None when it does not follow."""
    from tumblepipe.pipe.houdini.sops.import_rigs import ImportRigs
    from tumblepipe.pipe.houdini.sops import import_rig

    if not follows(native) or native.parm('stage_path').eval():
        return None
    node = ImportRigs(native)
    result = node.sync()
    if result.changed:
        import_rig.announce_warning(node.execute(sync=False, notes=result.notes))
        from tumblepipe.pipe.houdini import shot_start
        animate = shot_start.animate_node(native)
        if animate is not None:
            after_rows_changed(animate)
    return result


def sync_downstream(node) -> int:
    """Sync every th::import_rigs on Follow Stage in the th::animate nodes
    ``node`` (a LOP that just imported) feeds; how many changed rows.
    Nothing while a deferred resolver refresh is open (``sync_all`` runs
    after it), nothing on a failure (logged: the import itself is done)."""
    from tumblepipe import resolver

    if resolver.is_deferring():
        return 0
    try:
        animates = animates_below(node)
    except Exception:
        logger.debug('finding th::animate below %r', node, exc_info=True)
        return 0
    return _sync_rigs_of(animates)


def _sync_rigs_of(animates) -> int:
    changed = 0
    for animate in animates:
        try:
            rigs = rigs_in(animate)
        except Exception:
            logger.debug('listing the rigs of %r', animate, exc_info=True)
            continue
        for native in rigs:
            try:
                result = sync_node(native)
            except Exception:
                logger.exception('Rig sync of %s failed', native.path())
                continue
            if result is not None and result.changed:
                changed += 1
    return changed


def sync_all() -> int:
    """``sync_node`` for every th::animate's th::import_rigs in the scene;
    how many changed rows. The scene-open refresh's last step."""
    import hou

    category = getattr(hou, 'lopNodeTypeCategory', None)
    if category is None:
        return 0
    animates = []
    for node_type in category().nodeTypes().values():
        if is_animate_type_name(node_type.name()):
            animates += list(node_type.instances())
    return _sync_rigs_of(animates)


def is_animate_type_name(name: str) -> bool:
    return name == ANIMATE_TYPE or name.startswith(ANIMATE_TYPE + '::')


# --------------------------------------------------------------------------- #
# Adding characters
# --------------------------------------------------------------------------- #


def add_assets_enabled(animate) -> bool:
    """th::animate's Add Assets for Rigs (on when it has no such parm)."""
    if animate is None:
        return True
    parm = animate.parm(ADD_ASSETS_PARM)
    return True if parm is None else bool(parm.eval())


def managed_import(animate, create: bool = False):
    """The th::import_assets ``animate`` manages (as an ImportAssets): the
    one wired into its first input and marked as its own. ``create`` makes
    one there when there is none, between the input and th::animate."""
    import hou
    from tumblepipe.pipe.houdini.lops import import_assets

    upstream = animate.input(0)
    if (upstream is not None and _type_is(upstream, IMPORT_ASSETS_TYPE)
            and upstream.userData(MANAGED_KEY)):
        return import_assets.ImportAssets(upstream)
    if not create:
        return None
    node = import_assets.create(animate.parent(), f'{animate.name()}_characters')
    native = node.native()
    native.setUserData(MANAGED_KEY, animate.name())
    if upstream is not None:
        native.setInput(0, upstream)
        native.setPosition((upstream.position() + animate.position()) * 0.5)
    else:
        native.setPosition(animate.position() + hou.Vector2(0, 1))
    animate.setInput(0, native)
    native.setComment('Managed by th::animate: characters added with Add Characters')
    native.setGenericFlag(hou.nodeFlag.DisplayComment, True)
    return node


def managed_assets(animate) -> list[Uri]:
    """The assets the managed th::import_assets of ``animate`` lists."""
    node = managed_import(animate) if animate is not None else None
    if node is None:
        return []
    found = []
    for index in range(1, node.parm('asset_imports').eval() + 1):
        raw = node.parm(f'entity{index}').eval()
        if raw:
            try:
                found.append(Uri.parse(raw))
            except Exception:
                continue
    return found


def scene_assets_for(native) -> set[Uri]:
    """The assets the shot's scene (Root) lists for the shot the
    th::import_shot above ``native``'s th::animate imports."""
    from tumblepipe.pipe.houdini.sops.scene_copies import shot_import_for
    from tumblepipe.pipe.scene_build import root_assets, shot_root

    try:
        shot_import = shot_import_for(native)
        shot_uri = shot_import.get_entity_uri() if shot_import is not None else None
        scene_uri = shot_root(shot_uri) if shot_uri is not None else None
        if scene_uri is None:
            return set()
        return {Uri.parse(entry.asset) for entry in root_assets(scene_uri)}
    except Exception:
        logger.debug('reading the scene of %s', native.path(), exc_info=True)
        return set()


def rigged(asset_uri: Uri) -> bool:
    """Whether ``asset_uri`` has a SOP rig in any channel."""
    from tumblepipe.pipe.houdini.sops import import_rigs

    return import_rigs.rig_channel(asset_uri, 'default') is not None


def rigged_assets() -> list[str]:
    """The project's assets with a SOP rig, for the Add Characters picker."""
    from tumblepipe.api import api
    from tumblepipe.config.entities import is_terminal_entity
    from tumblepipe.pipe.paths import cached_version_listings

    with api.config.coherent(), cached_version_listings():
        return [
            str(uri) for uri in api.config.list_entity_uris(
                filter=Uri.parse('entity:/assets'), closure=True)
            if is_terminal_entity(api.config, uri) and rigged(uri)
        ]


def add_characters(native, picked) -> str:
    """Add Characters on th::import_rigs ``native`` (Follow Stage): sync,
    then for each pick (``rig_sync.plan_asset_add``) put its asset on the
    stage through the managed th::import_assets (which syncs the rows when
    it imports), un-skip a skipped row, or with Add Assets for Rigs off
    give it a row only (held until it is on the stage). Returns the status
    line."""
    from tumblepipe.pipe.houdini import shot_start
    from tumblepipe.pipe.houdini.sops.import_rigs import ImportRigs

    node = ImportRigs(native)
    node.sync()
    animate = shot_start.animate_node(native)
    stage, _why = node.stage_or_none()
    rows, _clean = node.read_rows()
    picked = [Uri.parse(str(uri)) for uri in picked]
    plan = rig_sync.plan_asset_add(
        picked,
        on_stage=assets_on_stage(stage),
        scene_assets=scene_assets_for(native),
        managed=managed_assets(animate),
        rows=[row.asset for row in rows],
        skipped=[row.asset for row in rows if row.skip],
        rigged=rigged,
        add_assets=add_assets_enabled(animate) and animate is not None,
    )
    if plan.unskip:
        for index in range(1, node.parm('rig_imports').eval() + 1):
            raw = node.parm(f'entity{index}').eval()
            if raw and Uri.parse(raw) in plan.unskip and node.parm(f'skip{index}') is not None:
                node.parm(f'skip{index}').set(0)
    if plan.rows_only:
        node.add_picked_rigs([str(uri) for uri in plan.rows_only])
    needs_import = bool(plan.unskip or plan.rows_only)
    if plan.imports:
        needs_import = not _import_managed(animate, node, plan.imports) or needs_import
    if needs_import:
        node.execute()
    return f"{native.name()}: {rig_sync.add_text(plan)}"


def _import_managed(animate, node, assets) -> bool:
    """Add ``assets`` to ``animate``'s managed th::import_assets (made when
    missing) and import it; True when its ``sync_downstream`` then changed
    and imported ``node``'s rows (else the caller imports them)."""
    managed = managed_import(animate, create=True)
    for asset_uri in assets:
        managed.add_asset_entry(asset_uri)
    before = node.read_rows()
    managed.execute()
    return node.read_rows() != before


def stage_held(native) -> str:
    """Add Held to Stage: the assets of the rows held off the stage go onto
    it through the managed th::import_assets (Add Characters for them)."""
    from tumblepipe.pipe.houdini.sops.import_rigs import ImportRigs

    node = ImportRigs(native)
    result = node.sync()
    held = [row.asset for row in (result.plan.held if result.plan else ())]
    if not held:
        return f'{native.name()}: no rows are held off the stage.'
    from tumblepipe.pipe.houdini import shot_start
    animate = shot_start.animate_node(native)
    if animate is None:
        return (f'{native.name()}: not inside a th::animate, so there is no '
                'stage to add them to.')
    stage, _why = node.stage_or_none()
    plan = rig_sync.plan_asset_add(
        held, on_stage=assets_on_stage(stage),
        scene_assets=scene_assets_for(native),
        managed=managed_assets(animate), rigged=rigged, add_assets=True)
    if plan.imports and not _import_managed(animate, node, plan.imports):
        node.execute()
    return f"{native.name()}: {rig_sync.add_text(plan)}"


def route_instances(native, index: int) -> str:
    """An Instances edit on row ``index`` of th::import_rigs ``native`` on
    Follow Stage goes where the asset's stage copies come from
    (``rig_sync.copies_source``): the managed th::import_assets row (then
    it imports, and the rows follow), the shot's scene (Set Scene Copies:
    a confirm, never headless), or nowhere (a department's publish: the
    edit is undone and the status line says where to change it). A row
    that does not follow the stage keeps the edit. Returns the status
    line ('' when there is nothing to say)."""
    from tumblepipe.pipe.houdini import shot_start
    from tumblepipe.pipe.houdini.sops import scene_copies
    from tumblepipe.pipe.houdini.sops.import_rigs import ImportRigs

    node = ImportRigs(native)
    wanted = native.parm(f'instances{index}').eval()
    raw = native.parm(f'entity{index}').eval()
    names = rig_sync.parse_names(native.parm(f'names{index}').eval())
    if not raw or not names:
        return ''
    asset_uri = Uri.parse(raw)
    # The row says what the stage has until the source changes.
    native.parm(f'instances{index}').set(len(names))
    if wanted == len(names):
        return ''
    animate = shot_start.animate_node(native)
    stage, _why = node.stage_or_none()
    source = rig_sync.copies_source(
        asset_uri,
        managed=managed_assets(animate),
        scene_assets=scene_assets_for(native),
        prim_inputs=stage_asset_inputs(stage, asset_uri) if stage is not None else (),
    )
    name = asset_uri.segments[-1]
    if source.kind == 'managed':
        managed = managed_import(animate)
        for row in range(1, managed.parm('asset_imports').eval() + 1):
            if managed.parm(f'entity{row}').eval() == str(asset_uri):
                managed.parm(f'instances{row}').set(wanted)
        before = node.read_rows()
        managed.execute()
        if node.read_rows() == before:
            node.execute()
        return f'{native.name()}: {name} now has {wanted} copies on the stage.'
    if source.kind == 'scene':
        scene_copies._run(
            native, scene_copies.shot_import_for(native),
            [(asset_uri, wanted)], node.discover)
        return ''
    return f'{native.name()}: {rig_sync.refused_copies_text(asset_uri, source)}'
