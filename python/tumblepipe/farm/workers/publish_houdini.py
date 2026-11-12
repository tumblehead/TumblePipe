"""The hython half of the publish worker: load the bundled workfile, bring
its imports up to date and publish the department (and the downstream ones
the config names) through the workfile's own export nodes."""

from pathlib import Path
import json

import hou

from tumblepipe.api import path_str
from tumblepipe.farm import worker
from tumblepipe.farm.env import job_data_dir
from tumblepipe.farm.specs import publish as publish_spec
from tumblepipe.farm.worker import error, headline
from tumblepipe.util.uri import Uri
from tumblepipe.config.timeline import BlockRange
from tumblepipe.config.groups import get_group
from tumblepipe.pipe.paths import (
    get_workfile_context,
)
from tumblepipe.pipe.houdini import nodes as ns
from tumblepipe.pipe.houdini import scene_imports
from tumblepipe.pipe.houdini.lops import (
    export_layer,
    layer_split
)

class NothingToPublish(Exception):
    """The workfile has no export for this entity/department.

    A warning, not a failure: the department is skipped and the task still
    succeeds. Raised only when the workfile's export nodes all resolved and
    none of them is meant for the entity — see :func:`_no_export_node`.
    """

def _no_export_node(entity_uri: Uri, department_name: str, group) -> Exception:
    """Decide what a zero-match export search means.

    ``NothingToPublish`` when the workfile genuinely has nothing for the
    entity: a Multi workfile with no export node of the member's own (the
    member never got a column), or a single-entity workfile with no export
    node at all. Anything else — an export node whose entity does not
    resolve, or export nodes for this entity that the matcher still rejected
    — is a resolution problem, and publishing nothing would hide it, so it
    stays a ``RuntimeError`` that names what the nodes resolved to.
    """
    nodes = list(map(
        export_layer.ExportLayer,
        ns.list_by_node_type('export_layer', 'Lop')
    ))
    resolved = [
        (node.path(), node.get_entity_uri(), node.get_department_name())
        for node in nodes
    ]
    unresolved = any(uri is None or dept is None for _, uri, dept in resolved)
    targets_entity = any(uri == entity_uri for _, uri, _ in resolved)

    if not nodes:
        return NothingToPublish(
            f'The workfile has no export nodes; nothing to publish for '
            f'{entity_uri}/{department_name}'
        )
    if group is not None and not unresolved and not targets_entity:
        return NothingToPublish(
            f'The Multi workfile has no export node for {entity_uri}; '
            f'nothing to publish for {entity_uri}/{department_name}'
        )
    found = ', '.join(
        f'{path} -> {uri}/{dept}' for path, uri, dept in resolved
    )
    return RuntimeError(
        f'No export node found for {entity_uri}/{department_name} — '
        f'refusing to publish an empty version (export nodes: {found})'
    )

def _get_workfile_group():
    """Check if current workfile is a group workfile and return group if so."""
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None:
        return None
    if context.entity_uri.purpose != 'groups':
        return None
    return get_group(context.entity_uri)

def _update():
    # A publish always composes against the newest upstream: every import is
    # forced to its latest available version, overriding any per-node or
    # per-row version pin the artist set while working. Artist pins are a
    # working-context convenience (previewing an older dependency); they must
    # not leak into a published department. Render determinism does not rely on
    # these node pins — the render/stage builder (render_stage.py) rebuilds a
    # fresh graph and never reads them.
    #
    # The node types come from scene_imports.REFRESH_SPECS, the list the
    # scene-load refresh uses. This used to be a hand list of its own that
    # left out standalone import_asset and import_rig nodes, which then
    # published whatever version the workfile was saved with. Unlike the
    # scene-load refresh, a failing node fails the publish.
    for native, spec in scene_imports.find_import_nodes():
        scene_imports.force_newest(native, spec)
        scene_imports.execute(native, spec)
        print(f'Updated {native.path()}')

def _publish_entity(entity_uri: Uri, department_name: str, render_range=None):
    """Publish one shot or asset's department from the loaded workfile: its
    shared layers first, then its layer.

    Every node runs here, headless (``force_local``): without it a node's
    ``execute`` opens the export window, which hython has no UI for.
    ``render_range`` (a ``BlockRange``) is the publish window's range for
    this submit (#76), or None for each node's own Frame Range.
    """

    def _is_for_entity(node) -> bool:
        return (
            node.get_department_name() == department_name
            and node.get_entity_uri() == entity_uri
        )

    split_nodes = filter(_is_for_entity, map(
        layer_split.LayerSplit, ns.list_by_node_type('layer_split', 'Lop')
    ))
    for split_node in split_nodes:
        split_node.execute(force_local=True, render_range=render_range)
        print(f'Exported shared layer: {split_node.path()}')

    export_nodes = list(filter(_is_for_entity, map(
        export_layer.ExportLayer, ns.list_by_node_type('export_layer', 'Lop')
    )))

    # Zero matches is either a workfile with nothing for this entity
    # (skipped) or a matcher/context failure (raised) — see _no_export_node.
    if len(export_nodes) == 0:
        raise _no_export_node(entity_uri, department_name, _get_workfile_group())

    export_node = export_nodes[0]
    export_node.execute(force_local=True, render_range=render_range)
    print(f'Published {export_node.path()}')

def _publish(entity_uri: Uri, department_name: str, render_range=None):
    """Publish the department of a shot, an asset, or each member of a
    Multi. Failures raise so main()'s per-department handler records them
    and the task exits non-zero."""

    def _require_entity(uri: Uri) -> Uri:
        kind = uri.segments[0] if uri.segments else None
        if uri.purpose != 'entity' or kind not in ('shots', 'assets'):
            raise RuntimeError(f'Not a shot or an asset: {uri}')
        return uri

    if entity_uri.purpose != 'groups':
        _publish_entity(_require_entity(entity_uri), department_name, render_range)
        return

    group = get_group(entity_uri)
    if group is None:
        raise RuntimeError(f'Group not found: {entity_uri}')
    # A member with nothing to publish is skipped, not fatal: it must not
    # stop the members after it from publishing.
    skipped = []
    for member_uri in group.members:
        try:
            _publish_entity(_require_entity(member_uri), department_name, render_range)
        except NothingToPublish as e:
            print(f'WARNING: {e}')
            skipped.append(member_uri)
    if skipped and len(skipped) == len(group.members):
        raise NothingToPublish(
            f'No member of {entity_uri} has anything to publish for '
            f'{department_name}'
        )

def _load_workfile(bundled_path: Path) -> None:
    """Load the bundled workfile, raising if it is missing or fails to load."""

    # Workfile MUST be provided by job - no searching
    if not bundled_path.exists():
        raise FileNotFoundError(
            f'Bundled workfile not found: {bundled_path}. The job must '
            'provide a valid workfile via workfile_path config.'
        )

    print(f'Loading bundled workfile: {bundled_path}')
    hou.hipFile.load(
        path_str(bundled_path),
        suppress_save_prompt = True,
        ignore_load_warnings = True
    )
    print(f'Loaded workfile: {bundled_path}')


def main(config) -> int:

    # Print parameters
    headline('Parameters')
    print(f'Config: {json.dumps(config, indent=2)}')

    # The bundled workfile, relative to the job data dir (forwarded as
    # TH_FARM_DATA): hython runs with CWD = the hpm manifest dir.
    bundled_workfile = job_data_dir() / config['workfile_path']

    # Parse the entity from the config
    entity_uri = Uri.parse(config['entity']['uri'])
    department_name = config['entity']['department']

    # Get downstream departments from config
    downstream_departments = publish_spec.downstream_departments(config)

    # The publish window's First/Last for this submit (#76): every export
    # of the job, downstream ones too, writes these frames. None: each node
    # reads its own Frame Range.
    span = publish_spec.frame_range(config)
    render_range = BlockRange(*span) if span is not None else None

    # Build list of all entity URIs + department names to process (main + downstream)
    downstream_entity_pairs = [(entity_uri, department_name)]

    if entity_uri.purpose == 'groups':
        # Groups are expanded in _publish, just process as-is
        for dept_name in downstream_departments:
            downstream_entity_pairs.append((entity_uri, dept_name))
    elif entity_uri.segments[0] in ('shots', 'assets'):
        for dept_name in downstream_departments:
            downstream_entity_pairs.append((entity_uri, dept_name))
    else:
        return error(f'Unknown entity type: {entity_uri.segments[0]}')

    # Process all entities using the same code path
    headline('Processing Entities')
    failures: list[tuple[str, str]] = []  # (department, error)
    skipped: list[tuple[str, str]] = []  # (department, reason)
    for curr_entity_uri, curr_dept_name in downstream_entity_pairs:
        print(f'\nDepartment: {curr_dept_name}')

        # Load workfile - no fallback searching
        _load_workfile(bundled_workfile)

        try:
            # Update the scene with latest imports
            headline('Updating')
            _update()

            # Publish the department. The workfile is not saved afterwards:
            # the export records what was published, and the workfile stays
            # the artist's. Saving a new version here meant one version per
            # published shot of a Multi workfile, each of the scene its job
            # had loaded - which could land on top of an artist's newer save,
            # and a submission could bundle one of them half-written.
            headline('Publishing')
            _publish(curr_entity_uri, curr_dept_name, render_range)

        except NothingToPublish as e:
            print(f'WARNING: {e} — skipping department {curr_dept_name}')
            skipped.append((curr_dept_name, str(e)))
            continue

        except Exception as e:
            import traceback
            print(f'Error processing department {curr_dept_name}: {e}')
            traceback.print_exc()
            failures.append((curr_dept_name, str(e)))
            continue

    # Done
    headline('Done')
    if skipped:
        print(f'WARNING: {len(skipped)} department(s) had nothing to publish:')
        for dept_name, reason in skipped:
            print(f'  - {dept_name}: {reason}')
    if failures:
        print(f'FAILED: {len(failures)} department(s) did not publish:')
        for dept_name, reason in failures:
            print(f'  - {dept_name}: {reason}')
        return 1
    return 0

if __name__ == '__main__':
    hou.exit(worker.run(publish_spec.is_valid_config, main, frames=False))
