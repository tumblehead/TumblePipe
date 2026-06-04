from tumblepipe.api import get_config_path
from tumblepipe.config import ConfigConvention, Entity
from tumblepipe.config.schema import Schema, schema_from_properties
from tumblepipe.util.io import load_json, store_json
from tumblepipe.util.uri import Uri

def _contains(data: dict, path: list[str]) -> bool:
    for step in path:
        children = data.get('children')
        if children is None or step not in children: return False
        data = children[step]
    return True

def _remove(data, path):
    last = path[-1]
    for step in path[:-1]:
        data = data['children'][step]
    del data['children'][last]

def _list_uri_shallow(data, root_path: Uri, filter_path: list[str] | None = None) -> list[Uri]:
    if filter_path is None:
        # Return only leaf nodes from root level
        return [
            root_path / name
            for name, datum in data.items()
            if len(datum.get('children', {})) == 0
        ]

    # Navigate down the filter_path to reach target node
    current = data
    for segment in filter_path:
        if current is None or segment not in current:
            return []
        current = current[segment].get('children', {})

    # Build the base path including filter segments
    base_path = root_path
    for segment in filter_path:
        base_path = base_path / segment

    # Return only leaf children (nodes with no children)
    return [
        base_path / name
        for name, datum in current.items()
        if len(datum.get('children', {})) == 0
    ]

def _list_uri_deep(data, root_path: Uri, filter_path: list[str] | None = None) -> list[Uri]:

    def _filter_none(data, root_path):
        result = []
        worklist = [
            (name, datum, root_path)
            for name, datum in data.items()
        ]
        while len(worklist) != 0:
            name, datum, root_path = worklist.pop()
            next_root_path = root_path / name
            if len(datum['children']) == 0:
                result.append(next_root_path)
                continue
            worklist += [
                (next_name, next_datum, next_root_path)
                for next_name, next_datum in datum['children'].items()
            ]
        return list(reversed(result))

    if filter_path is None:
        return _filter_none(data, root_path)

    # Navigate down the filter_path to reach target node
    current = data
    for segment in filter_path:
        if current is None or segment not in current:
            return []
        current = current[segment].get('children', {})

    # Build the base path including filter segments
    base_path = root_path
    for segment in filter_path:
        base_path = base_path / segment

    # Return all descendants of target node
    return _filter_none(current, base_path)

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

def _deep_diff(base: dict, new: dict) -> dict:
    """Return only the fields in 'new' that differ from 'base'.

    For nested dicts, recurse and only include changed sub-fields.
    Returns empty dict if all values match.
    """
    result = {}
    for key, value in new.items():
        if key not in base:
            # New field not in base - include it
            result[key] = value
        elif isinstance(value, dict) and isinstance(base[key], dict):
            # Both are dicts - recurse
            diff = _deep_diff(base[key], value)
            if diff:  # Only include if there are differences
                result[key] = diff
        elif value != base[key]:
            # Value differs - include it
            result[key] = value
    return result

class ProjectConfigConvention(ConfigConvention):
    def __init__(self):

        # Paths
        self.config_path = get_config_path()
        self.db_path = self.config_path / 'db'

        # Pre-cached data
        self.cache = dict()
        for file_path in self.db_path.iterdir():
            if not file_path.is_file(): continue
            if not file_path.suffix == '.json': continue
            purpose = file_path.stem
            self.cache[purpose] = load_json(file_path)

    def refresh_cache(self, purpose: str = None):
        """Reload cache from disk files.

        Args:
            purpose: If specified, only reload that purpose (e.g., 'entity').
                     If None, reload all purposes.
        """
        if purpose:
            file_path = self.db_path / f'{purpose}.json'
            if file_path.exists():
                self.cache[purpose] = load_json(file_path)
        else:
            for file_path in self.db_path.iterdir():
                if not file_path.is_file(): continue
                if not file_path.suffix == '.json': continue
                self.cache[file_path.stem] = load_json(file_path)

    def _insert(self, data: dict, datum: dict, path: list[str]):
        """Insert entity at path, creating intermediate nodes as needed.

        Properties are stored sparsely - only overrides, not defaults.
        Defaults are resolved from schemas at query time, and the schema
        itself is resolved from the entity's position (get_entity_schema_uri),
        so nothing about it is stored per-node.
        """
        for step in path:
            if 'children' not in data:
                data['children'] = dict()
            if step not in data['children']:
                data['children'][step] = dict(
                    properties = dict(),  # Sparse: no defaults, resolved at query time
                    children = dict()
                )
            data = data['children'][step]
        data['properties'] = datum

    def add_entity(self, uri: Uri, properties: dict):
        # Safety: Ensure cache is loaded from disk if missing
        if uri.purpose not in self.cache:
            file_path = self.db_path / f'{uri.purpose}.json'
            if file_path.exists():
                self.cache[uri.purpose] = load_json(file_path)
            else:
                self.cache[uri.purpose] = {'properties': {}, 'children': {}}

        data = self.cache[uri.purpose]
        if _contains(data, uri.segments):
            raise ValueError('Entity already exists')

        self._insert(data, properties, uri.segments)
        # Note: data is modified in place, cache is already updated
        store_json(self.db_path / f'{uri.purpose}.json', data)

    def remove_entity(self, uri: Uri):
        data = self.cache.get(uri.purpose, dict())
        if not _contains(data, uri.segments): raise ValueError('Entity does not exist')
        _remove(data, uri.segments)
        self.cache[uri.purpose] = data
        store_json(self.db_path / f'{uri.purpose}.json', data)

    def get_properties(self, uri: Uri) -> dict | None:
        """Get properties with hierarchical resolution.

        For entity URIs: starts with schema defaults, then deep merges entity
        hierarchy properties from root to leaf.
        For schema URIs: just merges schema hierarchy (no schema-of-schema lookup).
        """
        import copy

        data = self.cache.get(uri.purpose)
        if data is None:
            return None

        # For non-schema URIs, start with schema defaults
        if uri.purpose != 'schemas':
            schema = self.get_entity_schema(uri)
            if schema is not None:
                result = {name: copy.deepcopy(field.default) for name, field in schema.fields.items()}
            else:
                result = {}
        else:
            result = {}

        # Merge root properties
        result = _deep_merge(result, copy.deepcopy(data.get('properties', {})))

        # Walk down the path, deep merging properties
        for step in uri.segments:
            if step not in data.get('children', {}):
                return None
            data = data['children'][step]
            child_props = data.get('properties', {})
            result = _deep_merge(result, child_props)

        return result if result else None

    def get_own_properties(self, uri: Uri) -> dict | None:
        """Properties stored directly on this entity (no defaults, no
        inheritance). {} if the entity exists but stores nothing; None if
        it does not exist. See the ConfigConvention base for the contract.
        """
        import copy

        data = self.cache.get(uri.purpose)
        if data is None:
            return None
        for step in uri.segments:
            children = data.get('children', {})
            if step not in children:
                return None
            data = children[step]
        return copy.deepcopy(data.get('properties', {}))

    def _get_inherited_properties(self, uri: Uri) -> dict:
        """Get inherited properties (schema defaults + parent chain) without this entity's own props."""
        import copy

        # Start with schema defaults
        if uri.purpose != 'schemas':
            schema = self.get_entity_schema(uri)
            if schema is not None:
                result = {name: copy.deepcopy(field.default) for name, field in schema.fields.items()}
            else:
                result = {}
        else:
            result = {}

        data = self.cache.get(uri.purpose)
        if data is None:
            return result

        # For a URI with no segments, the root entity IS the target — its
        # own properties must not appear in "inherited", or set_properties'
        # sparse diff will silently drop fields that happen to equal what's
        # already stored. Only merge root properties when we have at least
        # one segment to descend into.
        if not uri.segments:
            return result

        # Merge root properties (these are inherited by all children).
        result = _deep_merge(result, copy.deepcopy(data.get('properties', {})))

        # Walk down the path, merging parent properties (stop before the final segment).
        for step in uri.segments[:-1]:
            if step not in data.get('children', {}):
                break
            data = data['children'][step]
            child_props = data.get('properties', {})
            result = _deep_merge(result, child_props)

        return result

    def set_properties(self, uri: Uri, properties: dict):
        # Calculate inherited properties and store only the difference
        inherited = self._get_inherited_properties(uri)
        sparse_properties = _deep_diff(inherited, properties)

        data = self.cache.get(uri.purpose, dict(children=dict()))
        self._insert(data, sparse_properties, uri.segments)
        self.cache[uri.purpose] = data
        file_path = self.db_path / f'{uri.purpose}.json'
        store_json(file_path, data)

    def list_entities(self, filter: Uri | None = None, closure: bool = False) -> list[Entity]:
        if filter is None:
            if 'entity' not in self.cache: return []
            data = self.cache['entity'].get('children', {})
            root_path = Uri.parse_unsafe('entity:/')
            if not closure:
                uris = _list_uri_shallow(data, root_path)
            else:
                uris = _list_uri_deep(data, root_path)
        else:
            purpose = filter.purpose
            if purpose not in self.cache:
                return []
            data = self.cache[purpose].get('children', {})
            root_path = Uri.parse_unsafe(f'{purpose}:/')
            filter_path = filter.segments
            if not closure:
                uris = _list_uri_shallow(data, root_path, filter_path)
            else:
                uris = _list_uri_deep(data, root_path, filter_path)
        result = [
            Entity(uri=uri, properties=self.get_properties(uri) or {})
            for uri in uris
        ]
        return result

    def get_schema(self, schema_uri: Uri) -> Schema | None:
        if schema_uri.purpose != 'schemas':
            return None
        properties = self.get_properties(schema_uri)
        if properties is None:
            return None
        return schema_from_properties(schema_uri, properties)

    def list_schemas(self, parent_uri: Uri | None = None) -> list[Schema]:
        if 'schemas' not in self.cache:
            return []
        schemas_data = self.cache['schemas']
        if parent_uri is None:
            data = schemas_data.get('children', {})
            root_path = Uri.parse_unsafe('schemas:/')
        else:
            if parent_uri.purpose != 'schemas':
                return []
            data = schemas_data
            for segment in parent_uri.segments:
                data = data.get('children', {}).get(segment)
                if data is None:
                    return []
            data = data.get('children', {})
            root_path = parent_uri
        return [
            schema_from_properties(root_path / name, child_data.get('properties', {}))
            for name, child_data in data.items()
        ]

    def get_entity_schema_uri(self, entity_uri: Uri) -> Uri | None:
        """Resolve an entity's schema URI purely from its position.

        The schema URI is entirely determined by an entity's position, so
        this is the single source of truth — there is no per-node stored
        schema string to drift out of sync (that drift was the cause of the
        frame-range inheritance bug). The schema tree uses placeholder
        segment names (e.g. ``schemas:/entity/assets/category/asset``) so a
        real entity path like ``entity:/assets/CHAR/Hero`` never matches it
        literally. We walk the schema tree in lock-step with the entity
        segments: a segment that names a literal schema child
        (``shots``/``assets``) is used as-is, otherwise we descend through
        the single placeholder child at that level. Returns ``None`` if the
        schema tree doesn't cover the path (no child, or an ambiguous fork
        of placeholders).
        """
        node = self.cache.get('schemas')
        if node is None:
            return None
        node = node.get('children', {}).get(entity_uri.purpose)
        if node is None:
            return None
        schema_segments = [entity_uri.purpose]
        for segment in entity_uri.segments:
            children = node.get('children', {})
            if segment in children:
                chosen = segment
            elif len(children) == 1:
                chosen = next(iter(children))
            else:
                return None
            schema_segments.append(chosen)
            node = children[chosen]
        return Uri.parse_unsafe('schemas:/' + '/'.join(schema_segments))

    def get_entity_schema(self, entity_uri: Uri) -> Schema | None:
        schema_uri = self.get_entity_schema_uri(entity_uri)
        if schema_uri is None:
            return None
        return self.get_schema(schema_uri)

    def get_child_schemas(self, schema_uri: Uri) -> list[Schema]:
        if schema_uri.purpose != 'schemas':
            return []
        return self.list_schemas(schema_uri)

def create():
    return ProjectConfigConvention()