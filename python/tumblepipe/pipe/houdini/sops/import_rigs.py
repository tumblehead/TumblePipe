from pathlib import Path

import hou

from tumblepipe.api import api
from tumblepipe.util.uri import Uri
from tumblepipe.config.entities import is_terminal_entity
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.asset_rows import AssetRows
from tumblepipe.pipe.houdini.sops import import_rig, rig_sync
from tumblepipe.pipe.houdini.lops import animate_ghost
from tumblepipe.config.channels import CHANNEL_KEY, list_channels
from tumblepipe.pipe.houdini import prim_metadata
from tumblepipe.pipe.paths import get_rig_export_path
from tumblepipe.pipe.usd.instances import copies_are_animated
from tumblepipe.pipe.houdini.nodes import button


def has_rig_export(asset_uri: Uri, channel_name: str = 'default') -> bool:
    """Whether ``asset_uri`` has published a rig in ``channel_name``. A shot
    imports props and sets that never get one, so the animation template
    adds a row only for an asset that has.

    A SOP rig: when the newest rig is a LOP rig (``import_rig.is_lop_rig``)
    the SOP rig nodes cannot load it, so the asset counts as having none
    here and is brought in with th::import_layer instead."""
    version_name = import_rig.latest_rig_version(asset_uri, channel_name)
    if version_name is None:
        return False
    return not import_rig.is_lop_rig(asset_uri, channel_name, version_name)


def rig_channel(asset_uri: Uri, preferred: str) -> str | None:
    """The channel to import ``asset_uri``'s rig from: ``preferred`` (the
    channel the shot imports the asset in) when it has a rig, else its
    first channel that has one; None when no channel has."""
    channel_names = list_channels(asset_uri)
    if preferred in channel_names:
        channel_names = [preferred] + [
            name for name in channel_names if name != preferred
        ]
    return next(
        (name for name in channel_names if has_rig_export(asset_uri, name)),
        None
    )


def discover_rig_imports(stage, animated=None):
    """``(rows, unrigged)`` for the rigs ``stage`` animates.

    Rows are ``(asset_uri, channel, 'latest', instances)``, one per asset
    whose prims are tagged animatable (prim_metadata.ANIMATABLE_KEY), with
    a row instance per tagged prim. ``unrigged`` names the animatable
    assets with no rig published, which get no row. A prim imported
    before the tag existed is judged by ``animated`` — the rule the import
    nodes stamp with, copies_are_animated by default — until its import
    runs again.
    """
    if stage is None:
        return [], []
    if animated is None:
        animated = copies_are_animated
    verdicts = {}

    def untagged(asset_uri):
        if asset_uri not in verdicts:
            verdicts[asset_uri] = animated(asset_uri)
        return verdicts[asset_uri]

    counts, channels = {}, {}
    for asset_info in prim_metadata.list_animatable_assets(
        stage.GetPseudoRoot(), untagged
    ):
        metadata = asset_info['metadata']
        asset_uri = Uri.parse(metadata['uri'])
        counts[asset_uri] = counts.get(asset_uri, 0) + 1
        channels.setdefault(asset_uri, metadata[CHANNEL_KEY])

    rows, unrigged = [], []
    for asset_uri in sorted(counts, key=str):
        channel = rig_channel(asset_uri, channels[asset_uri])
        if channel is None:
            unrigged.append(asset_uri)
            continue
        rows.append((asset_uri, channel, 'latest', counts[asset_uri]))
    return rows, unrigged


def keep_row_choices(rows, current):
    """Discovered ``rows`` with the artist's choices kept: an asset in
    ``current`` ({asset_uri: (channel, version)}, the rows already on the
    node) keeps that channel and version; Instances always come from the
    stage."""
    return [
        (asset_uri, *current.get(asset_uri, (channel, version)), instances)
        for asset_uri, channel, version, instances in rows
    ]


def plan_rig_rows(existing, picked, channel_of):
    """What Browse Rigs... adds to a node whose rows hold ``existing``, for
    the picks ``picked`` (asset URIs, the picker's order):
    ``(rows, skipped, unrigged)``.

    ``rows`` are ``(asset_uri, channel)``, one per new pick that has a rig,
    in the channel ``channel_of(asset_uri)`` names; ``skipped`` the picks a
    row already holds, or picked twice; ``unrigged`` the new picks
    ``channel_of`` finds no rig for (None), which get no row. All three
    keep ``picked``'s order; a pick that is not an entity URI is in none.
    """
    from tumblepipe.pipe import entity_picker as ep

    added, skipped = ep.plan_rows(existing, picked)
    rows, unrigged = [], []
    for raw in added:
        asset_uri = Uri.parse(raw)
        channel = channel_of(asset_uri)
        if channel is None:
            unrigged.append(asset_uri)
        else:
            rows.append((asset_uri, channel))
    return rows, skipped, unrigged


def rigs_added_text(added: int, skipped: int, unrigged) -> str:
    """The status line after Browse Rigs...: 'Added 2 rigs, skipped 1
    already in the list. No rig published: Tree.'"""
    from tumblepipe.pipe import entity_picker as ep

    if added == 0 and skipped == 0 and unrigged:
        text = 'Added nothing.'
    else:
        text = ep.rows_added_text(added, skipped, noun='rig')
    if unrigged:
        text += ' No rig published: ' + ', '.join(
            uri.segments[-1] for uri in unrigged
        ) + '.'
    return text


def split_skipped(rig_imports, skipped):
    """``(kept, skipped_assets)``: ``rig_imports`` without the rows whose
    asset is in ``skipped`` (a row's Skip: a rigged asset this animation
    leaves alone), and those assets, in row order."""
    skipped = set(skipped)
    kept = [row for row in rig_imports if row[0] not in skipped]
    return kept, [row[0] for row in rig_imports if row[0] in skipped]


def row_parm_writes(current: dict, rows) -> list[tuple[str, object]]:
    """The ``(parm name, value)`` sets that make a node whose row parms are
    ``current`` ({parm name: value}, ``rig_imports`` the count) hold
    ``rows`` (``rig_sync.RigRow``): only the parms whose value differs, the
    count first. A set of the value a parm holds still marks the scene
    changed (QA ledger 51), so rows that are already right set nothing."""
    writes = []
    if current.get('rig_imports') != len(rows):
        writes.append(('rig_imports', len(rows)))
    for index, row in enumerate(rows, start=1):
        for name, value in (
            ('entity', str(row.asset)),
            ('channel', row.channel),
            ('version', row.version),
            ('instances', row.instances),
            ('names', rig_sync.format_names(row.names)),
            ('skip', int(row.skip)),
        ):
            key = f'{name}{index}'
            # A row the count adds starts at the parm defaults.
            fresh = index > current.get('rig_imports', 0)
            held = _ROW_DEFAULTS[name] if fresh else current.get(key)
            if held != value:
                writes.append((key, value))
    return writes


def _notes_text(notes) -> str:
    """``notes`` as node comment lines, each wrapped to a readable width."""
    import textwrap

    lines = []
    for note in notes:
        if note:
            lines += textwrap.wrap(note, 72)
    return ''.join(f'\n{line}' for line in lines)


#: A new row's parm values (the DialogScript defaults).
_ROW_DEFAULTS = {
    'entity': '', 'channel': 'default', 'version': 'latest', 'instances': 1,
    'names': '', 'skip': 0,
}


def split_unpublished(rig_imports):
    """``(to_import, unpublished)``: the rows with instances to import, and
    the assets of the 'latest' rows that have no rig published.

    Those rows are skipped and named in the node comment, not imported as
    a dead character: the animation template before migration v16 added a
    row for every asset in the shot (Magnus). A pinned version still has to
    exist, so its row stays in and fails loudly on import.
    """
    to_import, unpublished = [], []
    for row in rig_imports:
        asset_uri, channel, version, instances = row
        if instances <= 0:
            continue
        if version == 'latest' and not has_rig_export(asset_uri, channel):
            unpublished.append(asset_uri)
        else:
            to_import.append(row)
    return to_import, unpublished


class ImportRigs(AssetRows):
    """One rig import per multiparm row (see AssetRows): the row's asset's
    published rig, in its channel, at its version."""

    COUNT_PARM = 'rig_imports'
    VERSION_TOKENS = ('latest',)

    def _offers(self, asset_uri: Uri) -> bool:
        """Only real assets: the template's empty categories list as leaves
        too, and have no rig to import."""
        return is_terminal_entity(api.config, asset_uri)

    def _version_folder(self, asset_uri: Uri, channel_name: str) -> Path:
        return get_rig_export_path(asset_uri, channel_name)

    def _latest_version(self, asset_uri: Uri, channel_name: str) -> str | None:
        return import_rig.latest_rig_version(asset_uri, channel_name)

    def set_rig_imports(self, rig_imports: list[tuple[Uri, str, str, int]]):
        """Set rig imports from list of (asset_uri, channel, version, instances) tuples."""
        self.parm('rig_imports').set(0)
        for asset_uri, channel, version, instances in rig_imports:
            if instances == 0: continue
            index = self.parm('rig_imports').eval() + 1
            self.parm('rig_imports').set(index)
            self.set_entity_uri(index, asset_uri)
            self.set_channel_name(index, channel)
            self.set_version_name(index, version)
            self.set_instances(index, instances)

    def add_picked_rigs(self, picked, channel_of=None) -> tuple[str, int]:
        """Append a row for each of ``picked`` (asset URIs, the picker's
        order) that no row holds yet and that has a rig (``plan_rig_rows``;
        ``channel_of`` defaults to the asset's 'default' channel, else its
        first rigged one), on 'latest' with one instance. Rows already
        there are not touched. Returns the status line and how many rows
        were added."""
        if channel_of is None:
            def channel_of(asset_uri):
                return rig_channel(asset_uri, 'default')
        count = self.parm('rig_imports').eval()
        existing = [self.parm(f'entity{i}').eval() for i in range(1, count + 1)]
        rows, skipped, unrigged = plan_rig_rows(existing, picked, channel_of)
        for asset_uri, channel in rows:
            index = self.parm('rig_imports').eval() + 1
            self.parm('rig_imports').set(index)
            # Written directly, not through set_entity_uri: the URI is
            # already a picked asset, and that check reads every row.
            self.parm(f'entity{index}').set(str(asset_uri))
            self.parm(f'channel{index}').set(channel)
            self.parm(f'version{index}').set('latest')
            self.parm(f'instances{index}').set(1)
        message = rigs_added_text(len(rows), len(skipped), unrigged)
        return f"{self.path().rsplit('/', 1)[-1]}: {message}", len(rows)

    # Rig Sync (sops.rig_sync)

    def follows_stage(self) -> bool:
        """Rig Sync is Follow Stage (an HDA without the parm: Manual)."""
        parm = self.parm('rig_sync')
        return parm is not None and parm.eval() == rig_sync.FOLLOW

    def _row_parm(self, name: str, index: int, default=None):
        parm = self.parm(f'{name}{index}')
        return default if parm is None else parm.eval()

    def read_rows(self) -> tuple[list, bool]:
        """``(rows, clean)``: the rows as ``rig_sync.RigRow``, and False
        when a row names no asset or one that is not a URI (left out; a
        sync then rewrites the rows without it)."""
        rows, clean = [], True
        for index in range(1, self.parm('rig_imports').eval() + 1):
            raw = self.parm(f'entity{index}').eval()
            try:
                asset_uri = Uri.parse(raw) if raw else None
            except Exception:
                asset_uri = None
            if asset_uri is None:
                clean = False
                continue
            rows.append(rig_sync.RigRow(
                asset=asset_uri,
                channel=self.parm(f'channel{index}').eval(),
                version=self.parm(f'version{index}').eval() or 'latest',
                instances=self.parm(f'instances{index}').eval(),
                names=rig_sync.parse_names(self._row_parm('names', index, '')),
                skip=bool(self._row_parm('skip', index, 0)),
            ))
        return rows, clean

    def _current_row_parms(self) -> dict:
        count = self.parm('rig_imports').eval()
        current = {'rig_imports': count}
        for index in range(1, count + 1):
            for name in _ROW_DEFAULTS:
                parm = self.parm(f'{name}{index}')
                if parm is not None:
                    current[f'{name}{index}'] = parm.eval()
        return current

    def write_rows(self, rows) -> int:
        """Make the rows ``rows`` (``rig_sync.RigRow``), setting only the
        parms that differ (``row_parm_writes``); how many were set."""
        writes = row_parm_writes(self._current_row_parms(), rows)
        for name, value in writes:
            parm = self.parm(name)
            if parm is not None:
                parm.set(value)
        return len(writes)

    def stage_or_none(self):
        """``(stage, why)``: the stage the rows follow (as ``get_stage``),
        or None and why there is none; never raises."""
        stage_path = self.parm('stage_path').eval()
        if len(stage_path) == 0:
            stage = import_rig.shot_stage(self.native())
            if stage is None:
                return None, 'not inside a th::animate and Stage is empty'
            return stage, ''
        lop_node = self.native().node(stage_path)
        if lop_node is None or not isinstance(lop_node, hou.LopNode):
            return None, f"Stage '{stage_path}' is not a LOP node"
        try:
            stage = lop_node.stage()
        except hou.Error as exc:
            return None, f"Stage '{stage_path}' does not cook: {exc}"
        if stage is None:
            return None, f"Stage '{stage_path}' has no stage"
        return stage, ''

    def sync(self) -> rig_sync.SyncResult:
        """Make the rows follow the stage (``rig_sync.plan_stage_sync``),
        setting parms only when they change. A node from before Rig Sync
        (``sync_version`` 0) migrates on its first sync: rows its stage no
        longer has are held, never dropped, and the comment says so. A
        character animated alone (X) whose asset now has several copies
        gets its keys moved to X0 in the dive's Scene Animates, with its
        Characters and th::layout_keys rows, in one undo step with the
        rows (``animate_sync.move_keys``); never onto keys X0 has.
        Nothing is imported here (``execute`` imports)."""
        from dataclasses import replace
        from tumblepipe.pipe.houdini import animate_sync

        result = rig_sync.SyncResult()
        stage, why = self.stage_or_none()
        if stage is None:
            result.why = why
            result.notes.append(f'Follow Stage: nothing to follow ({why}).')
            return result
        current, clean = self.read_rows()
        dive = self.native().parent()
        assets = animate_sync.read_stage_assets(stage)
        keyed = animate_sync.keyed_characters(dive)
        plan = rig_sync.plan_stage_sync(assets, current, keyed)
        if plan.moved_keys:
            # A Scene Animate keeping Animate data of the new name blocks
            # the move: the old character stays beside it.
            hold = animate_sync.blocked_moves(dive, plan.moved_keys)
            if hold:
                plan = rig_sync.plan_stage_sync(assets, current, keyed, hold=hold)
        if not clean:
            plan = replace(plan, changed=True)
        if plan.moved_keys:
            # The rows and the keys they name move together: one undo step.
            moves = ', '.join(f'{old} -> {new}' for old, new in plan.moved_keys)
            with animate_sync.undo_block(f'Rig Sync: move keys {moves}'):
                self.write_rows(plan.rows)
                result.notes += animate_sync.move_keys(self.native(), plan.moved_keys)
                self._synced(current, plan, stage, result)
        else:
            if plan.changed:
                self.write_rows(plan.rows)
            self._synced(current, plan, stage, result)
        result.plan = plan
        result.notes += list(plan.notes)
        return result

    def _synced(self, current, plan, stage, result) -> None:
        """After a sync's rows: the one-time migration note and
        ``sync_version``, and the stage's signature (each set only when it
        differs)."""
        from tumblepipe.pipe.houdini import animate_sync

        version_parm = self.parm('sync_version')
        if version_parm is not None and version_parm.eval() < rig_sync.SYNC_VERSION:
            note = rig_sync.migration_note(current, plan)
            if note:
                result.notes.append(note)
            version_parm.set(rig_sync.SYNC_VERSION)
        animate_sync.store_signature(self.native(), stage)

    def get_stage(self):
        """The stage Discover reads: the Stage parm's LOP, or with it
        empty, the stage fed into the th::animate this node sits in."""
        stage_path = self.parm('stage_path').eval()
        if len(stage_path) == 0:
            stage = import_rig.shot_stage(self.native())
            if stage is None:
                raise self._error(
                    'not inside a th::animate, so there is no shot stage to '
                    'discover rigs in. Set Stage to a LOP node.'
                )
            return stage
        lop_node = self.native().node(stage_path)
        if lop_node is None or not isinstance(lop_node, hou.LopNode):
            raise self._error(f"Stage '{stage_path}' is not a LOP node")
        stage = lop_node.stage()
        if stage is None:
            raise self._error(f"Stage '{stage_path}' has no stage: {lop_node.errors()}")
        return stage

    def discover(self):
        """Add Rigs from Scene. On Follow Stage it is Sync Now: ``execute``
        (the rows follow the stage, then Import). On Manual, rows from the
        stage's animatable prims (discover_rig_imports), then Import: a
        row the stage still animates keeps its channel and version; the
        rest of the rows go."""
        if self.follows_stage():
            self.get_stage()  # no stage to follow raises, as Manual does
            return self.execute()
        rows, unrigged = discover_rig_imports(self.get_stage())
        current = {}
        for index in range(1, self.parm('rig_imports').eval() + 1):
            asset_uri_raw = self.parm(f'entity{index}').eval()
            if len(asset_uri_raw) == 0:
                continue
            current[Uri.parse(asset_uri_raw)] = (
                self.parm(f'channel{index}').eval(),
                self.parm(f'version{index}').eval() or 'latest',
            )
        self.set_rig_imports(keep_row_choices(rows, current))
        return self.execute(unrigged)

    def execute(self, unrigged=(), sync=True, notes=()):
        """Import every row but the skipped ones. On Follow Stage the rows
        follow the stage first (``sync``), unless ``sync`` is False or a
        deferred resolver refresh is open (the scene-open refresh syncs
        once it has ended, ``animate_sync.sync_all``). ``unrigged``:
        animatable assets Discover found with no rig; ``notes``: lines
        for the comment.

        Returns the rows' copies warnings (``import_rig.copies_warning``:
        characters the shot stage lacks, or stage copies no character is
        named for), one line each, '' when there are none; the node comment
        has them too."""
        from tumblepipe import resolver

        notes = list(notes)
        if sync and self.follows_stage() and not resolver.is_deferring():
            result = self.sync()
            notes += result.notes
        with self._rows_read_once():
            return self._execute(unrigged, notes)

    def _execute(self, unrigged, notes=()):

        # Update labels for all entries
        count = self.parm('rig_imports').eval()
        for index in range(1, count + 1):
            self._update_labels(index)

        # Clear scene
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')
        ns.clear_network(dive_node, output_node)

        # Parameters
        row_imports = self.get_row_imports()
        rows, _clean = self.read_rows()
        names_of = {row.asset: row.names for row in rows}
        row_imports, skipped = split_skipped(
            row_imports, [row.asset for row in rows if row.skip])
        active_imports, unpublished = split_unpublished(row_imports)
        unpublished_note = ''.join(
            f'\n{label}: ' + ', '.join(uri.segments[-1] for uri in uris)
            for label, uris in (
                ('No rig published', unpublished),
                ('Animatable, no rig published', unrigged),
                ('Skipped', skipped),
            )
            if uris
        ) + _notes_text(notes)

        # Check if any rigs to import
        if not active_imports:
            ns.set_node_comment(
                context, f"Bypassed: No rigs configured{unpublished_note}")
            context.bypass(True)
            return ''
        # Undo a bypass left by an earlier empty run, or the node reports
        # "Imported" while passing nothing through.
        ns.clear_auto_bypass(context)

        # Build asset nodes
        prev_node = None
        warnings = []
        for asset_uri, channel, version, instances in active_imports:

            # Create node name from URI segments (include channel for uniqueness)
            uri_name = '_'.join(asset_uri.segments[1:])
            node_name = f'{uri_name}_{channel}_import' if channel != 'default' else f'{uri_name}_import'

            # Import the rig
            rig_node = import_rig.create(dive_node, node_name)
            rig_node.set_entity_uri(asset_uri)
            rig_node.parm('channel').set(channel)
            rig_node.set_instances(instances)
            # Characters named after the stage copies (Follow Stage)
            if names_of.get(asset_uri):
                rig_node.set_names(names_of[asset_uri])
            # Honour the selected version; 'latest' auto-resolves on execute.
            if version == 'latest':
                rig_node.latest()
            else:
                rig_node.set_version_name(version)
            warning = rig_node.execute()
            if warning and warning not in warnings:
                warnings.append(warning)

            # Connect the rig
            if prev_node is not None:
                ns.connect_next(prev_node, rig_node.native())
            prev_node = rig_node.native()

        # Connect to output
        if prev_node is not None:
            ns.connect_next(prev_node, output_node)

        # Layout the nodes
        dive_node.layoutChildren()

        # Set success comment
        rig_count = len(active_imports)
        ns.set_node_comment(
            context,
            f"Imported: {rig_count} rig{'s' if rig_count != 1 else ''}"
            f"{unpublished_note}{import_rig.comment_note(warnings)}",
        )
        return '\n'.join(warnings)

def create(scene, name):
    return ns.create_node(scene, name, ImportRigs, 'import_rigs', 'Sop')

@button('Import Rigs')
def execute(kwargs):
    raw_node = kwargs['node']
    node = ImportRigs(raw_node)
    try:
        import_rig.announce_warning(node.execute())
    finally:
        animate_ghost.refresh_ghost(raw_node)

@button('Add Rigs from Scene')
def discover(kwargs):
    """The Add Rigs from Scene button (parm ``discover``): rows from the
    stage's animatable prims."""
    node = ImportRigs(kwargs['node'])
    try:
        import_rig.announce_warning(node.discover())
    finally:
        animate_ghost.refresh_ghost(kwargs['node'])

@button('Set Scene Copies')
def set_scene_copies(kwargs):
    """The Set Scene Copies button: write the rows' copy counts into the
    shot's scene (after a confirm), re-import the shot and Discover
    (``sops.scene_copies``)."""
    from tumblepipe.pipe.houdini.sops import scene_copies
    scene_copies.from_import_rigs(kwargs)

@button('Import Rigs')
def browse(kwargs):
    """The Browse Rigs... button beside Add Rigs from Scene (and, through
    ``add_characters_from_animate``, th::animate's Add Characters...):
    pick several assets in the picker.

    Follow Stage: the picker lists the assets with a SOP rig; a pick not on
    the stage is added to it through the th::import_assets th::animate
    manages (Add Assets for Rigs on), a skipped row is un-skipped, and the
    rows follow (``animate_sync.add_characters``). Manual: add a row for
    each pick that has a rig and no row yet, then Import (as the Import
    button does). Cancel changes nothing; the status line says what was
    added and skipped, and names picks with no rig published."""
    from tumblepipe.pipe.houdini import animate_sync
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    native = kwargs['node']
    node = ImportRigs(native)
    follow = node.follows_stage()
    # Follow Stage lists only rigged assets: a pick is a character to add.
    listed = {'entity_uris': animate_sync.rigged_assets()} if follow else {}
    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='assets',
        include_from_context=False,
        title="Add Characters" if follow else "Add Rigs",
        parent=hou.qt.mainWindow(),
        multi_select=True,
        **listed,
    )
    if not dialog.exec_():
        return
    picked = dialog.get_selected_uris()
    if not picked:
        return
    if follow:
        try:
            message = add_characters_to(native, picked)
            hou.ui.setStatusMessage(message, severity=hou.severityType.Message)
        finally:
            animate_ghost.refresh_ghost(native)
        return
    # One undo step for all the rows added.
    with hou.undos.group(f'Add rigs to {node.path()}'):
        message, _added = node.add_picked_rigs(picked)
    hou.ui.setStatusMessage(message, severity=hou.severityType.Message)
    execute(kwargs)


def add_characters_to(native, picked) -> str:
    """Add Characters for ``picked`` on th::import_rigs ``native`` on
    Follow Stage (``animate_sync.add_characters``), as one undo step;
    returns the status line."""
    from tumblepipe.pipe.houdini import animate_sync

    with hou.undos.group(f'Add characters to {native.path()}'):
        return animate_sync.add_characters(native, picked)


@button('Import Rigs')
def stage_held(kwargs):
    """Add Held to Stage: the rows kept though their asset is not on the
    stage get it added, through the managed th::import_assets."""
    from tumblepipe.pipe.houdini import animate_sync

    native = kwargs['node']
    try:
        with hou.undos.group(f'Add held rigs to the stage of {native.path()}'):
            message = animate_sync.stage_held(native)
        hou.ui.setStatusMessage(message, severity=hou.severityType.Message)
    finally:
        animate_ghost.refresh_ghost(native)


@button('Add Characters')
def add_characters_from_animate(kwargs):
    """th::animate's Add Characters... button: Browse Rigs... on the
    th::import_rigs in its dive (the first one on Follow Stage, else the
    first)."""
    from tumblepipe.pipe.houdini import animate_sync

    animate = kwargs['node']
    rigs = animate_sync.rigs_in(animate)
    if not rigs:
        raise ns.NodeError(
            f'{animate.path()}: its anim_dive has no th::import_rigs to add '
            'characters to.')
    native = next((n for n in rigs if animate_sync.follows(n)), rigs[0])
    browse({'node': native})


@button('Import Rigs')
def instances_changed(kwargs):
    """A row's Instances edited by hand. On Follow Stage the count goes to
    where the asset's stage copies come from (``animate_sync.
    route_instances``); on Manual it stays on the row, as before."""
    from tumblepipe.pipe.houdini import animate_sync

    native = kwargs['node']
    if not animate_sync.follows(native):
        return
    index = kwargs['parm'].multiParmInstanceIndices()[0]
    message = animate_sync.route_instances(native, index)
    if message:
        import_rig.announce_warning(message)
    animate_ghost.refresh_ghost(native)


@button('Import Rigs')
def select(kwargs):
    """A row's Browse... button: pick that row's asset, then Import (as
    the Import button does). Cancel changes nothing."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    index = kwargs['parm'].multiParmInstanceIndices()[0]
    node = ImportRigs(kwargs['node'])
    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='assets',
        include_from_context=False,
        current_selection=node.parm(f'entity{index}').eval(),
        title="Select Asset",
        parent=hou.qt.mainWindow()
    )
    if not dialog.exec_():
        return
    selected_uri = dialog.get_selected_uri()
    if not selected_uri:
        return
    node.parm(f'entity{index}').set(selected_uri)
    execute(kwargs)
