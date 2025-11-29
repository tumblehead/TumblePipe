from qtpy.QtGui import QStandardItemModel, QStandardItem

from tumblehead.util.uri import Uri


def _build_tree_from_uris(root_item, uris, start_segment_index):
    """Build a hierarchical tree from a list of URIs

    Args:
        root_item: The root QStandardItem to attach children to
        uris: List of Uri objects with full paths (e.g., entity:/assets/CHAR/character1)
        start_segment_index: Which segment index to start building from (e.g., 0 for 'assets')
    """
    # Group URIs by their segment at the current level
    children = {}
    for uri in uris:
        if len(uri.segments) <= start_segment_index:
            continue

        segment_name = uri.segments[start_segment_index]
        if segment_name not in children:
            children[segment_name] = []
        children[segment_name].append(uri)

    # Handle empty segments by processing their children at current level
    if '' in children:
        empty_uris = children.pop('')
        _build_tree_from_uris(root_item, empty_uris, start_segment_index + 1)

    # Create items for each child segment
    for segment_name in sorted(children.keys()):
        child_uris = children[segment_name]

        # Determine if this is a leaf node (all URIs end at this segment)
        is_leaf = all(len(uri.segments) == start_segment_index + 1 for uri in child_uris)

        # Create the item
        item = QStandardItem(segment_name)
        item.setEditable(False)

        if is_leaf:
            # Leaf nodes are selectable
            item.setSelectable(True)
        else:
            # Intermediate nodes are not selectable
            item.setSelectable(False)
            # Recursively build children
            _build_tree_from_uris(item, child_uris, start_segment_index + 1)

        root_item.appendRow(item)


def _create_workspace_model(api):
    # Create the model
    model = QStandardItemModel()

    # Collect all entity URIs
    all_uris = []

    # Get asset entities
    asset_uris = api.config.list_entities(Uri.parse_unsafe('entity:/assets'), closure=True)
    all_uris.extend(asset_uris)

    # Get shot entities
    shot_uris = api.config.list_entities(Uri.parse_unsafe('entity:/shots'), closure=True)
    all_uris.extend(shot_uris)

    # Get group URIs
    try:
        shot_groups = api.config.list_groups('shots')
        asset_groups = api.config.list_groups('assets')
        all_groups = shot_groups + asset_groups
        for group in all_groups:
            all_uris.append(group.uri)
    except AttributeError:
        # list_groups not available in this config
        pass

    # Build tree from segment 0 (includes 'assets', 'shots', 'groups' as top-level items)
    # Uri.segments strips leading '/' so entity:/assets/CHAR/Baby has segments ['assets', 'CHAR', 'Baby']
    _build_tree_from_uris(model.invisibleRootItem(), all_uris, 0)

    # Done
    return model