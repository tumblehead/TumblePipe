from qtpy.QtCore import Qt
from qtpy.QtGui import QBrush, QColor, QStandardItemModel, QStandardItem

from tumblehead.util.uri import Uri
from tumblehead.config.groups import list_groups, find_groups_for_entity
from tumblehead.config.scene import get_inherited_scene_ref

# Custom data role for entity URI
EntityUriRole = Qt.UserRole + 1


def _ensure_top_level_items(model):
    """Ensure assets, shots, and groups top-level items always exist in consistent order"""
    root_item = model.invisibleRootItem()

    # Build map of existing rows using takeRow to preserve ownership
    existing = {}
    while root_item.rowCount() > 0:
        row = root_item.takeRow(0)
        if row and row[0]:
            existing[row[0].text()] = row

    # Required order: assets, shots, groups
    for name in ['assets', 'shots', 'groups']:
        if name in existing:
            root_item.appendRow(existing[name])
        else:
            name_item = QStandardItem(name)
            name_item.setEditable(False)
            name_item.setSelectable(False)
            scene_item = QStandardItem()
            scene_item.setEditable(False)
            group_item = QStandardItem()
            group_item.setEditable(False)
            root_item.appendRow([name_item, scene_item, group_item])


def _build_tree_from_uris(root_item, uris, start_segment_index, context=None):
    """Build a hierarchical tree from a list of URIs with 3 columns

    Args:
        root_item: The root QStandardItem to attach children to
        uris: List of Uri objects with full paths (e.g., entity:/assets/CHAR/character1)
        start_segment_index: Which segment index to start building from (e.g., 0 for 'assets')
        context: The top-level context ('assets', 'shots', 'groups', or None to detect)
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
        _build_tree_from_uris(root_item, empty_uris, start_segment_index + 1, context)

    # Create items for each child segment
    for segment_name in sorted(children.keys()):
        child_uris = children[segment_name]

        # Determine context from first URI if not provided
        current_context = context
        if current_context is None and child_uris:
            first_uri = child_uris[0]
            if first_uri.segments:
                current_context = first_uri.segments[0]

        # Determine if this is a leaf node (all URIs end at this segment)
        is_leaf = all(len(uri.segments) == start_segment_index + 1 for uri in child_uris)

        # Create the name item (column 0)
        name_item = QStandardItem(segment_name)
        name_item.setEditable(False)

        # Create scene item (column 1)
        scene_item = QStandardItem()
        scene_item.setEditable(False)

        # Create group item (column 2)
        group_item = QStandardItem()
        group_item.setEditable(False)

        if is_leaf:
            # Leaf nodes are selectable
            name_item.setSelectable(True)

            # Get entity URI for lookup
            entity_uri = child_uris[0]

            # Store entity URI on the name item
            name_item.setData(str(entity_uri), EntityUriRole)

            # Skip Scene/Group columns for groups themselves
            if current_context != 'groups':
                # Scene column (only for shots)
                if current_context == 'shots':
                    try:
                        scene_ref, inherited_from = get_inherited_scene_ref(entity_uri)
                        if scene_ref:
                            scene_item.setText('/'.join(scene_ref.segments))
                            if inherited_from:
                                scene_item.setForeground(QBrush(QColor("#888888")))
                    except Exception:
                        pass

                # Group column (for both assets and shots)
                try:
                    groups = find_groups_for_entity(entity_uri)
                    if groups:
                        group_names = ', '.join(g.name for g in groups)
                        group_item.setText(group_names)
                except Exception:
                    pass
        else:
            # Intermediate nodes are not selectable
            name_item.setSelectable(False)
            # Recursively build children
            _build_tree_from_uris(name_item, child_uris, start_segment_index + 1, current_context)

        root_item.appendRow([name_item, scene_item, group_item])


def _create_workspace_model(api):
    # Create the model with 3 columns
    model = QStandardItemModel()
    model.setColumnCount(3)
    model.setHorizontalHeaderLabels(["Name", "Scene", "Group"])

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