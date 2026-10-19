"""Copy a shot's best AOVs (the beauty, mattes and ramps of the highest
department and version rendered) into the edit folder, and keep the edit's
manifest of what each AOV there came from."""

import shutil
import json
import sys

from tumblepipe.api import api
from tumblepipe import naming
from tumblepipe.config.timeline import BlockRange
from tumblepipe.config.department import list_departments
from tumblepipe.util.io import load_json_if_exists, store_json
from tumblepipe.pipe.aovs import is_edit_aov
from tumblepipe.pipe.paths import get_render_context
from tumblepipe.pipe.paths.naming import entity_stem
from tumblepipe.util.uri import Uri
from tumblepipe.farm import worker
from tumblepipe.farm.env import print_env
from tumblepipe.farm.frames import frame_file
from tumblepipe.farm.specs import sync as sync_spec
from tumblepipe.farm.worker import ensure_dir, error, headline

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

def _compare_with_manifest(
    prev_data,
    curr_data,
    shot_department_priority,
    render_department_priority
    ):
    """
    Decide whether a resolved AOV beats the manifest entry it would replace.

    Hierarchical comparison: shot dept > render dept > version.
    Both entries are (shot_department, render_department, version) triples.
    Returns (should_sync, message).
    """
    prev_shot_dept, prev_render_dept, prev_version = prev_data
    curr_shot_dept, curr_render_dept, curr_version = curr_data

    prev_shot_idx = shot_department_priority.index(prev_shot_dept)
    curr_shot_idx = shot_department_priority.index(curr_shot_dept)
    if prev_shot_idx > curr_shot_idx:
        return False, f'already at higher shot department {prev_shot_dept}/{prev_render_dept}/{prev_version}'
    if prev_shot_idx < curr_shot_idx:
        return True, f'upgrading from {prev_shot_dept}/{prev_render_dept}/{prev_version} to {curr_shot_dept}/{curr_render_dept}/{curr_version}'

    prev_render_idx = render_department_priority.index(prev_render_dept)
    curr_render_idx = render_department_priority.index(curr_render_dept)
    if prev_render_idx > curr_render_idx:
        return False, f'already at higher render department {prev_render_dept}/{prev_version}'
    if prev_render_idx < curr_render_idx:
        return True, f'upgrading from {prev_render_dept}/{prev_version} to {curr_render_dept}/{curr_version}'

    prev_version_code = naming.get_version_code(prev_version)
    curr_version_code = naming.get_version_code(curr_version)
    if prev_version_code >= curr_version_code:
        return False, f'already at version {prev_render_dept}/{prev_version}'
    return True, f'upgrading from {prev_render_dept}/{prev_version} to {curr_render_dept}/{curr_version}'

def _sync_aov_frames(shot_uri, layer_name, aov_name, aov, render_range):
    """Copy the AOV's frames into the edit folder."""
    output_path = (
        api.storage.resolve(Uri.parse('edit:/')) /
        '/'.join(shot_uri.segments[1:]) /
        layer_name /
        aov_name /
        f'{entity_stem(shot_uri)}.####.exr'
    )
    ensure_dir(output_path.parent)

    # Copy frames
    for frame_index in render_range:
        input_frame_path = aov.get_aov_frame_path(str(frame_index).zfill(4))
        output_frame_path = frame_file(output_path, frame_index)
        shutil.copyfile(input_frame_path, output_frame_path)

def main(shot_uri: Uri, purpose: str, render_range: BlockRange):

    # Print environment variables for debugging
    print_env()

    # Get render context and resolve latest AOVs at runtime
    headline('Resolving latest AOVs across all departments')
    render_context = get_render_context(shot_uri, purpose=purpose)

    # Get both shot and render department priorities
    shot_departments = [d.name for d in list_departments('shots')]
    render_departments = [d.name for d in list_departments('render')]

    print(f'Shot department priority (low->high): {shot_departments}')
    print(f'Render department priority (low->high): {render_departments}')

    # Resolve using both hierarchies - no minimum filters, always pick the best available
    latest_aovs = render_context.resolve_latest_aovs(
        shot_departments,
        render_departments,
        min_shot_department=None,
        min_render_department=None,
        aov_filter=is_edit_aov
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
    manifest_path = api.storage.resolve(Uri.parse('edit:/')) / 'manifest.json'
    # The first sync into an edit has no manifest yet.
    manifest_data = load_json_if_exists(manifest_path) or dict()
    shot_key = str(shot_uri)

    # Debug: Show what's in the manifest
    if shot_key in manifest_data:
        print('Manifest contents:')
        for layer_name, layer_data in manifest_data[shot_key].items():
            for aov_name, data in layer_data.items():
                shot_dept, render_dept, version = data
                print(f'  {layer_name}/{aov_name}: shot_dept={shot_dept}, render_dept={render_dept}, version={version}')
        print()

    # Sync each resolved layer/AOV combination
    synced_count = 0
    skipped_count = 0

    for layer_name, layer_aovs in latest_aovs.items():
        for aov_name, (render_department_name, aov_version_name, aov, shot_department_name) in layer_aovs.items():

            # Check manifest to see if we should skip
            prev_data = _manifest_get(manifest_data, shot_key, layer_name, aov_name)
            if prev_data is not None:
                should_sync, message = _compare_with_manifest(
                    prev_data,
                    (shot_department_name, render_department_name, aov_version_name),
                    shot_departments,
                    render_departments
                )
                if not should_sync:
                    print(f'Skip {layer_name}/{aov_name}: {message}')
                    skipped_count += 1
                    continue
                print(f'Update {layer_name}/{aov_name}: {message}')

            # Sync this AOV
            print(f'Sync {layer_name}/{aov_name} from {render_department_name}/{aov_version_name}')
            _sync_aov_frames(shot_uri, layer_name, aov_name, aov, render_range)

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
    headline('Updating manifest')
    store_json(manifest_path, manifest_data)

    # Done
    print(f'Synced {synced_count} AOVs, skipped {skipped_count}')
    return 0

def from_config(config: dict, render_range: BlockRange) -> int:
    """Sync the shot over the frame span Deadline hands the task."""
    headline('Config')
    print(json.dumps(config, indent=4))

    entity_uri = Uri.parse(config['entity_uri'])
    if entity_uri.segments[0] != 'shots':
        return error(f'Invalid entity type: {entity_uri.segments[0]} (expected shots)')
    if len(entity_uri.segments) < 3:
        return error(f'Invalid entity URI: {entity_uri} (expected entity:/shots/sequence/shot)')
    return main(entity_uri, config['purpose'], render_range)

if __name__ == '__main__':
    sys.exit(worker.run(sync_spec.is_valid_context, from_config))
