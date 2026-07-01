"""On-disk JSON config store — the engine behind ``ConfigConvention``.

This is the generic, project-independent implementation that backs every
project's config database (``_config/db/*.json``). It used to live, in full,
inside each project's templated ``_config/config_convention.py``; that meant
the engine was frozen into a project at creation and could never be fixed for
existing projects. It now lives in the package, so a single update reaches
every project, and the per-project convention is a thin ``create()`` shim.

Reads are **coherent**: every read validates the backing file's stamp
(``st_mtime_ns`` + ``st_size``) and transparently reloads when another process
(another Houdini session, the project browser, the Database Editor, the farm)
has written it. There is no "loaded once at startup" snapshot to go stale, so
no manual ``refresh_cache`` is needed for correctness — the entity/config reads
across the codebase are always current.
"""

import copy
from pathlib import Path

from tumblepipe.api import get_config_path
from tumblepipe.config import ConfigConvention, Entity
from tumblepipe.config.schema import Schema, schema_from_properties
from tumblepipe.util.io import load_json, store_json
from tumblepipe.util.uri import Uri


def _contains(data: dict, path: list[str]) -> bool:
    for step in path:
        children = data.get('children')
        if children is None or step not in children:
            return False
        data = children[step]
    return True


def _remove(data, path):
    last = path[-1]
    for step in path[:-1]:
        data = data['children'][step]
    del data['children'][last]


def _insert(data: dict, datum: dict, path: list[str]):
    """Insert ``datum`` at ``path``, creating intermediate nodes as needed.

    Properties are stored sparsely - only overrides, not defaults. Defaults
    are resolved from schemas at query time, and the schema itself is resolved
    from the entity's position (get_entity_schema_uri), so nothing about it is
    stored per-node.
    """
    for step in path:
        if 'children' not in data:
            data['children'] = dict()
        if step not in data['children']:
            data['children'][step] = dict(properties=dict(), children=dict())
        data = data['children'][step]
    data['properties'] = datum


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


class JsonConfigStore(ConfigConvention):
    """A ``ConfigConvention`` backed by ``_config/db/<purpose>.json`` files.

    Each ``<purpose>.json`` holds one entity/schema tree (``entity``,
    ``schemas``, ``departments``, ``config`` ...). Properties are stored
    sparsely against schema defaults resolved at query time.

    The in-memory copy of each purpose is kept coherent with disk: every
    access re-stamps the file and reloads if it changed, so a write from any
    other process is picked up on the next read with no manual refresh.
    """

    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path if config_path is not None else get_config_path()
        self.db_path = self.config_path / 'db'
        # Coherent cache: purpose -> parsed json, plus the file stamp it was
        # loaded from so a stale entry is detected and reloaded on next read.
        self._cache: dict[str, dict] = {}
        self._stamps: dict[str, tuple[int, int]] = {}

    # ------------------------------------------------------------------ #
    # Coherent-cache core
    # ------------------------------------------------------------------ #
    def db_file(self, purpose: str) -> Path:
        return self.db_path / f'{purpose}.json'

    def _stamp(self, purpose: str) -> tuple[int, int] | None:
        """Identity stamp for a purpose file: (mtime_ns, size), or None if absent.

        ``st_mtime_ns`` dodges the float-rounding and coarse-resolution traps
        of ``st_mtime``; pairing it with ``st_size`` catches the rare
        same-nanosecond rewrite of a different length.
        """
        file_path = self.db_file(purpose)
        if not file_path.exists():
            return None
        info = file_path.stat()
        return (info.st_mtime_ns, info.st_size)

    def _load(self, purpose: str) -> dict | None:
        """Return the coherent in-memory tree for ``purpose`` (None if no file).

        Reloads from disk when the file's stamp differs from the one the
        cached copy was loaded with — i.e. whenever anyone wrote it.
        """
        stamp = self._stamp(purpose)
        if stamp is None:
            self._cache.pop(purpose, None)
            self._stamps.pop(purpose, None)
            return None
        if purpose not in self._cache or self._stamps.get(purpose) != stamp:
            self._cache[purpose] = load_json(self.db_file(purpose))
            self._stamps[purpose] = stamp
        return self._cache[purpose]

    def purposes(self) -> list[str]:
        """Every purpose with a ``<purpose>.json`` file on disk."""
        if not self.db_path.exists():
            return []
        return sorted(
            p.stem for p in self.db_path.iterdir()
            if p.is_file() and p.suffix == '.json'
        )

    def root(self, purpose: str) -> dict | None:
        """The coherent top-level tree for ``purpose`` (live object, None if absent).

        Read-only callers must not mutate the returned dict; use ``snapshot``
        for an owned copy.
        """
        return self._load(purpose)

    def snapshot(self, purpose: str) -> dict | None:
        """An owned deep copy of ``purpose``'s tree, safe to mutate freely."""
        data = self._load(purpose)
        return copy.deepcopy(data) if data is not None else None

    def write_root(self, purpose: str, data: dict) -> None:
        """Persist ``data`` as ``purpose``'s whole tree and refresh the stamp.

        The stamp is taken *after* writing so this process does not mistake its
        own write for an external change and pointlessly reload it.
        """
        store_json(self.db_file(purpose), data)
        self._cache[purpose] = data
        self._stamps[purpose] = self._stamp(purpose)

    def refresh_cache(self, purpose: str | None = None) -> None:
        """Force the next read to reload from disk.

        Reads are already coherent, so this is no longer needed for
        correctness; it is retained as an explicit "drop what you have" for
        callers that want to discard in-memory state outright.
        """
        if purpose is not None:
            self._cache.pop(purpose, None)
            self._stamps.pop(purpose, None)
        else:
            self._cache.clear()
            self._stamps.clear()

    # ------------------------------------------------------------------ #
    # Entity CRUD
    # ------------------------------------------------------------------ #
    def add_entity(self, uri: Uri, properties: dict):
        data = self._load(uri.purpose)
        if data is None:
            data = {'properties': {}, 'children': {}}
        if _contains(data, uri.segments):
            raise ValueError('Entity already exists')
        _insert(data, properties, uri.segments)
        self.write_root(uri.purpose, data)

    def remove_entity(self, uri: Uri):
        data = self._load(uri.purpose)
        if data is None or not _contains(data, uri.segments):
            raise ValueError('Entity does not exist')
        _remove(data, uri.segments)
        self.write_root(uri.purpose, data)

    def get_properties(self, uri: Uri) -> dict | None:
        """Get properties with hierarchical resolution.

        For entity URIs: starts with schema defaults, then deep merges entity
        hierarchy properties from root to leaf. For schema URIs: just merges
        schema hierarchy (no schema-of-schema lookup).
        """
        data = self._load(uri.purpose)
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
        data = self._load(uri.purpose)
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
        # Start with schema defaults
        if uri.purpose != 'schemas':
            schema = self.get_entity_schema(uri)
            if schema is not None:
                result = {name: copy.deepcopy(field.default) for name, field in schema.fields.items()}
            else:
                result = {}
        else:
            result = {}

        data = self._load(uri.purpose)
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

        data = self._load(uri.purpose)
        if data is None:
            data = dict(children=dict())
        _insert(data, sparse_properties, uri.segments)
        self.write_root(uri.purpose, data)

    def list_entities(self, filter: Uri | None = None, closure: bool = False) -> list[Entity]:
        if filter is None:
            data = self._load('entity')
            if data is None:
                return []
            data = data.get('children', {})
            root_path = Uri.parse_unsafe('entity:/')
            if not closure:
                uris = _list_uri_shallow(data, root_path)
            else:
                uris = _list_uri_deep(data, root_path)
        else:
            purpose = filter.purpose
            root = self._load(purpose)
            if root is None:
                return []
            data = root.get('children', {})
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

    # ------------------------------------------------------------------ #
    # Schemas
    # ------------------------------------------------------------------ #
    def get_schema(self, schema_uri: Uri) -> Schema | None:
        if schema_uri.purpose != 'schemas':
            return None
        properties = self.get_properties(schema_uri)
        if properties is None:
            return None
        return schema_from_properties(schema_uri, properties)

    def list_schemas(self, parent_uri: Uri | None = None) -> list[Schema]:
        schemas_data = self._load('schemas')
        if schemas_data is None:
            return []
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
        node = self._load('schemas')
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


def create() -> JsonConfigStore:
    return JsonConfigStore()
