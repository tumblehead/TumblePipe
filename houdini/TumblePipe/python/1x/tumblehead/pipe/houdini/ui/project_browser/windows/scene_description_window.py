"""
Scene Editor window.

Manages scene definitions with hierarchical organization.
- Left panel: Hierarchical scenes tree + Shots tree with inline scene column
- Right panel: Scene asset editor (when scene selected)

Scene assignment to shots is done inline via the Scene column dropdown.
Changes are batched until Save is clicked, which regenerates root .usda
files and submits build jobs for affected shots using ProcessDialog for
progress tracking.
"""

from dataclasses import dataclass, field
import uuid

from qtpy.QtCore import Qt, Signal, QEvent
from qtpy.QtGui import QBrush, QColor, QStandardItemModel, QStandardItem
from qtpy.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyledItemDelegate,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from tumblehead.util.uri import Uri
from tumblehead.config.scene import (
    list_available_assets,
    generate_root_version,
    set_scene_ref,
)
from tumblehead.config.scenes import (
    AssetEntry,
    DEFAULT_VARIANT,
    SceneTreeNode,
    add_scene,
    remove_scene,
    get_scene,
    get_inherited_assets,
    list_scene_tree,
    set_scene_assets,
    find_all_shots_using_scene,
    export_scene_version,
)
from tumblehead.config.variants import list_variants, refresh_cache as refresh_variants_cache

from ..views.scene_entity_view import SceneEntityView
from ..dialogs import ProcessDialog, SaveConfirmationDialog
from ..models.process_task import ProcessTask


@dataclass
class PendingChanges:
    """Tracks all uncommitted changes in the scene editor."""

    # Shot scene assignment changes: entity_uri_str -> scene_uri_str (or None to clear)
    shot_scene_changes: dict[str, str | None] = field(default_factory=dict)

    def has_changes(self) -> bool:
        """Check if there are any pending changes."""
        return bool(self.shot_scene_changes)

    def clear(self):
        """Clear all pending changes."""
        self.shot_scene_changes.clear()

    def get_affected_scenes(self) -> set[str]:
        """Get all scenes that are referenced by pending shot changes."""
        return {v for v in self.shot_scene_changes.values() if v}


class AvailableAssetsModel(QStandardItemModel):
    """Model for available assets (showing assets that have unused variants)."""

    def __init__(self, parent=None):
        super().__init__(parent)

    def load_assets(self, all_assets: list[Uri], scene_entries: list[AssetEntry]):
        """Load available assets, showing assets that have unused variants.

        An asset is only hidden when ALL its variants are used in scene_entries.

        Args:
            all_assets: List of all available asset URIs
            scene_entries: List of AssetEntry objects currently in the scene
        """
        self.clear()

        # Build a set of (asset_uri_str, variant) tuples that are used
        used_asset_variants = {(entry.asset, entry.variant) for entry in scene_entries}

        # Refresh variants cache once before iterating (not inside the loop!)
        refresh_variants_cache()

        # Group by category, but only include assets with available variants
        grouped = {}
        for asset_uri in all_assets:
            uri_str = str(asset_uri)

            # Get all variants for this asset
            all_variants = set(list_variants(asset_uri))

            # Get variants already used for this asset
            used_variants = {v for a, v in used_asset_variants if a == uri_str}

            # Only include asset if it has unused variants
            if used_variants >= all_variants:
                continue  # All variants used, skip this asset

            if len(asset_uri.segments) >= 3:
                category = asset_uri.segments[1]
                asset_name = asset_uri.segments[2]

                if category not in grouped:
                    grouped[category] = []
                grouped[category].append((asset_name, uri_str))

        # Build tree
        for category in sorted(grouped.keys()):
            category_item = QStandardItem(category)
            category_item.setSelectable(False)
            category_item.setEditable(False)

            for asset_name, uri_str in sorted(grouped[category]):
                asset_item = QStandardItem(asset_name)
                asset_item.setData(uri_str, Qt.UserRole)
                asset_item.setEditable(False)
                category_item.appendRow(asset_item)

            self.appendRow(category_item)


class SceneAssetsModel(QStandardItemModel):
    """Model for assets in a scene, with support for inherited assets, instance counts, and variants.

    Now supports multiple entries of the same asset with different variants.
    """

    # Custom roles
    AssetUriRole = Qt.UserRole + 1
    InheritedFromRole = Qt.UserRole + 2  # Scene URI this asset is inherited from
    CountRole = Qt.UserRole + 3  # Instance count
    VariantRole = Qt.UserRole + 4  # Asset variant
    EntryIndexRole = Qt.UserRole + 5  # Index in the assets list (for removal)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["Asset", "Count", "Variant"])
        self._inherited_entries = []  # Store inherited entries for get_all_entries()

    def load_assets(
        self,
        direct_assets: list[AssetEntry],
        inherited_assets: list[tuple[AssetEntry, Uri]] = None
    ):
        """Load scene assets with inheritance support.

        Args:
            direct_assets: List of AssetEntry objects for direct assets
            inherited_assets: List of (AssetEntry, inherited_from_scene_uri) tuples
        """
        self.clear()
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["Asset", "Count", "Variant"])

        if inherited_assets is None:
            inherited_assets = []

        self._inherited_entries = [entry for entry, _ in inherited_assets]

        # Group all assets by category (inherited first, then direct)
        # Structure: { category: [ (entry, from_scene or None, entry_index or None), ... ] }
        grouped = {}

        # Add inherited assets (will be shown grayed out)
        for entry, from_scene in inherited_assets:
            asset_uri = Uri.parse_unsafe(entry.asset)
            if len(asset_uri.segments) >= 3:
                category = asset_uri.segments[1]
                if category not in grouped:
                    grouped[category] = []
                grouped[category].append((entry, from_scene, None))

        # Add direct assets with their index (for removal)
        for idx, entry in enumerate(direct_assets):
            asset_uri = Uri.parse_unsafe(entry.asset)
            if len(asset_uri.segments) >= 3:
                category = asset_uri.segments[1]
                if category not in grouped:
                    grouped[category] = []
                grouped[category].append((entry, None, idx))

        # Build tree - categories at root level
        for category in sorted(grouped.keys()):
            category_item = QStandardItem(category)
            category_item.setSelectable(False)
            category_item.setEditable(False)

            # Empty columns for category row
            category_count_item = QStandardItem("")
            category_count_item.setSelectable(False)
            category_count_item.setEditable(False)

            category_variant_item = QStandardItem("")
            category_variant_item.setSelectable(False)
            category_variant_item.setEditable(False)

            # Sort assets by name then variant
            sorted_assets = sorted(grouped[category], key=lambda x: (x[0].asset, x[0].variant))

            for entry, from_scene, entry_index in sorted_assets:
                asset_uri = Uri.parse_unsafe(entry.asset)
                asset_name = asset_uri.segments[2]
                count = entry.instances
                variant = entry.variant

                # Name column
                name_item = QStandardItem(asset_name)
                name_item.setData(entry.asset, self.AssetUriRole)
                name_item.setData(count, self.CountRole)
                name_item.setData(variant, self.VariantRole)
                name_item.setData(entry_index, self.EntryIndexRole)
                name_item.setEditable(False)

                # Count column
                count_item = QStandardItem(str(count))
                count_item.setData(entry.asset, self.AssetUriRole)
                count_item.setData(count, self.CountRole)
                count_item.setData(variant, self.VariantRole)
                count_item.setData(entry_index, self.EntryIndexRole)

                # Variant column
                variant_item = QStandardItem(variant)
                variant_item.setData(entry.asset, self.AssetUriRole)
                variant_item.setData(variant, self.VariantRole)
                variant_item.setData(entry_index, self.EntryIndexRole)

                if from_scene is not None:
                    # Inherited asset - gray out and make non-selectable/non-editable
                    name_item.setData(str(from_scene), self.InheritedFromRole)
                    name_item.setSelectable(False)
                    name_item.setForeground(QBrush(QColor("#888888")))

                    count_item.setData(str(from_scene), self.InheritedFromRole)
                    count_item.setSelectable(False)
                    count_item.setEditable(False)
                    count_item.setForeground(QBrush(QColor("#888888")))

                    variant_item.setData(str(from_scene), self.InheritedFromRole)
                    variant_item.setSelectable(False)
                    variant_item.setEditable(False)
                    variant_item.setForeground(QBrush(QColor("#888888")))
                else:
                    # Direct asset - count and variant are editable
                    name_item.setData(None, self.InheritedFromRole)
                    count_item.setData(None, self.InheritedFromRole)
                    count_item.setEditable(True)
                    variant_item.setData(None, self.InheritedFromRole)
                    variant_item.setEditable(True)

                category_item.appendRow([name_item, count_item, variant_item])

            self.appendRow([category_item, category_count_item, category_variant_item])

    def get_assets(self) -> list[AssetEntry]:
        """Get direct assets with counts and variants (not inherited)."""
        assets = []
        for row in range(self.rowCount()):
            category_item = self.item(row, 0)
            for child_row in range(category_item.rowCount()):
                name_item = category_item.child(child_row, 0)
                count_item = category_item.child(child_row, 1)
                variant_item = category_item.child(child_row, 2)
                uri_str = name_item.data(self.AssetUriRole)
                inherited_from = name_item.data(self.InheritedFromRole)
                # Only include direct assets (not inherited)
                if uri_str and inherited_from is None:
                    # Get count from count column text (in case user edited it)
                    count = int(count_item.text()) if count_item else 1
                    # Get variant from variant column text
                    variant = variant_item.text() if variant_item else DEFAULT_VARIANT
                    assets.append(AssetEntry(
                        asset=uri_str,
                        instances=max(1, count),
                        variant=variant or DEFAULT_VARIANT
                    ))
        return assets

    def get_all_entries(self) -> list[AssetEntry]:
        """Get all entries (both direct and inherited) for exclusion calculation."""
        return self.get_assets() + self._inherited_entries

    def get_used_variants_for_asset(self, asset_uri_str: str) -> set[str]:
        """Return variants already used for a specific asset (both direct and inherited)."""
        all_entries = self.get_all_entries()
        return {entry.variant for entry in all_entries if entry.asset == asset_uri_str}

    def add_asset(self, uri_str: str, entry: AssetEntry = None):
        """Add an asset to the model with the first available variant.

        If entry is provided, uses its variant. Otherwise picks the first unused variant.
        """
        uri = Uri.parse_unsafe(uri_str)
        if len(uri.segments) < 3:
            return

        category = uri.segments[1]
        asset_name = uri.segments[2]

        # Determine which variant to use
        if entry is None:
            # Pick the first available variant
            refresh_variants_cache()
            all_variants = list_variants(uri)
            used_variants = self.get_used_variants_for_asset(uri_str)
            available_variants = [v for v in all_variants if v not in used_variants]

            if not available_variants:
                return  # No available variants

            variant = available_variants[0]
            entry = AssetEntry(asset=uri_str, instances=1, variant=variant)
        else:
            # Check if this specific asset+variant combo already exists
            used_variants = self.get_used_variants_for_asset(uri_str)
            if entry.variant in used_variants:
                return  # Already exists

        # Find or create category item at root level
        category_item = None
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if item.text() == category:
                category_item = item
                break

        if category_item is None:
            category_item = QStandardItem(category)
            category_item.setSelectable(False)
            category_item.setEditable(False)

            category_count_item = QStandardItem("")
            category_count_item.setSelectable(False)
            category_count_item.setEditable(False)

            category_variant_item = QStandardItem("")
            category_variant_item.setSelectable(False)
            category_variant_item.setEditable(False)

            self.appendRow([category_item, category_count_item, category_variant_item])

        # Add asset item (direct asset - normal styling)
        name_item = QStandardItem(asset_name)
        name_item.setData(uri_str, self.AssetUriRole)
        name_item.setData(None, self.InheritedFromRole)
        name_item.setData(entry.instances, self.CountRole)
        name_item.setData(entry.variant, self.VariantRole)
        name_item.setEditable(False)

        count_item = QStandardItem(str(entry.instances))
        count_item.setData(uri_str, self.AssetUriRole)
        count_item.setData(None, self.InheritedFromRole)
        count_item.setData(entry.instances, self.CountRole)
        count_item.setData(entry.variant, self.VariantRole)
        count_item.setEditable(True)

        variant_item = QStandardItem(entry.variant)
        variant_item.setData(uri_str, self.AssetUriRole)
        variant_item.setData(None, self.InheritedFromRole)
        variant_item.setData(entry.variant, self.VariantRole)
        variant_item.setEditable(True)

        category_item.appendRow([name_item, count_item, variant_item])

    def remove_asset(self, uri_str: str, variant: str = None):
        """Remove a direct asset from the model by asset URI and optionally variant.

        If variant is None, removes the first direct asset with this URI.
        Cannot remove inherited assets.
        """
        for row in range(self.rowCount()):
            category_item = self.item(row, 0)
            for child_row in range(category_item.rowCount()):
                child = category_item.child(child_row, 0)
                child_uri = child.data(self.AssetUriRole)
                child_variant = child.data(self.VariantRole)
                inherited_from = child.data(self.InheritedFromRole)

                # Match by URI, and optionally by variant
                if child_uri == uri_str:
                    if variant is not None and child_variant != variant:
                        continue  # Not the right variant
                    if inherited_from is not None:
                        continue  # Can't remove inherited

                    category_item.removeRow(child_row)
                    # Remove empty category
                    if category_item.rowCount() == 0:
                        self.removeRow(row)
                    return


class ScenesTreeModel(QStandardItemModel):
    """Model for hierarchical scenes tree."""

    # Custom roles
    SceneUriRole = Qt.UserRole + 1
    IsSceneRole = Qt.UserRole + 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._uri_to_item = {}

    def load_scenes(self):
        """Load scenes into hierarchical tree."""
        self.clear()
        self._uri_to_item = {}

        tree = list_scene_tree()
        for node in tree:
            item = self._build_tree_item(node)
            self.appendRow(item)

    def _build_tree_item(self, node: SceneTreeNode) -> QStandardItem:
        """Build a tree item from a scene node."""
        item = QStandardItem(node.name)
        item.setEditable(False)
        item.setData(str(node.uri), self.SceneUriRole)
        item.setData(node.is_scene, self.IsSceneRole)

        # All nodes are scenes - no special styling

        for child in node.children:
            child_item = self._build_tree_item(child)
            item.appendRow(child_item)

        self._uri_to_item[str(node.uri)] = item
        return item

    def get_item_for_uri(self, uri: Uri) -> QStandardItem | None:
        """Get item for a URI."""
        return self._uri_to_item.get(str(uri))


class VariantDelegate(QStyledItemDelegate):
    """Delegate for inline variant dropdown editing in the scene assets tree.

    Filters the variant dropdown to show only unused variants for each asset.
    """

    def __init__(self, scene_model: SceneAssetsModel, parent=None):
        super().__init__(parent)
        self._scene_model = scene_model

    def createEditor(self, parent, option, index):
        """Create a combobox editor populated with available (unused) variants."""
        combo = QComboBox(parent)

        # Get the asset URI and current variant
        asset_uri_str = index.data(SceneAssetsModel.AssetUriRole)
        current_variant = index.data(SceneAssetsModel.VariantRole)

        if asset_uri_str:
            asset_uri = Uri.parse_unsafe(asset_uri_str)
            # Refresh cache to pick up any changes from Database Editor
            refresh_variants_cache()
            all_variants = list_variants(asset_uri)

            # Get variants already used for this asset
            used_variants = self._scene_model.get_used_variants_for_asset(asset_uri_str)
            # Allow the current variant (so user can keep it)
            used_variants.discard(current_variant)

            # Only show available variants
            available_variants = [v for v in all_variants if v not in used_variants]
            combo.addItems(available_variants)

        # Commit and close when selection is made
        combo.activated.connect(lambda: self.commitData.emit(combo))
        combo.activated.connect(lambda: self.closeEditor.emit(combo))
        return combo

    def setEditorData(self, editor, index):
        """Set the current variant in the combobox."""
        variant = index.data(SceneAssetsModel.VariantRole) or DEFAULT_VARIANT
        idx = editor.findText(variant)
        if idx >= 0:
            editor.setCurrentIndex(idx)

    def setModelData(self, editor, model, index):
        """Save the selected variant back to the model."""
        model.setData(index, editor.currentText())
        model.setData(index, editor.currentText(), SceneAssetsModel.VariantRole)


class SceneEditorPanel(QWidget):
    """Panel for editing a scene's assets."""

    scene_changed = Signal()  # Emitted when changes are saved
    changes_made = Signal()   # Emitted when unsaved changes occur

    def __init__(self, parent=None):
        super().__init__(parent)

        self._current_scene_uri = None
        self._original_assets = []  # List of AssetEntry objects
        self._inherited_assets = []  # List of (AssetEntry, from_scene_uri) tuples
        self._has_changes = False

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header with scene name
        self._header_label = QLabel("No scene selected")
        self._header_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(self._header_label)

        # Asset transfer area
        asset_layout = QHBoxLayout()
        layout.addLayout(asset_layout, stretch=1)

        # Available assets
        available_panel = QWidget()
        available_layout = QVBoxLayout(available_panel)
        available_layout.setContentsMargins(0, 0, 0, 0)
        available_label = QLabel("Available Assets")
        available_label.setStyleSheet("font-weight: bold;")
        available_layout.addWidget(available_label)

        self._available_model = AvailableAssetsModel()
        self._available_tree = QTreeView()
        self._available_tree.setModel(self._available_model)
        self._available_tree.setHeaderHidden(True)
        self._available_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._available_tree.doubleClicked.connect(self._on_available_double_click)
        available_layout.addWidget(self._available_tree)
        asset_layout.addWidget(available_panel, stretch=1)

        # Transfer buttons
        button_panel = QWidget()
        button_layout = QVBoxLayout(button_panel)
        button_layout.addStretch()
        self._add_button = QPushButton("->")
        self._add_button.clicked.connect(self._add_to_scene)
        self._add_button.setEnabled(False)
        button_layout.addWidget(self._add_button)
        self._remove_button = QPushButton("<-")
        self._remove_button.clicked.connect(self._remove_from_scene)
        self._remove_button.setEnabled(False)
        button_layout.addWidget(self._remove_button)
        button_layout.addStretch()
        asset_layout.addWidget(button_panel)

        # Scene assets
        scene_panel = QWidget()
        scene_layout = QVBoxLayout(scene_panel)
        scene_layout.setContentsMargins(0, 0, 0, 0)
        scene_label = QLabel("Scene Assets")
        scene_label.setStyleSheet("font-weight: bold;")
        scene_layout.addWidget(scene_label)

        self._scene_model = SceneAssetsModel()
        self._scene_tree = QTreeView()
        self._scene_tree.setModel(self._scene_model)
        self._scene_tree.setHeaderHidden(False)  # Show column headers
        self._scene_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._scene_tree.selectionModel().selectionChanged.connect(
            self._on_scene_selection_changed
        )
        # Install event filter for Delete key handling
        self._scene_tree.installEventFilter(self)
        # Connect dataChanged to track count/variant edits
        self._scene_model.dataChanged.connect(self._on_model_data_changed)
        # Set variant delegate for column 2 (pass scene_model for variant filtering)
        self._variant_delegate = VariantDelegate(self._scene_model)
        self._scene_tree.setItemDelegateForColumn(2, self._variant_delegate)
        # Configure header column widths (model has 3 columns: Asset, Count, Variant)
        header = self._scene_tree.header()
        header.setStretchLastSection(False)
        if header.count() >= 3:
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            header.setSectionResizeMode(1, QHeaderView.Interactive)
            header.setSectionResizeMode(2, QHeaderView.Interactive)
            header.resizeSection(1, 60)
            header.resizeSection(2, 100)
        scene_layout.addWidget(self._scene_tree)
        asset_layout.addWidget(scene_panel, stretch=1)

    def set_scene(self, scene_uri: Uri | None):
        """Set the scene to edit."""
        self._current_scene_uri = scene_uri

        if scene_uri is None:
            self._header_label.setText("No scene selected")
            self._available_model.clear()
            self._scene_model.clear()
            self._add_button.setEnabled(False)
            self._remove_button.setEnabled(False)
            self._original_assets = []
            self._inherited_assets = []
            self._has_changes = False
            return

        scene = get_scene(scene_uri)
        if scene is None:
            self._header_label.setText(f"Scene not found: {scene_uri}")
            self._available_model.clear()
            self._scene_model.clear()
            return

        # Show full path in header
        self._header_label.setText(f"SCENE: {scene.display_name}")

        # Get inherited assets from parent scenes
        self._inherited_assets = get_inherited_assets(scene_uri)

        # Load assets with inheritance (scene.assets is now list[AssetEntry])
        self._scene_model.load_assets(scene.assets, self._inherited_assets)
        self._original_assets = list(scene.assets)  # Copy the list

        # Ensure header is configured after data load (may not be ready in __init__)
        header = self._scene_tree.header()
        if header.count() >= 3:
            header.setStretchLastSection(False)
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            header.setSectionResizeMode(1, QHeaderView.Interactive)
            header.setSectionResizeMode(2, QHeaderView.Interactive)
            header.resizeSection(1, 60)
            header.resizeSection(2, 100)

        # Load available assets, using all entries (direct + inherited) for exclusion
        all_assets = list_available_assets()
        all_scene_entries = self._scene_model.get_all_entries()
        self._available_model.load_assets(all_assets, all_scene_entries)

        self._available_tree.expandAll()
        self._scene_tree.expandAll()
        self._add_button.setEnabled(True)
        self._has_changes = False

    def has_unsaved_changes(self) -> bool:
        return self._has_changes

    def save_changes(self) -> bool:
        """Save scene changes. Returns True on success."""
        if self._current_scene_uri is None:
            return False

        # Check if scene still exists (may have been deleted)
        scene = get_scene(self._current_scene_uri)
        if scene is None:
            return False

        try:
            # get_assets() now returns list[AssetEntry]
            assets = self._scene_model.get_assets()
            set_scene_assets(self._current_scene_uri, assets)
            self._original_assets = list(assets)  # Store copy
            self._has_changes = False
            self.scene_changed.emit()
            return True
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save scene: {e}")
            return False

    def discard_changes(self):
        """Discard changes and reload."""
        self.set_scene(self._current_scene_uri)

    def _get_selected_available(self) -> list[str]:
        selected = []
        for index in self._available_tree.selectedIndexes():
            item = self._available_model.itemFromIndex(index)
            if item:
                uri_str = item.data(Qt.UserRole)
                if uri_str:
                    selected.append(uri_str)
        return selected

    def _get_selected_scene(self) -> list[tuple[str, str]]:
        """Get selected direct assets (not inherited) as (uri_str, variant) tuples."""
        selected = set()  # Use set to deduplicate
        for index in self._scene_tree.selectedIndexes():
            # Only process column 0 (name column has all data roles)
            if index.column() != 0:
                continue
            item = self._scene_model.itemFromIndex(index)
            if item:
                uri_str = item.data(SceneAssetsModel.AssetUriRole)
                variant = item.data(SceneAssetsModel.VariantRole)
                inherited_from = item.data(SceneAssetsModel.InheritedFromRole)
                # Only include direct assets with valid uri and variant
                if uri_str and variant and inherited_from is None:
                    selected.add((uri_str, variant))
        return list(selected)

    def _add_to_scene(self):
        for uri_str in self._get_selected_available():
            self._scene_model.add_asset(uri_str)
        self._refresh_available()
        self._mark_changed()

    def _remove_from_scene(self):
        for uri_str, variant in self._get_selected_scene():
            self._scene_model.remove_asset(uri_str, variant)
        self._refresh_available()
        self._mark_changed()

    def _on_available_double_click(self, index):
        item = self._available_model.itemFromIndex(index)
        if item:
            uri_str = item.data(Qt.UserRole)
            if uri_str:
                self._scene_model.add_asset(uri_str)
                self._refresh_available()
                self._mark_changed()

    def _on_scene_selection_changed(self):
        selected = self._get_selected_scene()
        self._remove_button.setEnabled(len(selected) > 0)

    def _refresh_available(self):
        all_assets = list_available_assets()
        all_scene_entries = self._scene_model.get_all_entries()
        self._available_model.load_assets(all_assets, all_scene_entries)
        self._available_tree.expandAll()
        self._scene_tree.expandAll()

    def _mark_changed(self):
        current = self._scene_model.get_assets()  # Returns list[AssetEntry]
        # Compare lists by converting to comparable form (set of tuples)
        current_set = {(e.asset, e.instances, e.variant) for e in current}
        original_set = {(e.asset, e.instances, e.variant) for e in self._original_assets}
        self._has_changes = current_set != original_set
        self.changes_made.emit()

    def _on_model_data_changed(self, top_left, bottom_right, roles):
        """Handle data changes in the model (e.g., count edits)."""
        self._mark_changed()

    def eventFilter(self, obj, event):
        """Handle key events on scene tree."""
        if obj == self._scene_tree and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Delete:
                self._remove_from_scene()
                return True
        return super().eventFilter(obj, event)


class SceneDescriptionWindow(QMainWindow):
    """Scene editor window with hierarchical scenes and inline shot assignment."""

    data_changed = Signal(object)
    window_closed = Signal()

    def __init__(self, api, parent=None):
        super().__init__(parent)

        self.setWindowFlags(
            Qt.Window |
            Qt.WindowCloseButtonHint |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint
        )

        self.setWindowTitle("Scene Editor")
        self.resize(1100, 700)

        self._api = api
        self._current_scene_uri = None
        self._pending_changes = PendingChanges()

        self._build_ui()
        self._load_scenes()
        self._entity_view.load_entities()

        # Connect delegate's pending change signal
        self._entity_view._scene_delegate.scene_change_pending.connect(
            self._on_scene_assignment_pending
        )

    def _build_ui(self):
        central_widget = QSplitter(Qt.Horizontal, self)
        self.setCentralWidget(central_widget)

        # Left panel: Navigation
        nav_widget = QWidget()
        nav_layout = QVBoxLayout(nav_widget)
        nav_layout.setContentsMargins(0, 0, 0, 0)

        # Scenes section
        scenes_label = QLabel("SCENES")
        scenes_label.setStyleSheet("font-weight: bold;")
        nav_layout.addWidget(scenes_label)

        # Scenes tree (hierarchical)
        self._scenes_model = ScenesTreeModel()
        self._scenes_tree = QTreeView()
        self._scenes_tree.setModel(self._scenes_model)
        self._scenes_tree.setHeaderHidden(True)
        self._scenes_tree.setSelectionMode(QTreeView.SingleSelection)
        self._scenes_tree.selectionModel().selectionChanged.connect(
            self._on_scene_selected
        )
        # Context menu for add/remove
        self._scenes_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._scenes_tree.customContextMenuRequested.connect(
            self._on_scenes_context_menu
        )
        nav_layout.addWidget(self._scenes_tree)

        # Shots section
        shots_label = QLabel("SHOTS")
        shots_label.setStyleSheet("font-weight: bold;")
        nav_layout.addWidget(shots_label)

        self._entity_view = SceneEntityView(self)
        self._entity_view.selected.connect(self._on_shot_selected)
        nav_layout.addWidget(self._entity_view)

        central_widget.addWidget(nav_widget)

        # Right panel: Editor (stacked)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._editor_stack = QStackedWidget()

        # Empty panel
        empty_panel = QWidget()
        empty_layout = QVBoxLayout(empty_panel)
        empty_layout.addStretch()
        empty_label = QLabel("Select a scene to edit its assets\n\n"
                             "Scene assignment to shots is done via the\n"
                             "Scene column dropdown in the shots tree")
        empty_label.setAlignment(Qt.AlignCenter)
        empty_label.setStyleSheet("color: #888;")
        empty_layout.addWidget(empty_label)
        empty_layout.addStretch()
        self._editor_stack.addWidget(empty_panel)

        # Scene editor panel
        self._scene_editor = SceneEditorPanel()
        self._scene_editor.scene_changed.connect(self._on_scene_data_changed)
        self._scene_editor.changes_made.connect(self._update_buttons)
        self._editor_stack.addWidget(self._scene_editor)

        right_layout.addWidget(self._editor_stack, stretch=1)

        # Bottom buttons
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(5, 5, 5, 5)

        self._discard_button = QPushButton("Discard Changes")
        self._discard_button.clicked.connect(self._on_discard_changes)
        self._discard_button.setEnabled(False)
        button_layout.addWidget(self._discard_button)

        button_layout.addStretch()

        self._save_button = QPushButton("Save")
        self._save_button.clicked.connect(self._on_save_changes)
        self._save_button.setEnabled(False)
        button_layout.addWidget(self._save_button)

        right_layout.addLayout(button_layout)

        central_widget.addWidget(right_widget)
        central_widget.setSizes([350, 750])

    def _load_scenes(self):
        """Load scenes into the tree."""
        self._scenes_model.load_scenes()
        self._scenes_tree.expandAll()
        # Refresh entity view to update scene dropdowns
        self._entity_view.refresh_scenes()

    def _on_scenes_context_menu(self, point):
        """Show context menu for scenes tree."""
        index = self._scenes_tree.indexAt(point)
        menu = QMenu(self)

        if not index.isValid():
            # Clicked on empty area - offer to create scene
            new_action = menu.addAction("New Scene...")
            selected = menu.exec_(self._scenes_tree.mapToGlobal(point))
            if selected == new_action:
                self._on_add_scene()
            return

        item = self._scenes_model.itemFromIndex(index)
        if item is None:
            return

        scene_uri_str = item.data(ScenesTreeModel.SceneUriRole)

        # All nodes are scenes - offer same options
        new_subscene_action = menu.addAction("New Subscene...")
        menu.addSeparator()
        delete_action = menu.addAction("Delete Scene")
        selected = menu.exec_(self._scenes_tree.mapToGlobal(point))
        if selected == new_subscene_action:
            self._on_add_scene_in_category(scene_uri_str)
        elif selected == delete_action:
            self._on_remove_scene()

    def _on_add_scene_in_category(self, category_uri_str: str):
        """Add a scene with category prefix pre-filled."""
        uri = Uri.parse_unsafe(category_uri_str)
        prefix = '/'.join(uri.segments)

        name, ok = QInputDialog.getText(
            self, "New Scene",
            f"Scene name (will be added under '{prefix}/'):"
        )
        if not ok or not name.strip():
            return

        path = prefix + '/' + name.strip()
        try:
            scene_uri = add_scene(path)
            self._load_scenes()
            # Select the new scene
            item = self._scenes_model.get_item_for_uri(scene_uri)
            if item:
                index = self._scenes_model.indexFromItem(item)
                self._scenes_tree.setCurrentIndex(index)
        except ValueError as e:
            QMessageBox.warning(self, "Error", str(e))

    def _on_add_scene(self):
        """Create a new scene."""
        path, ok = QInputDialog.getText(
            self, "New Scene",
            "Scene path (e.g., 'forest' or 'outdoor/forest'):"
        )
        if not ok or not path.strip():
            return

        path = path.strip()
        try:
            scene_uri = add_scene(path)
            self._load_scenes()
            # Select the new scene
            item = self._scenes_model.get_item_for_uri(scene_uri)
            if item:
                index = self._scenes_model.indexFromItem(item)
                self._scenes_tree.setCurrentIndex(index)
        except ValueError as e:
            QMessageBox.warning(self, "Error", str(e))

    def _on_remove_scene(self):
        """Delete the selected scene with shot cleanup."""
        indexes = self._scenes_tree.selectedIndexes()
        if not indexes:
            return

        item = self._scenes_model.itemFromIndex(indexes[0])
        if item is None:
            return

        scene_uri_str = item.data(ScenesTreeModel.SceneUriRole)
        scene_uri = Uri.parse_unsafe(scene_uri_str)
        scene_name = item.text()

        # Find affected shots
        from tumblehead.config.scenes import find_shots_with_scene_ref
        from ..dialogs import AffectedEntitiesDialog

        affected_shots = find_shots_with_scene_ref(scene_uri)

        # Show confirmation dialog
        dialog = AffectedEntitiesDialog(
            title="Delete Scene",
            header=f'Delete scene "{scene_name}"?',
            affected_entities=affected_shots,
            action_description="Their scene assignments will be cleared.",
            confirm_button_text="Delete",
            parent=self
        )

        if dialog.exec_() != QDialog.Accepted:
            return

        try:
            # Clear scene references from affected shots
            from tumblehead.config.scene import set_scene_ref
            for shot_uri in affected_shots:
                set_scene_ref(shot_uri, None)

            # Delete the scene
            remove_scene(scene_uri)
            self._load_scenes()
            self._entity_view.refresh_scenes()
            self._scene_editor.set_scene(None)  # Clear editor state to prevent stale saves
            self._editor_stack.setCurrentIndex(0)
            self._current_scene_uri = None
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to delete scene: {e}")

    def _check_unsaved_changes(self) -> bool:
        """Check for unsaved changes. Returns True if ok to proceed."""
        has_scene_changes = self._scene_editor.has_unsaved_changes()
        has_pending_assignments = self._entity_view._scene_delegate.has_pending_changes()

        if has_scene_changes or has_pending_assignments:
            result = QMessageBox.question(
                self, "Unsaved Changes",
                "You have unsaved changes. Do you want to save them?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save
            )
            if result == QMessageBox.Cancel:
                return False
            elif result == QMessageBox.Save:
                self._on_save_changes()
            elif result == QMessageBox.Discard:
                # Clear pending changes on discard
                self._pending_changes.clear()
                self._entity_view._scene_delegate.clear_pending_changes()
        return True

    def _on_scene_selected(self, selected, deselected):
        """Handle scene selection."""
        if not self._check_unsaved_changes():
            # Restore previous selection
            if self._current_scene_uri:
                item = self._scenes_model.get_item_for_uri(self._current_scene_uri)
                if item:
                    index = self._scenes_model.indexFromItem(item)
                    self._scenes_tree.selectionModel().blockSignals(True)
                    self._scenes_tree.setCurrentIndex(index)
                    self._scenes_tree.selectionModel().blockSignals(False)
            return

        indexes = self._scenes_tree.selectedIndexes()
        if not indexes:
            return

        item = self._scenes_model.itemFromIndex(indexes[0])
        if item is None:
            return

        scene_uri_str = item.data(ScenesTreeModel.SceneUriRole)

        # All nodes are scenes
        scene_uri = Uri.parse_unsafe(scene_uri_str)
        self._current_scene_uri = scene_uri

        self._scene_editor.set_scene(scene_uri)
        self._editor_stack.setCurrentIndex(1)
        self._update_buttons()

    def _on_shot_selected(self, uri: Uri | None):
        """Handle shot selection - just for visual feedback, editing is inline."""
        # Scene assignment is done via inline dropdown, no panel needed
        pass

    def _on_scene_data_changed(self):
        """Handle scene data change signal."""
        self._load_scenes()
        self.data_changed.emit(self._current_scene_uri)

    def _on_scene_assignment_pending(self, entity_uri_str: str, scene_uri_str: str):
        """Track pending scene assignment change from dropdown."""
        self._pending_changes.shot_scene_changes[entity_uri_str] = scene_uri_str or None
        self._update_buttons()

    def _update_buttons(self):
        """Update button states."""
        has_scene_changes = self._scene_editor.has_unsaved_changes()
        has_pending_assignments = self._pending_changes.has_changes()
        has_delegate_changes = self._entity_view._scene_delegate.has_pending_changes()
        has_changes = has_scene_changes or has_pending_assignments or has_delegate_changes
        self._discard_button.setEnabled(has_changes)
        self._save_button.setEnabled(has_changes)

    def _on_discard_changes(self):
        """Discard all pending changes."""
        result = QMessageBox.question(
            self, "Discard Changes",
            "Are you sure you want to discard all changes?"
        )
        if result != QMessageBox.Yes:
            return

        # Discard scene asset changes
        self._scene_editor.discard_changes()

        # Clear pending shot scene assignments
        self._pending_changes.clear()
        self._entity_view._scene_delegate.clear_pending_changes()

        # Refresh entity view to restore original values from database
        self._entity_view.refresh_scenes()

        self._update_buttons()

    def _on_save_changes(self):
        """Save all pending changes with progress dialog.

        Key optimization: Roots reference scenes via entity:/ URIs which resolve
        dynamically, so when scene assets change, we only need to export the
        scene - no root/shot rebuild!

        Only rebuild roots/shots when scene ASSIGNMENTS change.
        """
        # Check if there are any changes to save
        has_scene_changes = self._scene_editor.has_unsaved_changes()
        pending_shot_changes = self._entity_view._scene_delegate.get_pending_changes()

        if not has_scene_changes and not pending_shot_changes:
            return

        # Separate the two types of changes:
        # 1. Scene asset changes (only need scene export)
        # 2. Shot assignment changes (need scene export + root + build)

        # Scenes that need export (from editor changes)
        scenes_to_export = set()
        if has_scene_changes and self._current_scene_uri:
            scenes_to_export.add(str(self._current_scene_uri))

        # Shots with assignment changes need root + build
        shots_with_assignment_changes = []
        for entity_uri_str, scene_uri_str in pending_shot_changes.items():
            shots_with_assignment_changes.append(Uri.parse_unsafe(entity_uri_str))
            # Also export the new scene if one is assigned
            if scene_uri_str:
                scenes_to_export.add(scene_uri_str)

        # For confirmation dialog, only show shots that will be rebuilt
        # (not all shots using the scene - they don't need rebuilding!)
        affected_shots_list = sorted(shots_with_assignment_changes, key=str)

        # Step 1: Show confirmation dialog (preview)
        confirm_dialog = SaveConfirmationDialog(
            scene_changes_count=1 if has_scene_changes else 0,
            shot_assignment_changes_count=len(pending_shot_changes),
            affected_shots=affected_shots_list,
            parent=self
        )

        if confirm_dialog.exec_() != QDialog.Accepted:
            return

        # Step 2: Collect tasks for ProcessDialog
        tasks = self._collect_save_tasks(scenes_to_export, shots_with_assignment_changes)

        if not tasks:
            # No tasks to run, just apply database changes
            self._apply_database_changes()
            self._on_save_completed({})
            return

        # Step 3: Show ProcessDialog with tasks
        # Note: current_department=None skips mode filtering, keeping all tasks enabled
        # This is appropriate since Scene Editor tasks are all local-only
        process_dialog = ProcessDialog(
            title="Save Scene Changes",
            tasks=tasks,
            current_department=None,
            pre_execute_callback=self._apply_database_changes,
            parent=self
        )
        process_dialog.process_completed.connect(self._on_save_completed)
        process_dialog.exec_()

    def _collect_save_tasks(
        self,
        scenes_to_export: set[str],
        shots_with_assignment_changes: list[Uri]
    ) -> list[ProcessTask]:
        """Create ProcessTask objects for saving scene changes.

        Key optimization: Only rebuild roots/shots when scene ASSIGNMENTS change.
        Scene asset changes only need scene export (roots use entity:/ URIs that resolve dynamically).

        Order: Scene exports → Root generation → Build staged
        """
        tasks = []

        # 1. Scene export tasks (for scene asset changes AND new assignments)
        for scene_uri_str in scenes_to_export:
            scene_uri = Uri.parse_unsafe(scene_uri_str)
            scene_task = ProcessTask(
                id=str(uuid.uuid4()),
                uri=scene_uri,
                department='scene',
                task_type='export',
                description="Export scene layer",
                execute_local=lambda uri=scene_uri: self._execute_scene_export(uri),
                execute_farm=None,  # Local only
            )
            tasks.append(scene_task)

        # 2. Root + Build tasks ONLY for shots with assignment changes
        # (not needed for pure scene asset changes - entity:/ URIs resolve dynamically)
        for shot_uri in shots_with_assignment_changes:
            root_task = ProcessTask(
                id=str(uuid.uuid4()),
                uri=shot_uri,
                department='root',
                task_type='generate',
                description="Generate root layer",
                execute_local=lambda uri=shot_uri: self._execute_root_generation(uri),
                execute_farm=None,  # Root generation is local only
            )
            tasks.append(root_task)

            build_task = ProcessTask(
                id=str(uuid.uuid4()),
                uri=shot_uri,
                department='staged',
                task_type='build',
                description="Build USD",
                execute_local=lambda uri=shot_uri: self._execute_build_local(uri),
                execute_farm=None,  # Local only for scene editor
            )
            tasks.append(build_task)

        return tasks

    def _apply_database_changes(self):
        """Apply scene and shot scene assignment changes to database.

        Called as pre_execute_callback before ProcessDialog runs tasks.
        """
        # Phase 1: Apply scene asset changes
        if self._scene_editor.has_unsaved_changes():
            self._scene_editor.save_changes()

        # Phase 2: Apply shot scene assignment changes
        pending_shot_changes = self._entity_view._scene_delegate.get_pending_changes()
        for entity_uri_str, scene_uri_str in pending_shot_changes.items():
            entity_uri = Uri.parse_unsafe(entity_uri_str)
            scene_uri = Uri.parse_unsafe(scene_uri_str) if scene_uri_str else None
            set_scene_ref(entity_uri, scene_uri)

    def _execute_scene_export(self, scene_uri: Uri):
        """Execute scene .usda export."""
        export_scene_version(scene_uri)

    def _execute_root_generation(self, shot_uri: Uri):
        """Execute root .usda generation for a shot."""
        generate_root_version(shot_uri)

    def _execute_build_local(self, shot_uri: Uri):
        """Execute build locally for a shot."""
        from tumblehead.pipe.paths import next_staged_file_path
        from tumblehead.config.timeline import get_frame_range
        from tumblehead.farm.tasks.build import build as build_task

        output_path = next_staged_file_path(shot_uri)
        frame_range = get_frame_range(shot_uri)
        render_range = frame_range.full_range() if frame_range else None

        if render_range is None:
            raise RuntimeError(f"No frame range found for shot: {shot_uri}")

        result = build_task.main(shot_uri, output_path, render_range)
        if result != 0:
            raise RuntimeError(f"Build failed with exit code {result}")

    def _on_save_completed(self, results: dict):
        """Handle completion of save process."""
        # Clear pending changes
        self._pending_changes.clear()
        self._entity_view._scene_delegate.clear_pending_changes()

        # Refresh UI
        self._load_scenes()
        self._entity_view.refresh()
        self._update_buttons()

    def closeEvent(self, event):
        """Handle window close."""
        if not self._check_unsaved_changes():
            event.ignore()
            return

        self.window_closed.emit()
        event.accept()
