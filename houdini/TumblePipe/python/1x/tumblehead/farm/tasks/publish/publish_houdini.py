from pathlib import Path
import json

import hou

from tumblehead.api import path_str, default_client
from tumblehead.util.io import load_json
from tumblehead.util.uri import Uri
from tumblehead.config.groups import get_group
from tumblehead.pipe.paths import (
    next_hip_file_path,
    get_workfile_context,
    Context
)
from tumblehead.pipe.houdini import nodes as ns
from tumblehead.pipe.houdini.lops import (
    import_shot,
    import_assets,
    import_layer,
    export_layer,
    layer_split
)
from tumblehead.pipe.houdini.sops import (
    import_rigs
)
from tumblehead.pipe.houdini.cops import (
    build_comp
)
from tumblehead.pipe.context import (
    save_context,
    save_entity_context,
)

api = default_client()

# Helpers
def _headline(title):
    print(f' {title} '.center(80, '='))

def _error(msg):
    print(f'ERROR: {msg}')
    return 1

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

    # Find import shot nodes
    import_shot_nodes = list(map(
        import_shot.ImportShot,
        ns.list_by_node_type('import_shot', 'Lop')
    ))

    # Find build comp nodes
    build_comp_nodes = list(map(
        build_comp.BuildComp,
        ns.list_by_node_type('build_comp', 'Cop')
    ))

    # Find import asset nodes
    import_assets_nodes = list(map(
        import_assets.ImportAssets,
        ns.list_by_node_type('import_assets', 'Lop')
    ))

    # Find the import layer nodes (unified)
    import_layer_nodes = list(map(
        import_layer.ImportLayer,
        ns.list_by_node_type('import_layer', 'Lop')
    ))

    # Find the import rigs nodes
    import_rigs_nodes = list(map(
        import_rigs.ImportRigs,
        ns.list_by_node_type('import_rigs', 'Sop')
    ))

    # Import latest shot stages
    for import_shot_node in import_shot_nodes:
        if not import_shot_node.is_valid(): continue
        import_shot_node.execute()
        print(f'Updated {import_shot_node.path()}')

    # Import latest comp builds
    for build_comp_node in build_comp_nodes:
        if not build_comp_node.is_valid(): continue
        build_comp_node.update()
        print(f'Updated {build_comp_node.path()}')

    # Import latest assets
    for import_assets_node in import_assets_nodes:
        if not import_assets_node.is_valid(): continue
        import_assets_node.execute()
        print(f'Updated {import_assets_node.path()}')

    # Import latest layers (unified)
    for import_node in import_layer_nodes:
        if not import_node.is_valid(): continue
        import_node.set_version_name('current')
        import_node.execute()
        print(f'Updated {import_node.path()}')

    # Import latest rigs
    for import_node in import_rigs_nodes:
        if not import_node.is_valid(): continue
        import_node.execute()
        print(f'Updated {import_node.path()}')

def _publish(entity_uri: Uri, department_name: str):

    def _publish_asset(
        asset_uri: Uri,
        department_name: str
        ):

        # Check if we're in a group workfile
        group = _get_workfile_group()

        if group is not None:
            # Group workfile: use membership-based matching
            member_uris = set(group.members)

            def _is_split_correct(node):
                if node.get_department_name() != department_name: return False
                entity_uri = node.get_entity_uri()
                if entity_uri is None: return False
                return entity_uri in member_uris and entity_uri == asset_uri

            def _is_export_correct(node):
                if node.get_department_name() != department_name: return False
                entity_uri = node.get_entity_uri()
                if entity_uri is None: return False
                return entity_uri in member_uris and entity_uri == asset_uri
        else:
            # Single entity workfile: use exact matching (current behavior)
            def _is_split_correct(node):
                if node.get_department_name() != department_name: return False
                if node.get_entity_uri() != asset_uri: return False
                return True

            def _is_export_correct(node):
                if node.get_department_name() != department_name: return False
                if node.get_entity_uri() != asset_uri: return False
                return True

        # Execute layer_split nodes first (shared content)
        split_nodes = list(filter(
            _is_split_correct,
            map(
                layer_split.LayerSplit,
                ns.list_by_node_type('layer_split', 'Lop')
            )
        ))
        for split_node in split_nodes:
            split_node.execute()
            print(f'Exported shared layer: {split_node.path()}')

        # Find the export nodes
        export_nodes = list(filter(
            _is_export_correct,
            map(
                export_layer.ExportLayer,
                ns.list_by_node_type('export_layer', 'Lop')
            )
        ))

        # Execute the export node
        if len(export_nodes) == 0:
            print(f'WARNING: No export node found for {asset_uri}/{department_name}')
            print(f'Skipping export for this department')
            return

        asset_export_node = export_nodes[0]
        asset_export_node.execute(force_local=True)
        print(f'Published {asset_export_node.path()}')

    def _publish_shot(
        shot_uri: Uri,
        department_name: str
        ):

        # Check if we're in a group workfile
        group = _get_workfile_group()

        if group is not None:
            # Group workfile: use membership-based matching
            member_uris = set(group.members)

            def _is_shot_split_correct(node):
                if node.get_department_name() != department_name: return False
                entity_uri = node.get_entity_uri()
                if entity_uri is None: return False
                return entity_uri in member_uris and entity_uri == shot_uri

            def _is_shot_export_correct(node):
                if node.get_department_name() != department_name: return False
                entity_uri = node.get_entity_uri()
                if entity_uri is None: return False
                return entity_uri in member_uris and entity_uri == shot_uri
        else:
            # Single entity workfile: use exact matching (current behavior)
            def _is_shot_split_correct(node):
                if node.get_department_name() != department_name: return False
                if node.get_entity_uri() != shot_uri: return False
                return True

            def _is_shot_export_correct(node):
                if node.get_department_name() != department_name: return False
                if node.get_entity_uri() != shot_uri: return False
                return True

        # Execute layer_split nodes first (shared content)
        shot_split_nodes = list(filter(
            _is_shot_split_correct,
            map(
                layer_split.LayerSplit,
                ns.list_by_node_type('layer_split', 'Lop')
            )
        ))
        for split_node in shot_split_nodes:
            split_node.execute()
            print(f'Exported shared layer: {split_node.path()}')

        # Find the export nodes
        shot_export_nodes = list(filter(
            _is_shot_export_correct,
            map(
                export_layer.ExportLayer,
                ns.list_by_node_type('export_layer', 'Lop')
            )
        ))

        # Execute the export node
        if len(shot_export_nodes) == 0:
            print(f'WARNING: No export node found for {shot_uri}/{department_name}')
            print(f'Skipping export for this department')
            return

        shot_export_node = shot_export_nodes[0]
        shot_export_node.execute(force_local=True)
        print(f'Published {shot_export_node.path()}')

    # Get entity type from URI
    if entity_uri.purpose == 'groups':
        # Expand group to member shots/assets
        group = get_group(entity_uri)
        if group is None:
            return _error(f'Group not found: {entity_uri}')
        for member_uri in group.members:
            if member_uri.segments[0] == 'shots':
                _publish_shot(
                    shot_uri = member_uri,
                    department_name = department_name
                )
            elif member_uri.segments[0] == 'assets':
                _publish_asset(
                    asset_uri = member_uri,
                    department_name = department_name
                )
    elif entity_uri.segments[0] == 'assets':
        _publish_asset(
            asset_uri = entity_uri,
            department_name = department_name
        )
    elif entity_uri.segments[0] == 'shots':
        _publish_shot(
            shot_uri = entity_uri,
            department_name = department_name
        )
    else:
        return _error(f'Invalid entity type: {entity_uri.segments[0]}')

def _save(entity_uri: Uri, department_name: str):
    # Get next hip file path using Uri
    hip_file_path = next_hip_file_path(entity_uri, department_name)

    # Save the hip file
    hou.hipFile.save(path_str(hip_file_path))

    # Extract version name from file path (e.g., "entity_animation_v0005.hip" -> "v0005")
    version_name = hip_file_path.stem.rsplit('_', 1)[-1]

    # Construct context from known parameters
    context = Context(
        entity_uri=entity_uri,
        department_name=department_name,
        version_name=version_name
    )

    # Save context metadata (user, timestamp, version info)
    save_context(hip_file_path.parent, None, context)
    save_entity_context(hip_file_path.parent, context)

def _load_workfile(bundled_path: Path, force_reload: bool = True) -> bool:
    """Load bundled workfile. Returns True if successful."""

    # Workfile MUST be provided by job - no searching
    if not bundled_path.exists():
        print(f'ERROR: Bundled workfile not found: {bundled_path}')
        print(f'The job must provide a valid workfile via workfile_path config')
        return False

    print(f'Loading bundled workfile: {bundled_path}')

    # Check if we're already in the correct workfile
    current_file = hou.hipFile.path()
    if not force_reload and current_file == path_str(bundled_path):
        print(f'Already in correct workfile: {bundled_path}')
        return True

    try:
        hou.hipFile.load(
            path_str(bundled_path),
            suppress_save_prompt = True,
            ignore_load_warnings = True
        )
        print(f'Loaded workfile: {bundled_path}')
        return True
    except Exception as e:
        print(f'ERROR: Failed to load workfile {bundled_path}: {e}')
        return False


def main(config) -> int:

    # Print parameters
    _headline('Parameters')
    print(f'Config: {json.dumps(config, indent=2)}')

    # Read bundled workfile path from config - REQUIRED
    if 'workfile_path' not in config:
        print('ERROR: workfile_path not found in config')
        print('The job must provide a bundled workfile')
        return 1

    bundled_workfile = Path(config['workfile_path'])
    # Resolve relative to current directory (job data path)
    if not bundled_workfile.is_absolute():
        bundled_workfile = Path.cwd() / bundled_workfile

    # Parse the entity from the config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']

    # Get downstream departments from config
    downstream_departments = config['tasks']['publish'].get('downstream_departments', [])

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
        return _error(f'Unknown entity type: {entity_uri.segments[0]}')

    # Process all entities using the same code path
    _headline('Processing Entities')
    for curr_entity_uri, curr_dept_name in downstream_entity_pairs:
        print(f'\nDepartment: {curr_dept_name}')

        # Load workfile - no fallback searching
        if not _load_workfile(bundled_workfile, force_reload = True):
            print(f'ERROR: Failed to load bundled workfile for {curr_entity_uri}')
            return 1

        try:
            # Update the scene with latest imports
            _headline('Updating')
            _update()

            # Publish the department
            _headline('Publishing')
            _publish(curr_entity_uri, curr_dept_name)

            # Save new version
            _headline('Saving')
            _save(curr_entity_uri, curr_dept_name)

        except Exception as e:
            print(f'Error processing department {curr_dept_name}: {e}')
            continue

    # Done
    _headline('Done')
    return 0

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('script_path', type=str)
    parser.add_argument('config_path', type=str)
    args = parser.parse_args()

    # Get the config file path
    config_path = Path(args.config_path)
    if not config_path.exists():
        return _error(f'Config file not found: {config_path}')
    config = load_json(config_path)
    
    # Run the main function
    return main(config)

if __name__ == '__main__':
    hou.exit(cli())