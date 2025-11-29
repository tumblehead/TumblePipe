from qtpy.QtGui import QStandardItemModel, QStandardItem
from qtpy.QtCore import Qt

from tumblehead.util.uri import Uri


class GroupListModel(QStandardItemModel):
    """Model for displaying list of entity groups"""

    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api

    def load_groups(self):
        """Load all groups from configuration"""
        self.clear()
        groups = self.api.config.list_groups()

        for group in groups:
            item = QStandardItem(group.name)
            item.setData(group, Qt.UserRole)

            # Add metadata as tooltip
            member_count = len(group.members)
            dept_count = len(group.departments)

            # Derive entity type from root Uri
            entity_type = "unknown"
            if group.root:
                purpose, parts = group.root.parts()
                if purpose == "entity" and len(parts) > 0:
                    entity_type = parts[0]  # 'shots' or 'assets'

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

        # Derive entity type from root Uri
        entity_type = "unknown"
        if group.root:
            purpose, parts = group.root.parts()
            if purpose == "entity" and len(parts) > 0:
                entity_type = parts[0]  # 'shots' or 'assets'

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
    """Model for available entities (not in any group)"""

    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api

    def load_entities(self, entity_type, assigned_entities, current_group_members=None):
        """Load available entities based on type (backward compatibility wrapper)

        Args:
            entity_type: 'shot' or 'asset'
            assigned_entities: Set of entity URI strings already in other groups
            current_group_members: Set of entity URI strings in current group being edited
        """
        # Convert entity_type to root_uri for backward compatibility
        if entity_type == 'shot':
            root_uri_str = 'entity:/shots'
        elif entity_type == 'asset':
            root_uri_str = 'entity:/assets'
        else:
            root_uri_str = 'entity:/'

        self.load_entities_from_uri(root_uri_str, assigned_entities, current_group_members)

    def load_entities_from_uri(self, root_uri_str, assigned_entities, current_group_members=None):
        """Load available entities based on root URI

        Args:
            root_uri_str: Root URI string (e.g., "entity:/shots", "entity:/shots/010", "entity:/assets/characters")
            assigned_entities: Set of entity URI strings already in other groups
            current_group_members: Set of entity URI strings in current group being edited
        """
        self.clear()

        if current_group_members is None:
            current_group_members = set()

        try:
            root_uri = Uri.parse_unsafe(root_uri_str)
            if not root_uri:
                return

            # Get all entities under this root URI with closure=True
            uris = self.api.config.list_entities(root_uri, closure=True)

            for uri in uris:
                # Skip None URIs (failed to parse)
                if uri is None:
                    continue

                try:
                    parts = uri.parts()[1]  # Get tuple of parts
                except (AttributeError, TypeError, IndexError):
                    # Skip URIs that fail to parse correctly
                    continue

                # Only show leaf entities (shots/assets, not sequences/categories)
                # Shots: entity:/shots/sequence/shot (3 parts)
                # Assets: entity:/assets/category/asset (3 parts)
                if len(parts) != 3:
                    continue

                entity_type = parts[0]  # 'shots' or 'assets'
                parent_name = parts[1]  # sequence or category
                entity_name = parts[2]  # shot or asset

                # Create display text and entity string directly from URI
                if entity_type == 'shots':
                    display_text = f"{parent_name}/{entity_name}"
                    entity_string = str(uri)
                elif entity_type == 'assets':
                    display_text = f"{parent_name}/{entity_name}"
                    entity_string = str(uri)
                else:
                    continue

                # Skip if in current group members (already on right side)
                if entity_string in current_group_members:
                    continue

                # Create item
                item = QStandardItem(display_text)
                item.setData(entity_string, Qt.UserRole)

                # Disable if in another group
                if entity_string in assigned_entities:
                    item.setEnabled(False)
                    item.setForeground(Qt.gray)
                    item.setText(f"{display_text} (in another group)")

                self.appendRow(item)

        except Exception as e:
            print(f"Error loading entities from URI '{root_uri_str}': {e}")


class GroupMembersModel(QStandardItemModel):
    """Model for current group members"""

    def __init__(self, parent=None):
        super().__init__(parent)

    def load_members(self, member_entities):
        """Load member entities into the model

        Args:
            member_entities: List of entity URI strings or Entity objects
        """
        self.clear()

        for entity in member_entities:
            self.add_member(entity)

    def add_member(self, entity):
        """Add a member to the model

        Args:
            entity: Entity URI string
        """
        # Convert string to display text
        entity_string = entity if isinstance(entity, str) else str(entity)

        # Parse display text from URI
        if 'entity:/shots/' in entity_string:
            parts = entity_string.replace('entity:/shots/', '').split('/')
            display_text = f"{parts[0]}/{parts[1]}"
        elif 'entity:/assets/' in entity_string:
            parts = entity_string.replace('entity:/assets/', '').split('/')
            display_text = f"{parts[0]}/{parts[1]}"
        else:
            display_text = entity_string

        item = QStandardItem(display_text)
        item.setData(entity_string, Qt.UserRole)
        self.appendRow(item)

    def remove_members(self, indexes):
        """Remove members at given indexes

        Args:
            indexes: List of QModelIndex objects to remove
        """
        # Sort by row in reverse order to avoid index shifting
        rows = sorted([index.row() for index in indexes], reverse=True)
        for row in rows:
            self.removeRow(row)

    def get_member_entities(self):
        """Get list of entity URI strings for all members

        Returns:
            List of entity URI strings
        """
        entities = []
        for row in range(self.rowCount()):
            item = self.item(row)
            entity_string = item.data(Qt.UserRole)
            entities.append(entity_string)
        return entities
