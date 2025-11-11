from pathlib import Path
import json

import hou

from tumblehead.api import path_str, default_client
from tumblehead.util.io import load_json
from tumblehead.pipe.paths import (
    latest_hip_file_path,
    next_hip_file_path,
    Entity,
    AssetEntity,
    ShotEntity,
    KitEntity,
)
from tumblehead.pipe.houdini import nodes as ns
from tumblehead.pipe.houdini.lops import (
    build_shot,
    import_assets,
    import_asset_layer,
    import_shot_layer,
    import_kit_layer,
    import_render_layer,
    export_asset_layer,
    export_shot_layer,
    export_render_layer,
    export_kit_layer
)
from tumblehead.pipe.houdini.sops import (
    import_rigs
)
from tumblehead.pipe.houdini.cops import (
    build_comp
)

api = default_client()

# Helpers
def _headline(title):
    print(f' {title} '.center(80, '='))

def _error(msg):
    print(f'ERROR: {msg}')
    return 1

def _update(entity):

    # Find shot build nodes
    build_shot_nodes = list(map(
        build_shot.BuildShot,
        ns.list_by_node_type('build_shot', 'Lop')
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
    import_rigs_nodes = list(map(
        import_rigs.ImportRigs,
        ns.list_by_node_type('import_rigs', 'Sop')
    ))

    # Import latest shot builds
    for build_shot_node in build_shot_nodes:
        if not build_shot_node.is_valid(): continue
        build_shot_node.execute()
        print(f'Updated {build_shot_node.path()}')
    
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
    for import_node in import_rigs_nodes:
        if not import_node.is_valid(): continue
        import_node.execute()
        print(f'Updated {import_node.path()}')

def _publish(entity: Entity):

    def _publish_asset(
        category_name: str,
        asset_name: str,
        department_name: str    
        ):

        def _is_asset_export_correct(node):
            if node.get_department_name() != department_name: return False
            if node.get_category_name() != category_name: return False
            if node.get_asset_name() != asset_name: return False
            return True
        
        # Find the export nodes
        export_nodes = list(filter(
            _is_asset_export_correct,
            map(
                export_asset_layer.ExportAssetLayer,
                ns.list_by_node_type('export_asset_layer', 'Lop')
            )
        ))

        # Execute the export node
        asset_export_node = export_nodes[0]
        asset_export_node.execute(force_local=True)
        print(f'Published {asset_export_node.path()}')

    def _publish_shot(
        sequence_name: str,
        shot_name: str,
        department_name: str    
        ):
        
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
        shot_export_node.execute(force_local=True)
        print(f'Published {shot_export_node.path()}')

        # Export the render layers
        for render_layer_export_node in render_layer_export_nodes:
            render_layer_export_node.execute()
            print(f'Published {render_layer_export_node.path()}')

    def _publish_kit(
        category_name: str,
        kit_name: str,
        department_name: str    
        ):
        
        def _is_kit_export_correct(node):
            if node.get_department_name() != department_name: return False
            if node.get_category_name() != category_name: return False
            if node.get_kit_name() != kit_name: return False
            return True
    
        # Find the export nodes
        kit_export_nodes = list(filter(
            _is_kit_export_correct,
            map(
                export_kit_layer.ExportKitLayer,
                ns.list_by_node_type('export_kit_layer', 'Lop')
            )
        ))

        # Execute the export node
        kit_export_node = kit_export_nodes[0]
        kit_export_node.execute(force_local=True)
        print(f'Published {kit_export_node.path()}')

    match entity:
        case AssetEntity(category_name, asset_name, department_name):
            _publish_asset(
                category_name = category_name,
                asset_name = asset_name,
                department_name = department_name
            )
        case ShotEntity(sequence_name, shot_name, department_name):
            _publish_shot(
                sequence_name = sequence_name,
                shot_name = shot_name,
                department_name = department_name
            )
        case KitEntity(category_name, kit_name, department_name):
            _publish_kit(
                category_name = category_name,
                kit_name = kit_name,
                department_name = department_name
            )
        case _:
            return _error(f'Invalid entity type: {entity}')

def _save(entity: Entity):
    hip_file_path = next_hip_file_path(entity)
    hou.hipFile.save(path_str(hip_file_path))

def _load_workfile(entity: Entity, force_reload: bool = True) -> bool:
    """Load the latest workfile for the given entity. Returns True if successful."""
    hip_file_path = latest_hip_file_path(entity)
    if hip_file_path is None or not hip_file_path.exists():
        print(f'No workfile found for entity: {entity}')
        return False
    
    # Check if we're already in the correct workfile
    current_file = hou.hipFile.path()
    if not force_reload and current_file == path_str(hip_file_path):
        print(f'Already in correct workfile: {hip_file_path}')
        return True
    
    try:
        hou.hipFile.load(
            path_str(hip_file_path),
            suppress_save_prompt = True,
            ignore_load_warnings = True
        )
        print(f'Loaded workfile: {hip_file_path}')
        return True
    except Exception as e:
        print(f'Failed to load workfile {hip_file_path}: {e}')
        return False


def main(config) -> int:

    # Print parameters
    _headline('Parameters')
    print(f'Config: {json.dumps(config, indent=2)}')

    # Parse the entity from the config
    entity = Entity.from_json(config['entity'])
    if entity is None:
        return _error('Invalid entity in config')
    
    # Get downstream departments from config
    downstream_departments = config['tasks']['publish'].get('downstream_departments', [])

    # Build list of all entities to process (main + downstream)
    downstream_entities = [entity]
    for department_name in downstream_departments:
        match entity:
            case ShotEntity(sequence_name, shot_name, _):
                downstream_entities.append(ShotEntity(
                    sequence_name = sequence_name,
                    shot_name = shot_name,
                    department_name = department_name
                ))
            case KitEntity(category_name, kit_name, _):
                downstream_entities.append(KitEntity(
                    category_name = category_name,
                    kit_name = kit_name,
                    department_name = department_name
                ))
            case AssetEntity(category_name, asset_name, _):
                downstream_entities.append(AssetEntity(
                    category_name = category_name,
                    asset_name = asset_name,
                    department_name = department_name
                ))
            case _: assert False, f'Unknown entity: {entity}'

    # Process all entities using the same code path
    _headline('Processing Entities')
    for entity in downstream_entities:
        print(f'\nDepartment: {entity.department_name}')
        
        # Load workfile for this entity
        if not _load_workfile(entity, force_reload = True):
            print(f'Skipping {entity} - could not load workfile')
            continue
        
        try:
            # Update the scene with latest imports
            _headline('Updating')
            _update(entity)
            
            # Publish the department
            _headline('Publishing')
            _publish(entity)
            
            # Save new version
            _headline('Saving')
            _save(entity)
            
        except Exception as e:
            print(f'Error processing department {entity.department_name}: {e}')
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