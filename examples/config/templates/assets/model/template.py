from tumblehead.pipe.houdini.lops import (
    create_model,
    model_validator,
    import_asset_layer,
    export_asset_layer
)

def create(scene_node, category_name, asset_name):

    # Create the create model node
    create_node = create_model.create(scene_node, 'create_model')

    # Create the import previous model node
    prev_import_node = import_asset_layer.create(scene_node, 'import_prev_model')

    # Create the model validator node
    validator_node = model_validator.create(scene_node, 'model_validator')
    validator_node.setInput(0, prev_import_node)
    validator_node.setInput(1, create_node)

    # Create the export node
    export_node = export_asset_layer.create(scene_node, 'export_model')
    export_node.setInput(0, create_node)