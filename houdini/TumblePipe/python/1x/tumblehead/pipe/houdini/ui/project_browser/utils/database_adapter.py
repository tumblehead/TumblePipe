from copy import deepcopy

from tumblehead.util.io import store_json
from tumblehead.util.uri import Uri


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dicts. Override values take precedence.

    For nested dicts, merge recursively. For other types, override replaces base.
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class DatabaseAdapter:
    """Adapter providing database_editor compatible interface over ConfigConvention"""

    def __init__(self, api):
        self._api = api
        self._config = api.config

    def list_purposes(self) -> list[str]:
        """List all available purposes from the config cache"""
        return list(self._config.cache.keys())

    def list_entities(self, root_uri: Uri) -> list[Uri]:
        """List all leaf entities under a root URI"""
        purpose = root_uri.purpose
        if purpose not in self._config.cache:
            return []

        def _collect(data, current_uri):
            result = []
            children = data.get('children', {})
            if not children:
                result.append(current_uri)
            else:
                for name, child_data in children.items():
                    child_uri = current_uri / name
                    result.extend(_collect(child_data, child_uri))
            return result

        data = self._config.cache[purpose]
        base_uri = Uri.parse_unsafe(f"{purpose}:/")
        return _collect(data, base_uri)

    def lookup(self, uri: Uri) -> dict | None:
        """Get the raw data for a URI (full node including properties and children)"""
        data = self._config.cache.get(uri.purpose)
        if data is None:
            return None
        for segment in uri.segments:
            children = data.get('children', {})
            if segment not in children:
                return None
            data = children[segment]
        return data

    def lookup_root(self, purpose: str) -> dict | None:
        """Get the raw data for a purpose root"""
        return self._config.cache.get(purpose)

    def save(self, uri: Uri, data: dict) -> None:
        """Save changes to a URI by updating cache and persisting to file"""
        purpose = uri.purpose
        cache_data = self._config.cache.get(purpose)

        if cache_data is None:
            cache_data = {'properties': {}, 'children': {}}

        if not uri.segments:
            cache_data = data
        else:
            current = cache_data
            for segment in uri.segments[:-1]:
                children = current.setdefault('children', {})
                if segment not in children:
                    children[segment] = {'properties': {}, 'children': {}}
                current = children[segment]
            current['children'][uri.segments[-1]] = data

        self._config.cache[purpose] = cache_data
        store_json(self._config.db_path / f'{purpose}.json', cache_data)

    def save_root(self, purpose: str, data: dict) -> None:
        """Save changes to a purpose root"""
        self._config.cache[purpose] = data
        store_json(self._config.db_path / f'{purpose}.json', data)

    def lookup_properties(self, uri: Uri) -> dict:
        """Get just the properties for a URI"""
        data = self.lookup(uri)
        if data is None:
            return {}
        return data.get('properties', {})

    def lookup_root_properties(self, purpose: str) -> dict:
        """Get just the properties for a purpose root"""
        data = self.lookup_root(purpose)
        if data is None:
            return {}
        return data.get('properties', {})

    def get_root_inherited_properties(self, purpose: str) -> dict:
        """Get inherited properties for a root entity (schema defaults).

        Root entities like entity:/ have their schema at schemas:/{purpose}.
        """
        schema_uri = Uri.parse_unsafe(f'schemas:/{purpose}')
        return self._get_schema_properties(schema_uri)

    def get_inherited_properties(self, uri: Uri) -> dict:
        """Get properties to compare against (schema defaults + parent inheritance).

        For comparison purposes:
        - Schema defaults define the baseline structure and field order
        - Parent inheritance provides inherited property values (render, farm, etc.)

        For a URI like entity:/assets/CHAR/Chair with schema schemas:/entity/assets/category/asset,
        this returns merged properties from:
        1. schemas:/entity/assets/category/asset (schema defaults - defines order)
        2. entity:/ -> entity:/assets -> entity:/assets/CHAR (parent inheritance)
        """
        # 1. Start with schema defaults (defines base structure and order)
        entity_data = self.lookup(uri)
        if entity_data and entity_data.get('schema'):
            # Entity has explicit schema - use it
            schema_uri = Uri.parse_unsafe(entity_data['schema'])
        else:
            # No explicit schema - derive from URI path
            # e.g., entity:/assets -> schemas:/entity/assets
            schema_uri = Uri.parse_unsafe(f'schemas:/{uri.purpose}/' + '/'.join(uri.segments))

        result = self._get_schema_properties(schema_uri)

        # 2. Merge parent entity inheritance on top
        if uri.segments:
            purpose = uri.purpose
            cache_data = self._config.cache.get(purpose)
            if cache_data:
                # Merge root properties
                result = _deep_merge(result, deepcopy(cache_data.get('properties', {})))

                # Merge from root to parent (stop before last segment)
                data = cache_data
                for segment in uri.segments[:-1]:
                    children = data.get('children', {})
                    if segment not in children:
                        break
                    data = children[segment]
                    result = _deep_merge(result, data.get('properties', {}))

        return result

    def _get_schema_properties(self, schema_uri: Uri) -> dict:
        """Get properties from a schema URI with full hierarchy inheritance."""
        # Use config's get_properties which walks full schema hierarchy
        props = self._config.get_properties(schema_uri)
        return props if props else {}

    def save_properties(self, uri: Uri, properties: dict) -> None:
        """Save just the properties for a URI, preserving children"""
        if uri.segments:
            data = self.lookup(uri) or {'properties': {}, 'children': {}}
        else:
            data = self.lookup_root(uri.purpose) or {'properties': {}, 'children': {}}
        data['properties'] = properties
        if uri.segments:
            self.save(uri, data)
        else:
            self.save_root(uri.purpose, data)

    def transact(self, uri: Uri) -> 'DatabaseTransaction':
        """Create a transaction context for applying changes"""
        return DatabaseTransaction(self, uri)


class DatabaseTransaction:
    """Transaction wrapper for applying changes to ConfigConvention"""

    def __init__(self, adapter: DatabaseAdapter, uri: Uri):
        self._adapter = adapter
        self._uri = uri
        self._original_data = None
        self._working_data = None

    def __enter__(self):
        if self._uri.segments:
            self._original_data = self._adapter.lookup(self._uri)
        else:
            self._original_data = self._adapter.lookup_root(self._uri.purpose)
        self._working_data = deepcopy(self._original_data) if self._original_data else {}
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            if self._uri.segments:
                self._adapter.save(self._uri, self._working_data)
            else:
                self._adapter.save_root(self._uri.purpose, self._working_data)

    def _navigate_path(self, path):
        """Navigate to the parent of the target location and return (parent, key/index)"""
        parts = _json_path_parts(path)
        current = self._working_data
        for part in parts[:-1]:
            if isinstance(part, int):
                current = current[part]
            else:
                current = current[part]
        return current, parts[-1] if parts else None

    def array_insert(self, path, index, value):
        parts = _json_path_parts(path)
        current = self._working_data
        for part in parts:
            if isinstance(part, int):
                current = current[part]
            else:
                current = current[part]
        current.insert(index, value)

    def array_update(self, path, index, value):
        parts = _json_path_parts(path)
        current = self._working_data
        for part in parts:
            if isinstance(part, int):
                current = current[part]
            else:
                current = current[part]
        current[index] = value

    def array_reorder(self, path, from_index, to_index, from_value, to_value):
        parts = _json_path_parts(path)
        current = self._working_data
        for part in parts:
            if isinstance(part, int):
                current = current[part]
            else:
                current = current[part]
        current[from_index] = to_value
        current[to_index] = from_value

    def array_remove(self, path, index):
        parts = _json_path_parts(path)
        current = self._working_data
        for part in parts:
            if isinstance(part, int):
                current = current[part]
            else:
                current = current[part]
        del current[index]

    def object_insert(self, path, key, value):
        parts = _json_path_parts(path)
        current = self._working_data
        for part in parts:
            if isinstance(part, int):
                current = current[part]
            else:
                current = current[part]
        current[key] = value

    def object_update(self, path, key, value):
        parts = _json_path_parts(path)
        current = self._working_data
        for part in parts:
            if isinstance(part, int):
                current = current[part]
            else:
                current = current[part]
        current[key] = value

    def object_rename(self, path, old_key, new_key):
        parts = _json_path_parts(path)
        current = self._working_data
        for part in parts:
            if isinstance(part, int):
                current = current[part]
            else:
                current = current[part]
        current[new_key] = current.pop(old_key)

    def object_remove(self, path, key):
        parts = _json_path_parts(path)
        current = self._working_data
        for part in parts:
            if isinstance(part, int):
                current = current[part]
            else:
                current = current[part]
        del current[key]


def _json_path_parts(path) -> list[int | str]:
    """Convert JsonPath to list of keys/indices"""
    from ..views.json_editor import JsonPathRoot, JsonPathField, JsonPathIndex

    parts = []
    while True:
        if isinstance(path, JsonPathRoot):
            return list(reversed(parts))
        elif isinstance(path, JsonPathIndex):
            parts.append(path.index)
            path = path.path
        elif isinstance(path, JsonPathField):
            parts.append(path.key)
            path = path.path
        else:
            raise ValueError(f"Invalid path type: {type(path)}")
