from qtpy.QtGui import QStandardItemModel, QStandardItem


def _create_workspace_model(api):
    # Create the model
    model = QStandardItemModel()

    # Populate assets
    assets_item = QStandardItem("Assets")
    assets_item.setSelectable(False)
    assets_item.setEditable(False)
    for category_name in api.config.list_category_names():
        category_item = QStandardItem(category_name)
        category_item.setSelectable(False)
        category_item.setEditable(False)
        for asset_name in api.config.list_asset_names(category_name):
            asset_item = QStandardItem(asset_name)
            asset_item.setEditable(False)
            category_item.appendRow(asset_item)
        assets_item.appendRow(category_item)
    model.appendRow(assets_item)

    # Populate shots
    shots_item = QStandardItem("Shots")
    shots_item.setSelectable(False)
    shots_item.setEditable(False)
    for sequence_name in api.config.list_sequence_names():
        sequence_item = QStandardItem(sequence_name)
        sequence_item.setSelectable(False)
        sequence_item.setEditable(False)
        for shot_name in api.config.list_shot_names(sequence_name):
            shot_item = QStandardItem(shot_name)
            shot_item.setEditable(False)
            sequence_item.appendRow(shot_item)
        shots_item.appendRow(sequence_item)
    model.appendRow(shots_item)

    # Populate kits
    kits_item = QStandardItem("Kits")
    kits_item.setSelectable(False)
    kits_item.setEditable(False)
    for category_name in api.config.list_kit_category_names():
        category_item = QStandardItem(category_name)
        category_item.setSelectable(False)
        category_item.setEditable(False)
        for kit_name in api.config.list_kit_names(category_name):
            kit_item = QStandardItem(kit_name)
            kit_item.setEditable(False)
            category_item.appendRow(kit_item)
        kits_item.appendRow(category_item)
    model.appendRow(kits_item)

    # Done
    return model