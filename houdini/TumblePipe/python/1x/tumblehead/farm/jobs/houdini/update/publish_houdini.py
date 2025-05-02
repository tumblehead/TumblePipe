import hou

from tumblehead.api import path_str, default_client
from tumblehead.pipe.paths import (
    latest_shot_hip_file_path,
    next_shot_hip_file_path
)
from tumblehead.pipe.houdini import nodes as ns
from tumblehead.pipe.houdini.lops import (
    build_shot,
    import_assets,
    import_asset_layer,
    import_kit_layer,
    import_shot_layer,
    import_render_layer,
    export_shot_layer,
    export_render_layer
)
from tumblehead.pipe.houdini.sops import (
    import_rigs
)

api = default_client()

# Helpers
def _headline(title):
    print(f' {title} '.center(80, '='))

def _error(msg):
    print(f'ERROR: {msg}')
    return 1

def _update():

    # Find shot build nodes
    build_shot_nodes = list(map(
        build_shot.BuildShot,
        ns.list_by_node_type('build_shot', 'Lop')
    ))

    # Find import asset nodes
    import_assets_nodes = list(map(
        import_assets.ImportAssets,
        ns.list_by_node_type('import_assets', 'Lop')
    ))

    # Find the import asset layer nodes
    import_asset_layer_nodes = list(map(
        import_asset_layer.ImportAssetLayer,
        ns.list_by_node_type('import_asset_layer', 'Lop')
    ))

    # Find the import kit layer nodes
    import_kit_layer_nodes = list(map(
        import_kit_layer.ImportKitLayer,
        ns.list_by_node_type('import_kit_layer', 'Lop')
    ))

    # Find the import shot layer nodes
    import_shot_layer_nodes = list(map(
        import_shot_layer.ImportShotLayer,
        ns.list_by_node_type('import_shot_layer', 'Lop')
    ))

    # Find the import render layer nodes
    import_render_layer_nodes = list(map(
        import_render_layer.ImportRenderLayer,
        ns.list_by_node_type('import_render_layer', 'Lop')
    ))

    # Find the import rigs nodes
    import_rig_nodes = list(map(
        import_rigs.ImportRigs,
        ns.list_by_node_type('import_rigs', 'Sop')
    ))

    # Clear the caches
    import_asset_layer.clear_cache()
    import_kit_layer.clear_cache()
    import_shot_layer.clear_cache()
    import_render_layer.clear_cache()
    import_rigs.clear_cache()

    # Import latest shot builds
    for build_shot_node in build_shot_nodes:
        if not build_shot_node.is_valid(): continue
        build_shot_node.execute()
        print(f'Updated {build_shot_node.path()}')

    # Import latest assets
    for import_assets_node in import_assets_nodes:
        if not import_assets_node.is_valid(): continue
        import_assets_node.execute()
        print(f'Updated {import_assets_node.path()}')

    # Import latest asset layers
    for import_node in import_asset_layer_nodes:
        if not import_node.is_valid(): continue
        import_node.latest()
        import_node.execute()
        print(f'Updated {import_node.path()}')
    
    # Import latest kit layers
    for import_node in import_kit_layer_nodes:
        if not import_node.is_valid(): continue
        import_node.latest()
        import_node.execute()
        print(f'Updated {import_node.path()}')
    
    # Import latest shot layers
    for import_node in import_shot_layer_nodes:
        if not import_node.is_valid(): continue
        import_node.latest()
        import_node.execute()
        print(f'Updated {import_node.path()}')
    
    # Import latest render layers
    for import_node in import_render_layer_nodes:
        if not import_node.is_valid(): continue
        import_node.latest()
        import_node.execute()
        print(f'Updated {import_node.path()}')
    
    # Import latest rigs
    for import_node in import_rig_nodes:
        if not import_node.is_valid(): continue
        import_node.execute()
        print(f'Updated {import_node.path()}')

def _publish(sequence_name, shot_name, department_name):

    def _is_shot_export_correct(node):
        if node.get_department_name() != department_name: return False
        if node.get_sequence_name() != sequence_name: return False
        if node.get_shot_name() != shot_name: return False
        return True

    def _is_render_layer_export_correct(node):
        if node.get_department_name() != department_name: return False
        if node.get_sequence_name() != sequence_name: return False
        if node.get_shot_name() != shot_name: return False
        return True

    # Find the export nodes
    shot_export_nodes = list(filter(
        _is_shot_export_correct,
        map(
            export_shot_layer.ExportShotLayer,
            ns.list_by_node_type('export_shot_layer', 'Lop')
        )
    ))
    render_layer_export_nodes = list(filter(
        _is_render_layer_export_correct,
        map(
            export_render_layer.ExportRenderLayer,
            ns.list_by_node_type('export_render_layer', 'Lop')
        )
    ))

    # Execute the export node
    shot_export_node = shot_export_nodes[0]
    shot_export_node.execute()
    print(f'Published {shot_export_node.path()}')

    # Export the render layers
    for render_layer_export_node in render_layer_export_nodes:
        render_layer_export_node.execute()
        print(f'Published {render_layer_export_node.path()}')

def main(
    sequence_name: str,
    shot_name: str,
    department_name: str
    ) -> int:

    # Print parameters
    _headline('Parameters')
    print(f'Sequence code: {sequence_name}')
    print(f'Shot code: {shot_name}')
    print(f'Department name: {department_name}')

    # Open the latest workfile
    latest_hip_file_path = latest_shot_hip_file_path(
        sequence_name,
        shot_name,
        department_name
    )
    if not latest_hip_file_path.exists():
        return _error(f'Hip file not found at {latest_hip_file_path}')
    hou.hipFile.load(
        path_str(latest_hip_file_path),
        suppress_save_prompt = True,
        ignore_load_warnings = True
    )

    # Update the scene
    _headline('Update')
    _update()

    # Publish the scene
    _headline('Publish')
    _publish(
        sequence_name,
        shot_name,
        department_name
    )

    # Save new workfile
    next_hip_file_path = next_shot_hip_file_path(
        sequence_name,
        shot_name,
        department_name
    )
    hou.hipFile.save(path_str(next_hip_file_path))

    # Done
    _headline('Done')
    return 0

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('script_path', type=str)
    parser.add_argument('sequence_name', type=str)
    parser.add_argument('shot_name', type=str)
    parser.add_argument('department_name', type=str)
    parser.add_argument('first_frame', type=int)
    parser.add_argument('last_frame', type=int)
    args = parser.parse_args()

    # Get the sequence code
    sequence_names = api.config.list_sequence_names()
    sequence_name = args.sequence_name
    if sequence_name not in sequence_names:
        return _error(f'Invalid sequence name: {sequence_name}')

    # Get the shot code
    shot_names = api.config.list_shot_names(sequence_name)
    shot_name = args.shot_name
    if shot_name not in shot_names:
        return _error(f'Invalid shot name: {shot_name}')

    # Get the department name
    department_names = api.config.list_shot_department_names()
    department_name = args.department_name
    if department_name not in department_names:
        return _error(f'Invalid department name: {department_name}')
    
    # Run the main function
    return main(
        sequence_name,
        shot_name,
        department_name
    )

if __name__ == '__main__':
    hou.exit(cli())