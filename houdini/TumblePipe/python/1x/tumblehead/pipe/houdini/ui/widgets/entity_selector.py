"""Reusable entity selector dialog with hierarchical tree view."""

from qtpy import QtWidgets
from qtpy.QtCore import Qt
from qtpy.QtGui import QStandardItemModel, QStandardItem

from tumblehead.util.uri import Uri


# Custom data role for entity URI (matching workspace.py)
EntityUriRole = Qt.UserRole + 1


def _build_entity_tree(root_item, uris, start_segment_index):
    """Build a hierarchical tree from a list of URIs.

    Simplified version of workspace._build_tree_from_uris - single column only.

    Args:
        root_item: The root QStandardItem to attach children to
        uris: List of Uri objects with full paths
        start_segment_index: Which segment index to start building from
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
        _build_entity_tree(root_item, empty_uris, start_segment_index + 1)

    # Create items for each child segment
    for segment_name in sorted(children.keys()):
        child_uris = children[segment_name]

        # Determine if this is a leaf node (all URIs end at this segment)
        is_leaf = all(len(uri.segments) == start_segment_index + 1 for uri in child_uris)

        # Create the item
        item = QStandardItem(segment_name)
        item.setEditable(False)
        item.setSelectable(is_leaf)

        if is_leaf:
            # Store entity URI on leaf item
            item.setData(str(child_uris[0]), EntityUriRole)
        else:
            # Recursively build children
            _build_entity_tree(item, child_uris, start_segment_index + 1)

        root_item.appendRow(item)


class EntitySelectorDialog(QtWidgets.QDialog):
    """Reusable dialog for selecting an entity from a tree view.

    Usage:
        dialog = EntitySelectorDialog(
            api=default_client(),
            entity_filter='both',  # 'assets', 'shots', or 'both'
            include_from_context=True,
            title="Select Entity",
            parent=hou.qt.mainWindow()
        )
        if dialog.exec_():
            selected_uri = dialog.get_selected_uri()
    """

    def __init__(
        self,
        api,
        entity_filter: str = 'both',
        include_from_context: bool = True,
        current_selection: str | None = None,
        title: str = "Select Entity",
        parent=None
    ):
        """Initialize the entity selector dialog.

        Args:
            api: API client for loading entities
            entity_filter: Which entities to show - 'assets', 'shots', or 'both'
            include_from_context: Whether to include 'from_context' option at top
            current_selection: URI string to pre-select and expand to
            title: Dialog window title
            parent: Parent widget
        """
        super().__init__(parent)
        self._api = api
        self._entity_filter = entity_filter
        self._include_from_context = include_from_context
        self._current_selection = current_selection
        self._selected_uri = None

        self.setWindowTitle(title)
        self.setMinimumSize(400, 500)
        self.resize(450, 550)

        self._create_ui()
        self._load_entities()
        self._select_current()

    def _create_ui(self):
        """Create the dialog UI."""
        layout = QtWidgets.QVBoxLayout(self)

        # Tree view
        self._tree_view = QtWidgets.QTreeView()
        self._tree_view.setHeaderHidden(True)
        self._tree_view.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._tree_view.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._tree_view)

        # Button box
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self._ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        self._ok_button.setEnabled(False)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_entities(self):
        """Load entities into the tree model."""
        model = QStandardItemModel()

        # Add "from_context" option at top if enabled
        if self._include_from_context:
            from_context_item = QStandardItem("from_context")
            from_context_item.setEditable(False)
            from_context_item.setSelectable(True)
            from_context_item.setData("from_context", EntityUriRole)
            # Style differently (italic)
            font = from_context_item.font()
            font.setItalic(True)
            from_context_item.setFont(font)
            model.appendRow(from_context_item)

        # Load entities based on filter
        all_uris = []
        if self._entity_filter in ('assets', 'both'):
            try:
                assets = self._api.config.list_entities(
                    Uri.parse_unsafe('entity:/assets'), closure=True
                )
                all_uris.extend([e.uri for e in assets])
            except Exception:
                pass

        if self._entity_filter in ('shots', 'both'):
            try:
                shots = self._api.config.list_entities(
                    Uri.parse_unsafe('entity:/shots'), closure=True
                )
                all_uris.extend([e.uri for e in shots])
            except Exception:
                pass

        # Build tree from segment 0 (includes 'assets', 'shots' as top-level items)
        _build_entity_tree(model.invisibleRootItem(), all_uris, 0)

        # Set model and connect selection
        self._tree_view.setModel(model)
        selection_model = self._tree_view.selectionModel()
        selection_model.selectionChanged.connect(self._on_selection_changed)

        # Expand top-level items by default
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            self._tree_view.expand(index)

    def _select_current(self):
        """Find and select the current_selection item, expanding parents as needed."""
        if not self._current_selection:
            return

        model = self._tree_view.model()
        if not model:
            return

        # Recursively search for matching item
        def find_item(parent_item, target_uri):
            for row in range(parent_item.rowCount()):
                item = parent_item.child(row)
                if item is None:
                    continue
                uri = item.data(EntityUriRole)
                if uri == target_uri:
                    return item
                # Search children
                found = find_item(item, target_uri)
                if found:
                    return found
            return None

        # Search from root
        root = model.invisibleRootItem()
        item = find_item(root, self._current_selection)

        if item:
            index = model.indexFromItem(item)
            # Expand all parent items
            parent_index = index.parent()
            while parent_index.isValid():
                self._tree_view.expand(parent_index)
                parent_index = parent_index.parent()
            # Select and scroll to item
            self._tree_view.setCurrentIndex(index)
            self._tree_view.scrollTo(index)

    def _on_selection_changed(self, selected, deselected):
        """Handle selection changes in the tree view."""
        indexes = selected.indexes()
        if indexes:
            model = self._tree_view.model()
            item = model.itemFromIndex(indexes[0])
            if item and item.isSelectable():
                uri = item.data(EntityUriRole)
                if uri:
                    self._selected_uri = uri
                    self._ok_button.setEnabled(True)
                    return
        self._selected_uri = None
        self._ok_button.setEnabled(False)

    def _on_double_click(self, index):
        """Handle double-click on tree item."""
        model = self._tree_view.model()
        item = model.itemFromIndex(index)
        if item and item.isSelectable():
            uri = item.data(EntityUriRole)
            if uri:
                self._selected_uri = uri
                self.accept()

    def _on_accept(self):
        """Handle OK button click."""
        if self._selected_uri:
            self.accept()

    def get_selected_uri(self) -> str | None:
        """Return the selected URI string, or None if nothing selected."""
        return self._selected_uri
