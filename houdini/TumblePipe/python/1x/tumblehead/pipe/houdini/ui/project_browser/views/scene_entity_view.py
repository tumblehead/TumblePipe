"""
Entity browser for scene description editor.

Shows a tree view of shots hierarchy with inline scene assignment.
Supports:
- Two columns: Name and Scene
- Dropdown editor for scene assignment
- Greyed-out display for inherited scenes
"""

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from qtpy.QtWidgets import (
    QComboBox,
    QStyledItemDelegate,
    QTreeView,
)

from tumblehead.api import default_client
from tumblehead.util.uri import Uri
from tumblehead.config.scene import (
    get_scene_ref,
    set_scene_ref,
    get_inherited_scene_ref,
)
from tumblehead.config.scenes import list_scenes

api = default_client()

# Custom data roles
SceneUriRole = Qt.UserRole + 1
InheritedRole = Qt.UserRole + 2
EntityUriRole = Qt.UserRole + 3


class SceneDropdownDelegate(QStyledItemDelegate):
    """Delegate for inline scene dropdown editing in the Scene column."""

    # Emitted when user changes scene assignment (stores pending, doesn't save yet)
    scene_change_pending = Signal(str, str)  # entity_uri_str, scene_uri_str (or "" to clear)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scenes_cache = None
        self._pending_changes = {}  # entity_uri_str -> scene_uri_str

    def _get_scenes(self) -> list:
        """Get cached list of scenes."""
        if self._scenes_cache is None:
            self._scenes_cache = list_scenes()
        return self._scenes_cache

    def refresh_scenes(self):
        """Clear scene cache to reload on next use."""
        self._scenes_cache = None

    def createEditor(self, parent, option, index):
        """Create a combo box for scene selection."""
        combo = QComboBox(parent)
        combo.addItem("(none)", "")

        for scene in self._get_scenes():
            combo.addItem(scene.display_name, str(scene.uri))

        # Connect signal to commit changes when selection made
        combo.activated.connect(lambda: self.commitData.emit(combo))
        combo.activated.connect(lambda: self.closeEditor.emit(combo))

        return combo

    def setEditorData(self, editor, index):
        """Set the editor's current value."""
        scene_uri_str = index.data(SceneUriRole) or ""
        idx = editor.findData(scene_uri_str)
        if idx >= 0:
            editor.setCurrentIndex(idx)

    def setModelData(self, editor, model, index):
        """Store pending scene change (doesn't save to DB until Save is clicked)."""
        scene_uri_str = editor.currentData()
        entity_uri_str = index.data(EntityUriRole)

        if entity_uri_str:
            # Store pending change
            self._pending_changes[entity_uri_str] = scene_uri_str or None

            # Emit signal for window to track
            self.scene_change_pending.emit(entity_uri_str, scene_uri_str or "")

            # Update visual display (without saving to DB)
            model.update_scene_display(entity_uri_str, scene_uri_str, self._pending_changes)

    def get_pending_changes(self) -> dict[str, str | None]:
        """Get all pending scene assignment changes."""
        return dict(self._pending_changes)

    def clear_pending_changes(self):
        """Clear all pending changes."""
        self._pending_changes.clear()

    def has_pending_changes(self) -> bool:
        """Check if there are pending changes."""
        return bool(self._pending_changes)

    def updateEditorGeometry(self, editor, option, index):
        """Set editor geometry to match cell."""
        editor.setGeometry(option.rect)


class SceneEntityModel(QStandardItemModel):
    """Model for entities with scene assignments (two columns: Name, Scene)."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["Name", "Scene"])

        self._uri_to_items = {}  # uri_str -> (name_item, scene_item)

    def load_entities(self):
        """Load shots from database."""
        self.clear()
        self._uri_to_items = {}
        self.setHorizontalHeaderLabels(["Name", "Scene"])

        # Get all shot entities with closure=True (flat list)
        shot_entities = api.config.list_entities(
            Uri.parse_unsafe('entity:/shots'),
            closure=True
        )

        # Add shots hierarchy
        shots_uri = Uri.parse_unsafe("entity:/shots")

        # Create name item
        name_item = QStandardItem("shots")
        name_item.setEditable(False)
        name_item.setData(str(shots_uri), Qt.UserRole)

        # Create scene item for root
        scene_ref, inherited_from = get_inherited_scene_ref(shots_uri)
        scene_item = self._create_scene_item(shots_uri, scene_ref, inherited_from)

        self._uri_to_items[str(shots_uri)] = (name_item, scene_item)

        # Build hierarchy from flat list of entities
        self._build_tree_from_entities(name_item, shot_entities, 1)
        self.appendRow([name_item, scene_item])

    def _create_scene_item(
        self,
        entity_uri: Uri,
        scene_ref: Uri | None,
        inherited_from: Uri | None
    ) -> QStandardItem:
        """Create a scene item with proper styling."""
        item = QStandardItem()
        item.setEditable(True)
        item.setData(str(entity_uri), EntityUriRole)

        if scene_ref:
            display_name = '/'.join(scene_ref.segments)
            item.setText(display_name)
            item.setData(str(scene_ref), SceneUriRole)
            is_inherited = inherited_from is not None
            item.setData(is_inherited, InheritedRole)

            if is_inherited:
                item.setForeground(QBrush(QColor("#888888")))
        else:
            item.setText("")
            item.setData("", SceneUriRole)
            item.setData(False, InheritedRole)

        return item

    def _build_tree_from_entities(
        self,
        parent_item: QStandardItem,
        entities: list,
        segment_index: int
    ):
        """Build tree hierarchy from flat entity list."""
        # Group by segment at current level
        children = {}
        for entity in entities:
            uri = entity.uri
            if len(uri.segments) <= segment_index:
                continue
            segment = uri.segments[segment_index]
            if segment not in children:
                children[segment] = []
            children[segment].append(entity)

        for segment in sorted(children.keys()):
            child_entities = children[segment]

            # Build URI for this segment
            first_uri = child_entities[0].uri
            uri_str = f"{first_uri.purpose}:/{'/'.join(first_uri.segments[:segment_index + 1])}"
            item_uri = Uri.parse_unsafe(uri_str)

            # Create name item
            name_item = QStandardItem(segment)
            name_item.setEditable(False)
            name_item.setData(str(item_uri), Qt.UserRole)

            # Create scene item
            scene_ref, inherited_from = get_inherited_scene_ref(item_uri)
            scene_item = self._create_scene_item(item_uri, scene_ref, inherited_from)

            self._uri_to_items[str(item_uri)] = (name_item, scene_item)

            # Recursively build children
            self._build_tree_from_entities(name_item, child_entities, segment_index + 1)

            parent_item.appendRow([name_item, scene_item])

    def get_item_for_uri(self, uri: Uri) -> QStandardItem | None:
        """Get the name item for a URI."""
        items = self._uri_to_items.get(str(uri))
        return items[0] if items else None

    def refresh_scene_data(self):
        """Refresh all scene assignments from database."""
        for uri_str, (name_item, scene_item) in self._uri_to_items.items():
            entity_uri = Uri.parse_unsafe(uri_str)
            scene_ref, inherited_from = get_inherited_scene_ref(entity_uri)

            if scene_ref:
                display_name = '/'.join(scene_ref.segments)
                scene_item.setText(display_name)
                scene_item.setData(str(scene_ref), SceneUriRole)
                is_inherited = inherited_from is not None
                scene_item.setData(is_inherited, InheritedRole)

                if is_inherited:
                    scene_item.setForeground(QBrush(QColor("#888888")))
                else:
                    scene_item.setForeground(QBrush())  # Default color
            else:
                scene_item.setText("")
                scene_item.setData("", SceneUriRole)
                scene_item.setData(False, InheritedRole)
                scene_item.setForeground(QBrush())

    def update_scene_display(
        self,
        changed_entity_uri_str: str,
        scene_uri_str: str | None,
        pending_changes: dict[str, str | None]
    ):
        """Update visual display for a scene assignment change without saving to DB.

        This updates the changed entity and all descendants that inherit from it,
        using pending_changes to resolve what scene each entity "would" have.
        """
        def _get_effective_scene(uri_str: str) -> tuple[str | None, bool]:
            """Get the effective scene for an entity, considering pending changes.

            Returns (scene_uri_str, is_inherited).
            """
            entity_uri = Uri.parse_unsafe(uri_str)
            segments = entity_uri.segments

            # Walk from this entity up to root
            for i in range(len(segments), 0, -1):
                ancestor_segments = segments[:i]
                ancestor_uri_str = f"{entity_uri.purpose}:/{'/'.join(ancestor_segments)}"

                # Check pending changes first
                if ancestor_uri_str in pending_changes:
                    pending_scene = pending_changes[ancestor_uri_str]
                    if pending_scene:
                        is_inherited = ancestor_uri_str != uri_str
                        return pending_scene, is_inherited
                    elif pending_scene is None:
                        # Explicitly cleared - continue up hierarchy
                        continue

                # Fall back to database
                ancestor_uri = Uri.parse_unsafe(ancestor_uri_str)
                db_scene_ref = get_scene_ref(ancestor_uri)
                if db_scene_ref:
                    is_inherited = ancestor_uri_str != uri_str
                    return str(db_scene_ref), is_inherited

            return None, False

        # Update all entities that might be affected
        # (the changed entity and any descendants)
        for uri_str, (name_item, scene_item) in self._uri_to_items.items():
            # Check if this entity is the changed one or a descendant
            if uri_str == changed_entity_uri_str or uri_str.startswith(changed_entity_uri_str + '/'):
                effective_scene, is_inherited = _get_effective_scene(uri_str)

                if effective_scene:
                    scene_uri = Uri.parse_unsafe(effective_scene)
                    display_name = '/'.join(scene_uri.segments)
                    scene_item.setText(display_name)
                    scene_item.setData(effective_scene, SceneUriRole)
                    scene_item.setData(is_inherited, InheritedRole)

                    if is_inherited:
                        scene_item.setForeground(QBrush(QColor("#888888")))
                    else:
                        scene_item.setForeground(QBrush())
                else:
                    scene_item.setText("")
                    scene_item.setData("", SceneUriRole)
                    scene_item.setData(False, InheritedRole)
                    scene_item.setForeground(QBrush())


class SceneEntityView(QTreeView):
    """Tree view for entities with inline scene assignment."""

    selected = Signal(object)  # Emits Uri or None
    scene_assignment_changed = Signal()  # Emitted when a scene assignment changes

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setSelectionMode(QTreeView.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setHeaderHidden(False)

        # Initialize _selecting BEFORE setModel() because setModel triggers selectionChanged
        self._selecting = False

        self._model = SceneEntityModel(self)
        self.setModel(self._model)

        # Set up delegate for scene column
        self._scene_delegate = SceneDropdownDelegate(self)
        self.setItemDelegateForColumn(1, self._scene_delegate)

    def load_entities(self):
        """Load entities from database."""
        # Block signals while rebuilding to prevent view update spam
        self._selecting = True
        self._model.blockSignals(True)
        try:
            self._model.load_entities()
            self._scene_delegate.refresh_scenes()
        finally:
            self._model.blockSignals(False)
            self._selecting = False
        # Reset model to trigger single view update
        self.setModel(self._model)
        self.setItemDelegateForColumn(1, self._scene_delegate)
        self.expandAll()
        self.resizeColumnToContents(0)
        self.resizeColumnToContents(1)

    def refresh(self):
        """Refresh the entity tree."""
        current_uri = self.get_selected()
        self._selecting = True
        self._model.blockSignals(True)
        try:
            self._model.load_entities()
            self._scene_delegate.refresh_scenes()
        finally:
            self._model.blockSignals(False)
            self._selecting = False
        # Reset model to trigger single view update
        self.setModel(self._model)
        self.setItemDelegateForColumn(1, self._scene_delegate)
        self.expandAll()
        self.resizeColumnToContents(0)
        self.resizeColumnToContents(1)
        if current_uri:
            self.set_selected(current_uri)

    def refresh_scenes(self):
        """Refresh scene assignments without rebuilding tree."""
        self._model.refresh_scene_data()
        self._scene_delegate.refresh_scenes()

    def get_selected(self) -> Uri | None:
        """Get the currently selected URI."""
        indexes = self.selectedIndexes()
        if not indexes:
            return None
        item = self._model.itemFromIndex(indexes[0])
        if item is None:
            return None
        uri_str = item.data(Qt.UserRole)
        if uri_str is None:
            return None
        return Uri.parse_unsafe(uri_str)

    def set_selected(self, uri: Uri | None):
        """Set the selected URI."""
        if uri is None:
            self.clearSelection()
            return
        item = self._model.get_item_for_uri(uri)
        if item is None:
            self.clearSelection()
            return
        index = item.index()
        self._selecting = True
        self.setCurrentIndex(index)
        self.scrollTo(index)
        self._selecting = False

    def selectionChanged(self, selected, deselected):
        """Handle selection changes."""
        super().selectionChanged(selected, deselected)
        if self._selecting:
            return
        if not selected.indexes():
            self.selected.emit(None)
            return
        index = selected.indexes()[0]
        item = self._model.itemFromIndex(index)
        if item is None:
            self.selected.emit(None)
            return
        uri_str = item.data(Qt.UserRole)
        if uri_str is None:
            self.selected.emit(None)
            return
        self.selected.emit(Uri.parse_unsafe(uri_str))
