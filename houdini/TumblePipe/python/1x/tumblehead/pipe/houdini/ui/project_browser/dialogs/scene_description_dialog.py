"""
Scene Description Editor dialog for shots.

Allows editing which assets compose a shot's scene.
When saved, updates the scene property and generates a new root department version.
"""

from qtpy import QtWidgets
from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QStandardItemModel, QStandardItem

from tumblehead.util.uri import Uri
from tumblehead.config.scene import get_scene, set_scene, list_available_assets
from tumblehead.config.scenes import AssetEntry, DEFAULT_VARIANT
from tumblehead.config.variants import list_variants, refresh_cache as refresh_variants_cache


class AvailableAssetsModel(QStandardItemModel):
    """Model for available assets (not yet in scene)."""

    def __init__(self, parent=None):
        super().__init__(parent)

    def load_assets(self, all_assets: list[Uri], scene_assets: set[str]):
        """
        Load available assets, excluding those already in scene.

        Args:
            all_assets: List of all asset URIs
            scene_assets: Set of asset URI strings already in scene
        """
        self.clear()

        # Group by category
        grouped = {}
        for asset_uri in all_assets:
            uri_str = str(asset_uri)
            if uri_str in scene_assets:
                continue

            # Expected format: entity:/assets/CATEGORY/ASSET_NAME
            if len(asset_uri.segments) >= 3:
                category = asset_uri.segments[1]  # e.g., "CHAR", "ENV"
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


class SceneAssetsTable(QtWidgets.QTableWidget):
    """Table widget for assets in the scene with instances and variant editing."""

    # Custom role for storing URI string
    URI_ROLE = Qt.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(['Asset', 'Instances', 'Variant'])
        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        if header.count() >= 3:
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.Interactive)
            header.setSectionResizeMode(2, QtWidgets.QHeaderView.Interactive)
        self.setColumnWidth(1, 80)
        self.setColumnWidth(2, 120)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.verticalHeader().setVisible(False)

        # Store asset data: {uri_str: AssetEntry}
        self._asset_data: dict[str, AssetEntry] = {}

    def load_assets(self, assets: dict[str, AssetEntry]):
        """
        Load scene assets into table.

        Args:
            assets: Dict of asset URI string -> AssetEntry
        """
        self.setRowCount(0)
        self._asset_data = {}

        for uri_str, entry in sorted(assets.items()):
            self._add_row(uri_str, entry)

    def _add_row(self, uri_str: str, entry: AssetEntry):
        """Add a row to the table for an asset."""
        uri = Uri.parse_unsafe(uri_str)
        if len(uri.segments) < 3:
            return

        row = self.rowCount()
        self.insertRow(row)

        # Asset name (read-only)
        asset_name = '/'.join(uri.segments[1:])  # e.g., "CHAR/Hero"
        name_item = QtWidgets.QTableWidgetItem(asset_name)
        name_item.setData(self.URI_ROLE, uri_str)
        name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
        self.setItem(row, 0, name_item)

        # Instances (spin box)
        spin = QtWidgets.QSpinBox()
        spin.setMinimum(1)
        spin.setMaximum(100)
        spin.setValue(entry.instances)
        spin.valueChanged.connect(lambda v, u=uri_str: self._on_instances_changed(u, v))
        self.setCellWidget(row, 1, spin)

        # Variant (combo box)
        combo = QtWidgets.QComboBox()
        refresh_variants_cache()  # Pick up changes from Database Editor
        variants = list_variants(uri)
        if not variants:
            variants = [DEFAULT_VARIANT]
        combo.addItems(variants)
        if entry.variant in variants:
            combo.setCurrentText(entry.variant)
        else:
            combo.setCurrentText(DEFAULT_VARIANT)
        combo.currentTextChanged.connect(lambda v, u=uri_str: self._on_variant_changed(u, v))
        self.setCellWidget(row, 2, combo)

        # Store in data dict
        self._asset_data[uri_str] = entry

    def _on_instances_changed(self, uri_str: str, value: int):
        """Handle instances spin box change."""
        if uri_str in self._asset_data:
            old_entry = self._asset_data[uri_str]
            self._asset_data[uri_str] = AssetEntry(
                instances=value,
                variant=old_entry.variant
            )

    def _on_variant_changed(self, uri_str: str, value: str):
        """Handle variant combo box change."""
        if uri_str in self._asset_data:
            old_entry = self._asset_data[uri_str]
            self._asset_data[uri_str] = AssetEntry(
                instances=old_entry.instances,
                variant=value
            )

    def get_all_assets(self) -> dict[str, AssetEntry]:
        """
        Get all assets with their entries.

        Returns:
            Dict of asset URI string -> AssetEntry
        """
        return dict(self._asset_data)

    def get_all_asset_uris(self) -> list[str]:
        """
        Get all asset URI strings.

        Returns:
            List of asset URI strings
        """
        return list(self._asset_data.keys())

    def add_asset(self, uri_str: str, entry: AssetEntry = None):
        """
        Add an asset to the table.

        Args:
            uri_str: Asset URI string
            entry: AssetEntry (default: instances=1, variant='default')
        """
        if uri_str in self._asset_data:
            return  # Already exists

        if entry is None:
            entry = AssetEntry(instances=1, variant=DEFAULT_VARIANT)

        self._add_row(uri_str, entry)

    def remove_selected_assets(self) -> list[str]:
        """
        Remove selected assets from the table.

        Returns:
            List of removed asset URI strings
        """
        removed = []
        rows_to_remove = set()

        for item in self.selectedItems():
            if item.column() == 0:  # Only process name column items
                uri_str = item.data(self.URI_ROLE)
                if uri_str:
                    removed.append(uri_str)
                    rows_to_remove.add(item.row())
                    if uri_str in self._asset_data:
                        del self._asset_data[uri_str]

        # Remove rows in reverse order to maintain indices
        for row in sorted(rows_to_remove, reverse=True):
            self.removeRow(row)

        return removed

    def get_selected_uris(self) -> list[str]:
        """Get selected asset URIs."""
        selected = []
        for item in self.selectedItems():
            if item.column() == 0:
                uri_str = item.data(self.URI_ROLE)
                if uri_str:
                    selected.append(uri_str)
        return selected


class SceneDescriptionDialog(QtWidgets.QDialog):
    """Dialog for editing shot scene description."""

    scene_saved = Signal(object)  # Emits shot_uri when saved

    def __init__(self, api, shot_uri: Uri, parent=None):
        super().__init__(parent)
        self.api = api
        self.shot_uri = shot_uri

        # Create window title from shot path
        shot_name = '/'.join(shot_uri.segments[1:])
        self.setWindowTitle(f"Scene Description - {shot_name}")
        self.resize(900, 500)

        self.available_model = AvailableAssetsModel()
        self.scene_table = SceneAssetsTable()

        self._create_ui()
        self._load_data()

    def _create_ui(self):
        """Create the dialog UI."""
        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)

        # Info label
        info_label = QtWidgets.QLabel(
            "Select assets to include in this shot's scene. "
            "Saving will generate a new root department version."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Main content - two panels with transfer buttons
        content_layout = QtWidgets.QHBoxLayout()

        # Available assets panel
        available_panel = self._create_available_panel()
        content_layout.addWidget(available_panel, stretch=1)

        # Transfer buttons
        transfer_panel = self._create_transfer_buttons()
        content_layout.addWidget(transfer_panel)

        # Scene assets panel
        scene_panel = self._create_scene_panel()
        content_layout.addWidget(scene_panel, stretch=1)

        layout.addLayout(content_layout, stretch=1)

        # Dialog buttons
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        self.save_button = button_box.button(QtWidgets.QDialogButtonBox.Save)
        button_box.accepted.connect(self._save_scene)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _create_available_panel(self):
        """Create available assets panel with tree view."""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        panel.setLayout(layout)

        label = QtWidgets.QLabel("Available Assets")
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)

        self.available_tree = QtWidgets.QTreeView()
        self.available_tree.setModel(self.available_model)
        self.available_tree.setHeaderHidden(True)
        self.available_tree.setSelectionMode(
            QtWidgets.QAbstractItemView.ExtendedSelection
        )
        self.available_tree.doubleClicked.connect(self._on_available_double_click)
        layout.addWidget(self.available_tree)

        return panel

    def _create_transfer_buttons(self):
        """Create add/remove buttons panel."""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        panel.setLayout(layout)

        layout.addStretch()

        add_button = QtWidgets.QPushButton("Add ->")
        add_button.clicked.connect(self._add_to_scene)
        layout.addWidget(add_button)

        remove_button = QtWidgets.QPushButton("<- Remove")
        remove_button.clicked.connect(self._remove_from_scene)
        layout.addWidget(remove_button)

        layout.addStretch()

        return panel

    def _create_scene_panel(self):
        """Create scene assets panel with table."""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        panel.setLayout(layout)

        label = QtWidgets.QLabel("Scene Assets (instances & variant)")
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)

        self.scene_table.doubleClicked.connect(self._on_scene_double_click)
        layout.addWidget(self.scene_table)

        return panel

    def _load_data(self):
        """Load current scene description and available assets."""
        # Get all available assets
        all_assets = list_available_assets()

        # Get current scene - assets is dict[str, AssetEntry]
        scene = get_scene(self.shot_uri)
        scene_asset_strs = set(scene.assets.keys())

        # Load models
        self.available_model.load_assets(all_assets, scene_asset_strs)
        self.scene_table.load_assets(scene.assets)

        # Expand available tree
        self.available_tree.expandAll()

    def _get_selected_available_assets(self) -> list[str]:
        """Get selected asset URIs from available tree."""
        selected = []
        for index in self.available_tree.selectedIndexes():
            item = self.available_model.itemFromIndex(index)
            if item:
                uri_str = item.data(Qt.UserRole)
                if uri_str:
                    selected.append(uri_str)
        return selected

    def _get_selected_scene_assets(self) -> list[str]:
        """Get selected asset URIs from scene table."""
        return self.scene_table.get_selected_uris()

    def _get_expanded_items(self):
        """Get set of expanded category names"""
        expanded = set()
        for row in range(self.available_model.rowCount()):
            index = self.available_model.index(row, 0)
            item = self.available_model.itemFromIndex(index)
            if item and self.available_tree.isExpanded(index):
                expanded.add(item.text())
        return expanded

    def _restore_expanded_items(self, expanded_names):
        """Restore expansion state for categories by name"""
        for row in range(self.available_model.rowCount()):
            index = self.available_model.index(row, 0)
            item = self.available_model.itemFromIndex(index)
            if item and item.text() in expanded_names:
                self.available_tree.expand(index)

    def _add_to_scene(self):
        """Add selected available assets to scene."""
        selected = self._get_selected_available_assets()
        for uri_str in selected:
            self.scene_table.add_asset(uri_str)
        self._refresh_available()

    def _remove_from_scene(self):
        """Remove selected assets from scene."""
        self.scene_table.remove_selected_assets()
        self._refresh_available()

    def _on_available_double_click(self, index):
        """Handle double-click on available asset."""
        item = self.available_model.itemFromIndex(index)
        if item:
            uri_str = item.data(Qt.UserRole)
            if uri_str:
                self.scene_table.add_asset(uri_str)
                self._refresh_available()

    def _on_scene_double_click(self, index):
        """Handle double-click on scene asset - remove it."""
        # Get URI from the row
        row = index.row()
        item = self.scene_table.item(row, 0)
        if item:
            uri_str = item.data(SceneAssetsTable.URI_ROLE)
            if uri_str:
                # Select and remove
                self.scene_table.selectRow(row)
                self.scene_table.remove_selected_assets()
                self._refresh_available()

    def _refresh_available(self):
        """Refresh available assets model after changes."""
        # Preserve tree state before refresh
        expanded = self._get_expanded_items()
        scroll_pos = self.available_tree.verticalScrollBar().value()

        current_scene = set(self.scene_table.get_all_asset_uris())
        all_assets = list_available_assets()
        self.available_model.load_assets(all_assets, current_scene)

        # Restore tree state after refresh
        self._restore_expanded_items(expanded)
        self.available_tree.verticalScrollBar().setValue(scroll_pos)

    def _save_scene(self):
        """Save scene description and generate root department version."""
        try:
            # Get assets from scene table: {uri_str: AssetEntry}
            assets_dict = self.scene_table.get_all_assets()

            # Convert to {Uri: AssetEntry} for set_scene
            assets = {Uri.parse_unsafe(uri_str): entry for uri_str, entry in assets_dict.items()}

            # Save to config
            set_scene(self.shot_uri, assets)

            # Generate new root department version if there are assets
            if len(assets) > 0:
                from tumblehead.config.scene import generate_root_version
                output_path = generate_root_version(self.shot_uri)

                QtWidgets.QMessageBox.information(
                    self,
                    "Scene Saved",
                    f"Scene saved and root department generated:\n{output_path}"
                )
            else:
                QtWidgets.QMessageBox.information(
                    self,
                    "Scene Saved",
                    "Scene saved (no assets, root department not generated)."
                )

            self.scene_saved.emit(self.shot_uri)
            self.accept()

        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                "Error",
                f"Error saving scene: {str(e)}"
            )


class MemberListModel(QStandardItemModel):
    """Model for group member shots."""

    def __init__(self, parent=None):
        super().__init__(parent)

    def load_members(self, members: list):
        """
        Load group members into model.

        Args:
            members: List of member URIs
        """
        self.clear()
        for member_uri in members:
            # Display as "seq/shot" (e.g., "010/010")
            name = '/'.join(member_uri.segments[1:])
            item = QStandardItem(name)
            item.setData(member_uri, Qt.UserRole)
            item.setEditable(False)
            self.appendRow(item)


class GroupSceneDescriptionDialog(QtWidgets.QDialog):
    """Dialog for editing scene descriptions for group members."""

    scene_saved = Signal(object)  # Emits group_uri when any member saved

    def __init__(self, api, group_uri: Uri, parent=None):
        super().__init__(parent)
        self.api = api
        self.group_uri = group_uri
        self.current_member = None  # Currently selected member shot URI

        # Get group and members
        from tumblehead.config.groups import get_group
        self.group = get_group(group_uri)

        if self.group is None:
            raise ValueError(f"Group not found: {group_uri}")

        # Create window title from group name
        group_name = self.group.name
        member_count = len(self.group.members)
        self.setWindowTitle(f"Scene Description - Group: {group_name} ({member_count} shots)")
        self.resize(1100, 500)

        self.member_model = MemberListModel()
        self.available_model = AvailableAssetsModel()
        self.scene_table = SceneAssetsTable()

        self._create_ui()
        self._load_members()

    def _create_ui(self):
        """Create the dialog UI."""
        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)

        # Info label
        info_label = QtWidgets.QLabel(
            "Select a member to edit its scene description. "
            "Saving will generate a new root department version for the selected member."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Main content - four panels
        content_layout = QtWidgets.QHBoxLayout()

        # Member list panel
        member_panel = self._create_member_panel()
        content_layout.addWidget(member_panel, stretch=1)

        # Available assets panel
        available_panel = self._create_available_panel()
        content_layout.addWidget(available_panel, stretch=1)

        # Transfer buttons
        transfer_panel = self._create_transfer_buttons()
        content_layout.addWidget(transfer_panel)

        # Scene assets panel
        scene_panel = self._create_scene_panel()
        content_layout.addWidget(scene_panel, stretch=1)

        layout.addLayout(content_layout, stretch=1)

        # Dialog buttons
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        self.save_button = button_box.button(QtWidgets.QDialogButtonBox.Save)
        self.save_button.setEnabled(False)  # Disabled until member selected
        button_box.accepted.connect(self._save_scene)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _create_member_panel(self):
        """Create member list panel."""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        panel.setLayout(layout)

        label = QtWidgets.QLabel("Group Members")
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)

        self.member_list = QtWidgets.QListView()
        self.member_list.setModel(self.member_model)
        self.member_list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.member_list.selectionModel().selectionChanged.connect(self._on_member_selected)
        layout.addWidget(self.member_list)

        return panel

    def _create_available_panel(self):
        """Create available assets panel with tree view."""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        panel.setLayout(layout)

        label = QtWidgets.QLabel("Available Assets")
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)

        self.available_tree = QtWidgets.QTreeView()
        self.available_tree.setModel(self.available_model)
        self.available_tree.setHeaderHidden(True)
        self.available_tree.setSelectionMode(
            QtWidgets.QAbstractItemView.ExtendedSelection
        )
        self.available_tree.doubleClicked.connect(self._on_available_double_click)
        layout.addWidget(self.available_tree)

        return panel

    def _create_transfer_buttons(self):
        """Create add/remove buttons panel."""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        panel.setLayout(layout)

        layout.addStretch()

        self.add_button = QtWidgets.QPushButton("Add ->")
        self.add_button.clicked.connect(self._add_to_scene)
        self.add_button.setEnabled(False)
        layout.addWidget(self.add_button)

        self.remove_button = QtWidgets.QPushButton("<- Remove")
        self.remove_button.clicked.connect(self._remove_from_scene)
        self.remove_button.setEnabled(False)
        layout.addWidget(self.remove_button)

        layout.addStretch()

        return panel

    def _create_scene_panel(self):
        """Create scene assets panel with table."""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        panel.setLayout(layout)

        label = QtWidgets.QLabel("Scene Assets (instances & variant)")
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)

        self.scene_table.doubleClicked.connect(self._on_scene_double_click)
        layout.addWidget(self.scene_table)

        return panel

    def _load_members(self):
        """Load group members into the member list."""
        self.member_model.load_members(self.group.members)

    def _on_member_selected(self, selected, deselected):
        """Handle member selection change."""
        indexes = self.member_list.selectedIndexes()
        if not indexes:
            self.current_member = None
            self.save_button.setEnabled(False)
            self.add_button.setEnabled(False)
            self.remove_button.setEnabled(False)
            self.available_model.clear()
            self.scene_table.setRowCount(0)
            self.scene_table._asset_data.clear()
            return

        # Get selected member URI
        item = self.member_model.itemFromIndex(indexes[0])
        self.current_member = item.data(Qt.UserRole)

        # Enable buttons
        self.save_button.setEnabled(True)
        self.add_button.setEnabled(True)
        self.remove_button.setEnabled(True)

        # Load scene data for selected member
        self._load_member_scene()

    def _load_member_scene(self):
        """Load scene data for the currently selected member."""
        if self.current_member is None:
            return

        # Get all available assets
        all_assets = list_available_assets()

        # Get current scene for this member - assets is dict[str, AssetEntry]
        scene = get_scene(self.current_member)
        scene_asset_strs = set(scene.assets.keys())

        # Load models
        self.available_model.load_assets(all_assets, scene_asset_strs)
        self.scene_table.load_assets(scene.assets)

        # Expand available tree
        self.available_tree.expandAll()

    def _get_selected_available_assets(self) -> list:
        """Get selected asset URIs from available tree."""
        selected = []
        for index in self.available_tree.selectedIndexes():
            item = self.available_model.itemFromIndex(index)
            if item:
                uri_str = item.data(Qt.UserRole)
                if uri_str:
                    selected.append(uri_str)
        return selected

    def _get_selected_scene_assets(self) -> list:
        """Get selected asset URIs from scene table."""
        return self.scene_table.get_selected_uris()

    def _get_expanded_items(self):
        """Get set of expanded category names"""
        expanded = set()
        for row in range(self.available_model.rowCount()):
            index = self.available_model.index(row, 0)
            item = self.available_model.itemFromIndex(index)
            if item and self.available_tree.isExpanded(index):
                expanded.add(item.text())
        return expanded

    def _restore_expanded_items(self, expanded_names):
        """Restore expansion state for categories by name"""
        for row in range(self.available_model.rowCount()):
            index = self.available_model.index(row, 0)
            item = self.available_model.itemFromIndex(index)
            if item and item.text() in expanded_names:
                self.available_tree.expand(index)

    def _add_to_scene(self):
        """Add selected available assets to scene."""
        if self.current_member is None:
            return
        selected = self._get_selected_available_assets()
        for uri_str in selected:
            self.scene_table.add_asset(uri_str)
        self._refresh_available()

    def _remove_from_scene(self):
        """Remove selected assets from scene."""
        if self.current_member is None:
            return
        self.scene_table.remove_selected_assets()
        self._refresh_available()

    def _on_available_double_click(self, index):
        """Handle double-click on available asset."""
        if self.current_member is None:
            return
        item = self.available_model.itemFromIndex(index)
        if item:
            uri_str = item.data(Qt.UserRole)
            if uri_str:
                self.scene_table.add_asset(uri_str)
                self._refresh_available()

    def _on_scene_double_click(self, index):
        """Handle double-click on scene asset - remove it."""
        if self.current_member is None:
            return
        # Get URI from the row
        row = index.row()
        item = self.scene_table.item(row, 0)
        if item:
            uri_str = item.data(SceneAssetsTable.URI_ROLE)
            if uri_str:
                # Select and remove
                self.scene_table.selectRow(row)
                self.scene_table.remove_selected_assets()
                self._refresh_available()

    def _refresh_available(self):
        """Refresh available assets model after changes."""
        # Preserve tree state before refresh
        expanded = self._get_expanded_items()
        scroll_pos = self.available_tree.verticalScrollBar().value()

        current_scene = set(self.scene_table.get_all_asset_uris())
        all_assets = list_available_assets()
        self.available_model.load_assets(all_assets, current_scene)

        # Restore tree state after refresh
        self._restore_expanded_items(expanded)
        self.available_tree.verticalScrollBar().setValue(scroll_pos)

    def _save_scene(self):
        """Save scene description for current member and generate root department version."""
        if self.current_member is None:
            QtWidgets.QMessageBox.warning(
                self,
                "No Member Selected",
                "Please select a member shot to save."
            )
            return

        try:
            # Get assets from scene table: {uri_str: AssetEntry}
            assets_dict = self.scene_table.get_all_assets()

            # Convert to {Uri: AssetEntry} for set_scene
            assets = {Uri.parse_unsafe(uri_str): entry for uri_str, entry in assets_dict.items()}

            # Save to config
            set_scene(self.current_member, assets)

            # Generate new root department version if there are assets
            if len(assets) > 0:
                from tumblehead.config.scene import generate_root_version
                output_path = generate_root_version(self.current_member)

                QtWidgets.QMessageBox.information(
                    self,
                    "Scene Saved",
                    f"Scene saved and root department generated:\n{output_path}"
                )
            else:
                QtWidgets.QMessageBox.information(
                    self,
                    "Scene Saved",
                    "Scene saved (no assets, root department not generated)."
                )

            self.scene_saved.emit(self.group_uri)
            # Don't close dialog - allow editing other members

        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                "Error",
                f"Error saving scene: {str(e)}"
            )
