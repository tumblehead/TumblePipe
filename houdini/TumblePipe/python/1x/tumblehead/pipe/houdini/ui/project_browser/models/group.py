from qtpy.QtGui import QStandardItemModel, QStandardItem
from qtpy.QtCore import Qt

from tumblehead.util.uri import Uri
from tumblehead.config.groups import list_groups as _list_groups
from tumblehead.config.scene import get_inherited_scene_ref


class GroupListModel(QStandardItemModel):
    """Model for displaying list of entity groups"""

    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api

    def load_groups(self):
        """Load all groups from configuration"""
        self.clear()
        shot_groups = _list_groups('shots')
        asset_groups = _list_groups('assets')
        groups = shot_groups + asset_groups

        for group in groups:
            item = QStandardItem(group.name)
            item.setData(group, Qt.UserRole)

            member_count = len(group.members)
            dept_count = len(group.departments)

            entity_type = "unknown"
            if group.root:
                purpose, parts = group.root.parts()
                if purpose == "entity" and len(parts) > 0:
                    entity_type = parts[0]

            tooltip = (
                f"Name: {group.name}\n"
                f"Type: {entity_type}\n"
                f"Members: {member_count}\n"
                f"Departments: {dept_count}"
            )
            item.setToolTip(tooltip)

            self.appendRow(item)

    def add_group_item(self, group):
        """Add a new group item to the model"""
        item = QStandardItem(group.name)
        item.setData(group, Qt.UserRole)

        member_count = len(group.members)
        dept_count = len(group.departments)

        entity_type = "unknown"
        if group.root:
            purpose, parts = group.root.parts()
            if purpose == "entity" and len(parts) > 0:
                entity_type = parts[0]

        tooltip = (
            f"Name: {group.name}\n"
            f"Type: {entity_type}\n"
            f"Members: {member_count}\n"
            f"Departments: {dept_count}"
        )
        item.setToolTip(tooltip)

        self.appendRow(item)

    def remove_group_item(self, group_name):
        """Remove a group item from the model"""
        for row in range(self.rowCount()):
            item = self.item(row)
            if item.text() == group_name:
                self.removeRow(row)
                break

    def get_group(self, index):
        """Get the EntityGroup object for a given index"""
        if not index.isValid():
            return None
        item = self.itemFromIndex(index)
        return item.data(Qt.UserRole)


class AvailableEntitiesModel(QStandardItemModel):
    """Model for available entities (not in any group) - displays as tree"""

    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api

    def load_entities(self, entity_type, assigned_entities, current_group_members=None):
        """Load available entities based on type (backward compatibility wrapper)"""
        if entity_type == 'shot':
            root_uri_str = 'entity:/shots'
        elif entity_type == 'asset':
            root_uri_str = 'entity:/assets'
        else:
            root_uri_str = 'entity:/'

        self.load_entities_from_uri(root_uri_str, assigned_entities, current_group_members)

    def load_entities_from_uri(self, root_uri_str, assigned_entities, current_group_members=None):
        """Load available entities as tree structure based on root URI"""
        self.clear()
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(['Name', 'Scene'])

        if current_group_members is None:
            current_group_members = set()

        try:
            root_uri = Uri.parse_unsafe(root_uri_str)
            if not root_uri:
                return

            entities = self.api.config.list_entities(root_uri, closure=True)
            uris = [entity.uri for entity in entities]

            grouped = {}

            for uri in uris:
                if uri is None:
                    continue

                try:
                    parts = uri.parts()[1]
                except (AttributeError, TypeError, IndexError):
                    continue

                if len(parts) != 3:
                    continue

                entity_type = parts[0]
                parent_name = parts[1]
                entity_name = parts[2]
                uri_str = str(uri)

                if uri_str in current_group_members:
                    continue

                if uri_str in assigned_entities:
                    continue

                if parent_name not in grouped:
                    grouped[parent_name] = []
                grouped[parent_name].append((entity_name, uri_str, entity_type))

            for parent_name in sorted(grouped.keys()):
                parent_item = QStandardItem(parent_name)
                parent_item.setSelectable(True)
                parent_item.setEditable(False)

                for entity_name, uri_str, entity_type in sorted(grouped[parent_name], key=lambda x: x[0]):
                    child_item = QStandardItem(entity_name)
                    child_item.setData(uri_str, Qt.UserRole)
                    child_item.setEditable(False)

                    # Look up scene for shots
                    scene_item = QStandardItem('')
                    scene_item.setEditable(False)
                    if entity_type == 'shots':
                        entity_uri = Uri.parse_unsafe(uri_str)
                        scene_ref, _ = get_inherited_scene_ref(entity_uri)
                        if scene_ref and scene_ref.segments:
                            scene_name = scene_ref.segments[-1]
                            scene_item.setText(scene_name)

                    parent_item.appendRow([child_item, scene_item])

                self.appendRow([parent_item, QStandardItem('')])

        except Exception as e:
            print(f"Error loading entities from URI '{root_uri_str}': {e}")


class GroupMembersModel(QStandardItemModel):
    """Model for current group members - displays as tree"""

    def __init__(self, parent=None):
        super().__init__(parent)

    def load_members(self, member_entities):
        """Load member entities into the model as tree structure"""
        self.clear()

        grouped = {}

        for entity in member_entities:
            uri_str = entity if isinstance(entity, str) else str(entity)

            parent_name = None
            entity_name = None

            if 'entity:/shots/' in uri_str:
                parts = uri_str.replace('entity:/shots/', '').split('/')
                if len(parts) >= 2:
                    parent_name = parts[0]
                    entity_name = parts[1]
            elif 'entity:/assets/' in uri_str:
                parts = uri_str.replace('entity:/assets/', '').split('/')
                if len(parts) >= 2:
                    parent_name = parts[0]
                    entity_name = parts[1]

            if parent_name and entity_name:
                if parent_name not in grouped:
                    grouped[parent_name] = []
                grouped[parent_name].append((entity_name, uri_str))

        for parent_name in sorted(grouped.keys()):
            parent_item = QStandardItem(parent_name)
            parent_item.setSelectable(True)
            parent_item.setEditable(False)

            for entity_name, uri_str in sorted(grouped[parent_name], key=lambda x: x[0]):
                child_item = QStandardItem(entity_name)
                child_item.setData(uri_str, Qt.UserRole)
                child_item.setEditable(False)
                parent_item.appendRow(child_item)

            self.appendRow(parent_item)

    def add_member(self, uri_str):
        """Add a member to the model, maintaining tree structure"""
        parent_name = None
        entity_name = None

        if 'entity:/shots/' in uri_str:
            parts = uri_str.replace('entity:/shots/', '').split('/')
            if len(parts) >= 2:
                parent_name = parts[0]
                entity_name = parts[1]
        elif 'entity:/assets/' in uri_str:
            parts = uri_str.replace('entity:/assets/', '').split('/')
            if len(parts) >= 2:
                parent_name = parts[0]
                entity_name = parts[1]

        if not parent_name or not entity_name:
            return

        parent_item = None
        for row in range(self.rowCount()):
            item = self.item(row)
            if item.text() == parent_name:
                parent_item = item
                break

        if parent_item is None:
            parent_item = QStandardItem(parent_name)
            parent_item.setSelectable(True)
            parent_item.setEditable(False)

            inserted = False
            for row in range(self.rowCount()):
                if self.item(row).text() > parent_name:
                    self.insertRow(row, parent_item)
                    inserted = True
                    break
            if not inserted:
                self.appendRow(parent_item)

        child_item = QStandardItem(entity_name)
        child_item.setData(uri_str, Qt.UserRole)
        child_item.setEditable(False)

        inserted = False
        for row in range(parent_item.rowCount()):
            if parent_item.child(row).text() > entity_name:
                parent_item.insertRow(row, child_item)
                inserted = True
                break
        if not inserted:
            parent_item.appendRow(child_item)

    def remove_members(self, indexes):
        """Remove members at given indexes, cleaning up empty parents"""
        items_to_remove = []
        for index in indexes:
            item = self.itemFromIndex(index)
            if item and item.data(Qt.UserRole):
                items_to_remove.append((index.parent(), index.row(), item.data(Qt.UserRole)))

        items_to_remove.sort(key=lambda x: x[1], reverse=True)

        parents_to_check = set()

        for parent_index, row, uri_str in items_to_remove:
            if parent_index.isValid():
                parent_item = self.itemFromIndex(parent_index)
                if parent_item:
                    parent_item.removeRow(row)
                    parents_to_check.add(parent_index)

        for parent_index in parents_to_check:
            parent_item = self.itemFromIndex(parent_index)
            if parent_item and parent_item.rowCount() == 0:
                self.removeRow(parent_index.row())

    def get_member_entities(self):
        """Get list of entity URI strings for all members (leaf nodes only)"""
        entities = []
        for row in range(self.rowCount()):
            parent_item = self.item(row)
            for child_row in range(parent_item.rowCount()):
                child_item = parent_item.child(child_row)
                uri_str = child_item.data(Qt.UserRole)
                if uri_str:
                    entities.append(uri_str)
        return entities
