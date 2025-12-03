from qtpy.QtGui import QStandardItemModel, QStandardItem

from tumblehead.util.uri import Uri
from tumblehead.config.groups import list_groups


def _ensure_top_level_items(model):
    """Ensure assets, shots, and groups top-level items always exist in consistent order"""
    root_item = model.invisibleRootItem()

    # Build map of existing items using takeRow to preserve ownership
    existing = {}
    while root_item.rowCount() > 0:
        item = root_item.takeRow(0)[0]
        if item:
            existing[item.text()] = item

    # Required order: assets, shots, groups
    for name in ['assets', 'shots', 'groups']:
        if name in existing:
            root_item.appendRow(existing[name])
        else:
            item = QStandardItem(name)
            item.setEditable(False)
            item.setSelectable(False)
            root_item.appendRow(item)


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
    asset_entities = api.config.list_entities(Uri.parse_unsafe('entity:/assets'), closure=True)
    all_uris.extend([entity.uri for entity in asset_entities])

    # Get shot entities
    shot_entities = api.config.list_entities(Uri.parse_unsafe('entity:/shots'), closure=True)
    all_uris.extend([entity.uri for entity in shot_entities])

    # Get group URIs - transform to display under 'groups' top-level item
    try:
        shot_groups = list_groups('shots')
        asset_groups = list_groups('assets')
        all_groups = shot_groups + asset_groups
        for group in all_groups:
            # Transform group URI to have 'groups' as first segment for tree display
            # e.g., groups:/shots/test -> display:/groups/shots/test
            tree_uri = Uri.parse_unsafe(f"display:/groups/{'/'.join(group.uri.segments)}")
            all_uris.append(tree_uri)
    except Exception:
        # Groups module not available or no groups defined
        pass

    # Build tree from segment 0 (includes 'assets', 'shots', 'groups' as top-level items)
    # Uri.segments strips leading '/' so entity:/assets/CHAR/Baby has segments ['assets', 'CHAR', 'Baby']
    _build_tree_from_uris(model.invisibleRootItem(), all_uris, 0)

    # Ensure required top-level items always exist (even when empty)
    _ensure_top_level_items(model)

    # Done
    return model