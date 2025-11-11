from pathlib import Path
import logging
import shutil
import json
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import default_client
from tumblehead.config import BlockRange
from tumblehead.util.io import load_json, store_json
from tumblehead.apps.deadline import log_progress
from tumblehead.pipe.paths import get_render_context, ShotEntity

api = default_client()

def _should_sync_aov(aov_name: str) -> bool:
    """Check if an AOV should be synced to edit"""
    if aov_name == 'beauty':
        return True
    if aov_name.startswith('objid_'):
        return True
    if aov_name.startswith('holdout_'):
        return True
    return False

def _error(msg):
    print(f'Error: {msg}')
    return 1

def _headline(title):
    print(f' {title} '.center(80, '='))

def _get_frame_path(frames_path, frame_index):
    assert '####' in frames_path.name, 'Frame path does not contain ####'
    frame_name = str(frame_index).zfill(4)
    return (
        frames_path.parent /
        frames_path.name.replace('####', frame_name)
    )

def _manifest_set(data, value, *path_steps):
    """Set a value in nested manifest dict, creating intermediate dicts as needed"""
    for step in path_steps[:-1]:
        data = data.setdefault(step, dict())
    data[path_steps[-1]] = value

def _manifest_get(data, *path_steps):
    """Get a value from nested manifest dict, returning None if path doesn't exist"""
    for step in path_steps:
        if data is None:
            return None
        data = data.get(step)
    return data

def main(config, render_range):

    # Parameters
    sequence_name = config['sequence_name']
    shot_name = config['shot_name']
    purpose = config.get('purpose', 'render')

    # Get render context and resolve latest AOVs at runtime
    _headline('Resolving latest AOVs across all departments')
    render_context = get_render_context(sequence_name, shot_name, purpose=purpose)

    # Get both shot and render department priorities
    shot_departments = api.config.list_shot_department_names()
    render_departments = api.config.list_render_department_names()

    print(f'Shot department priority (low->high): {shot_departments}')
    print(f'Render department priority (low->high): {render_departments}')

    # Resolve using both hierarchies - no minimum filters, always pick the best available
    latest_aovs = render_context.resolve_latest_aovs(
        shot_departments,
        render_departments,
        min_shot_department=None,
        min_render_department=None,
        aov_filter=_should_sync_aov
    )

    # Debug: Show what was resolved
    if latest_aovs:
        print('\nResolved AOVs:')
        for layer_name, layer_aovs in latest_aovs.items():
            for aov_name, (render_dept, version, aov, shot_dept) in layer_aovs.items():
                print(f'  {layer_name}/{aov_name}: shot_dept={shot_dept}, render_dept={render_dept}, version={version}')
        print()

    if not latest_aovs:
        print('No AOVs found to sync')
        return 0

    # Load manifest
    manifest_path = api.storage.resolve('edit:/') / 'manifest.json'
    manifest_data = load_json(manifest_path) or {}
    shot_key = f'{sequence_name}/{shot_name}'

    # Debug: Show what's in the manifest
    if shot_key in manifest_data:
        print('Manifest contents:')
        for layer_name, layer_data in manifest_data[shot_key].items():
            for aov_name, data in layer_data.items():
                shot_dept, render_dept, version = data
                print(f'  {layer_name}/{aov_name}: shot_dept={shot_dept}, render_dept={render_dept}, version={version}')
        print()

    # Department priorities for manifest checking (matching resolve_latest_aovs hierarchy)
    shot_department_priority = api.config.list_shot_department_names()
    render_department_priority = api.config.list_render_department_names()

    # Sync each resolved layer/AOV combination
    synced_count = 0
    skipped_count = 0

    for layer_name, layer_aovs in latest_aovs.items():
        for aov_name, (render_department_name, aov_version_name, aov, shot_department_name) in layer_aovs.items():

            # Check manifest to see if we should skip
            prev_data = _manifest_get(manifest_data, shot_key, layer_name, aov_name)
            if prev_data is not None:
                prev_shot_dept, prev_render_dept, prev_version = prev_data

                # Hierarchical comparison: shot dept > render dept > version
                prev_shot_idx = shot_department_priority.index(prev_shot_dept)
                curr_shot_idx = shot_department_priority.index(shot_department_name)

                # Compare shot department priority first
                if prev_shot_idx > curr_shot_idx:
                    print(f'Skip {layer_name}/{aov_name}: already at higher shot department {prev_shot_dept}/{prev_render_dept}/{prev_version}')
                    skipped_count += 1
                    continue
                elif prev_shot_idx < curr_shot_idx:
                    # Current has higher shot department, always update
                    print(f'Update {layer_name}/{aov_name}: upgrading from {prev_shot_dept}/{prev_render_dept}/{prev_version} to {shot_department_name}/{render_department_name}/{aov_version_name}')
                    pass
                else:
                    # Same shot department - compare render department priority
                    prev_render_idx = render_department_priority.index(prev_render_dept)
                    curr_render_idx = render_department_priority.index(render_department_name)

                    if prev_render_idx > curr_render_idx:
                        print(f'Skip {layer_name}/{aov_name}: already at higher render department {prev_render_dept}/{prev_version}')
                        skipped_count += 1
                        continue
                    elif prev_render_idx < curr_render_idx:
                        # Current has higher render department, always update
                        print(f'Update {layer_name}/{aov_name}: upgrading from {prev_render_dept}/{prev_version} to {render_department_name}/{aov_version_name}')
                        pass
                    else:
                        # Same shot and render department - compare version
                        prev_version_code = api.naming.get_version_code(prev_version)
                        curr_version_code = api.naming.get_version_code(aov_version_name)
                        if prev_version_code >= curr_version_code:
                            print(f'Skip {layer_name}/{aov_name}: already at version {prev_render_dept}/{prev_version}')
                            skipped_count += 1
                            continue
                        else:
                            print(f'Update {layer_name}/{aov_name}: upgrading from {prev_render_dept}/{prev_version} to {render_department_name}/{aov_version_name}')
                            pass

            # Sync this AOV
            print(f'Sync {layer_name}/{aov_name} from {render_department_name}/{aov_version_name}')

            # Build output path
            output_path = (
                api.storage.resolve('edit:/') /
                sequence_name /
                shot_name /
                layer_name /
                aov_name /
                f'{sequence_name}_{shot_name}.####.exr'
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Copy frames
            for frame_index in render_range:
                input_frame_path = aov.get_aov_frame_path(str(frame_index).zfill(4))
                output_frame_path = _get_frame_path(output_path, frame_index)
                shutil.copyfile(input_frame_path, output_frame_path)

            # Update manifest
            _manifest_set(
                manifest_data,
                [shot_department_name, render_department_name, aov_version_name],
                shot_key,
                layer_name,
                aov_name
            )
            synced_count += 1

    # Save manifest
    _headline('Updating manifest')
    store_json(manifest_path, manifest_data)

    # Done
    print(f'Synced {synced_count} AOVs, skipped {skipped_count}')
    return 0

"""
config = {
    'sequence_name': 'string',
    'shot_name': 'string',
    'purpose': 'render'  # optional
}
"""

def _is_valid_config(config):
    if not isinstance(config, dict): return False
    if 'sequence_name' not in config: return False
    if not isinstance(config['sequence_name'], str): return False
    if 'shot_name' not in config: return False
    if not isinstance(config['shot_name'], str): return False
    return True

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', type=str)
    parser.add_argument('start_frame', type=int)
    parser.add_argument('end_frame', type=int)
    args = parser.parse_args()
    
   # Load config data
    config_path = Path(args.config_path)
    config = load_json(config_path)
    if config is None:
        return _error(f'Config file not found: {config_path}')
    if not _is_valid_config(config):
        return _error(f'Invalid config file: {config_path}')
    
    # Print config
    _headline('Config')
    print(json.dumps(config, indent=4))
    
    # Get the start and end frames
    render_range = BlockRange(args.start_frame, args.end_frame)

    # Run main
    return main(config, render_range)

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())