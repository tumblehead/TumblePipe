"""On-disk JSON config store — the engine behind ``ConfigConvention``.

This is the generic, project-independent implementation that backs every
project's config database (``_config/db/*.json``). It used to live, in full,
inside each project's templated ``_config/config_convention.py``; that meant
the engine was frozen into a project at creation and could never be fixed for
existing projects. It now lives in the package, so a single update reaches
every project, and the per-project convention is a thin ``create()`` shim.

Reads are **coherent**: every public read validates the backing file's stamp
(``st_mtime_ns`` + ``st_size``) and transparently reloads when another process
(another Houdini session, the project browser, the Database Editor, the farm)
has written it. There is no "loaded once at startup" snapshot to go stale, so
no manual ``refresh_cache`` is needed for correctness — the entity/config reads
across the codebase are always current.

Coherency is paid for **once per public read, not once per file access**: a
public call opens a coherent-read scope in which each purpose file is stamped
at most once, and computed results (resolved properties, parsed schemas) are
memoized against a store generation that bumps whenever any purpose file
changes. Without this, ``list_entities(closure=True)`` stamped the db files
once per entity (~6 stats × entity count per call) — on a network share that
was a multi-second stall per parameter-pane redraw (the v1.16.5 regression).
"""

import copy
import threading
from contextlib import contextmanager
from pathlib import Path

from tumblepipe.api import get_config_path
from tumblepipe.config import ConfigConvention, Entity
from tumblepipe.config.schema import Schema, schema_from_properties
from tumblepipe.util.data import deep_merge as _deep_merge
from tumblepipe.util.io import load_json, store_json
from tumblepipe.util.uri import Uri


def _contains(data: dict, path: list[str]) -> bool:
    for step in path:
        children = data.get('children')
        if children is None or step not in children:
            return False
        data = children[step]
    return True


def _find_case_collision(data: dict, path: list[str]) -> tuple[str, str] | None:
    """First (wanted, existing) pair along ``path`` differing only by case.

    A sibling whose name matches a path step case-insensitively but not
    exactly would create a parallel case-variant hierarchy — the class of
    schism where 'clash/…' and 'Clash/…' each accumulate their own
    entities, sidecars and exports, and every consumer composes whichever
    spelling it happens to hold. Walking stops where the tree ends: steps
    below that have no siblings to collide with.
    """
    for step in path:
        children = data.get('children')
        if children is None:
            return None
        if step not in children:
            step_folded = step.casefold()
            for existing in children:
                if existing.casefold() == step_folded:
                    return step, existing
            return None
        data = children[step]
    return None


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
    public read re-stamps the file (at most once per coherent-read scope)
    and reloads if it changed, so a write from any other process is picked
    up on the next read with no manual refresh.
    """

    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path if config_path is not None else get_config_path()
        self.db_path = self.config_path / 'db'
        # Coherent cache: purpose -> parsed json, plus the file stamp it was
        # loaded from so a stale entry is detected and reloaded on next read.
        self._cache: dict[str, dict] = {}
        self._stamps: dict[str, tuple[int, int]] = {}
        # Bumps whenever any purpose file's content changes (reload, delete,
        # write). Memoized results are tagged with the generation they were
        # computed under; a mismatch is a miss, so memo hits are exactly as
        # coherent as the underlying cache.
        self._generation: int = 0
        self._generation_lock = threading.Lock()
        self._memo: dict[tuple[str, str], tuple[int, object]] = {}
        # Per-thread coherent-read scope: while a public read is on the
        # stack, each purpose is stamped at most once. Thread-local so a
        # GUI-thread read and a worker-thread read cannot share a scope.
        self._scope = threading.local()

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
        try:
            info = self.db_file(purpose).stat()
        except OSError:
            return None
        return (info.st_mtime_ns, info.st_size)

    def _bump_generation(self) -> None:
        """Invalidate all memoized results.

        Locked because ``+= 1`` is a read-modify-write: two threads
        reloading different purpose files concurrently must not collapse
        into a single generation, or a memo entry computed between the two
        reloads could later match and serve a stale result.
        """
        with self._generation_lock:
            self._generation += 1

    def _scope_state(self) -> threading.local:
        scope = self._scope
        if not hasattr(scope, 'depth'):
            scope.depth = 0
            scope.synced = set()
        return scope

    @contextmanager
    def _coherent(self):
        """Re-entrant scope within which each purpose is stamped at most once.

        Nested public reads (``list_entities`` → ``get_properties`` × N →
        ``get_entity_schema``) share the outermost scope, so one logical read
        costs one stat per purpose file instead of one per ``_load``.
        """
        scope = self._scope_state()
        scope.depth += 1
        try:
            yield
        finally:
            scope.depth -= 1
            if scope.depth == 0:
                scope.synced.clear()

    def _load(self, purpose: str) -> dict | None:
        """Return the coherent in-memory tree for ``purpose`` (None if no file).

        Reloads from disk when the file's stamp differs from the one the
        cached copy was loaded with — i.e. whenever anyone wrote it. Inside
        an active coherent-read scope the stamp check runs once per purpose;
        subsequent loads trust it.
        """
        scope = self._scope_state()
        if scope.depth > 0 and purpose in scope.synced:
            return self._cache.get(purpose)
        stamp = self._stamp(purpose)
        if stamp is None:
            if purpose in self._cache:
                self._cache.pop(purpose, None)
                self._stamps.pop(purpose, None)
                self._bump_generation()
        elif purpose not in self._cache or self._stamps.get(purpose) != stamp:
            self._cache[purpose] = load_json(self.db_file(purpose))
            self._stamps[purpose] = stamp
            self._bump_generation()
        if scope.depth > 0:
            scope.synced.add(purpose)
        return self._cache.get(purpose)

    def purposes(self) -> list[str]:
        """Every purpose with a ``<purpose>.json`` file on disk."""
        if not self.db_path.exists():
            return []
        return sorted(
            p.stem for p in self.db_path.iterdir()
            if p.is_file() and p.suffix == '.json'
        )

    def coherent(self):
        """Public coherent-read scope for batching multiple reads.

        ``with config.coherent():`` around a loop of reads stamps each
        purpose file once for the whole block instead of once per call —
        the results are a consistent snapshot of the files as they were at
        first touch inside the block.
        """
        return self._coherent()

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
        self._bump_generation()

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
        self._memo.clear()
        self._bump_generation()

    # ------------------------------------------------------------------ #
    # Entity CRUD
    # ------------------------------------------------------------------ #
    def add_entity(self, uri: Uri, properties: dict):
        data = self._load(uri.purpose)
        if data is None:
            data = {'properties': {}, 'children': {}}
        if _contains(data, uri.segments):
            raise ValueError('Entity already exists')
        collision = _find_case_collision(data, uri.segments)
        if collision is not None:
            wanted, existing = collision
            raise ValueError(
                f"Entity name '{wanted}' collides with existing "
                f"'{existing}' (names may not differ only by case — "
                "case-variant hierarchies split exports and sidecars)"
            )
        _insert(data, properties, uri.segments)
        self.write_root(uri.purpose, data)

    def remove_entity(self, uri: Uri):
        data = self._load(uri.purpose)
        if data is None or not _contains(data, uri.segments):
            raise ValueError('Entity does not exist')
        _remove(data, uri.segments)
        self.write_root(uri.purpose, data)

    def reorder_children(self, uri: Uri, names: list[str]):
        data = self._load(uri.purpose)
        if data is None:
            raise ValueError('Entity does not exist')
        node = data
        for step in uri.segments:
            children = node.get('children', {})
            if step not in children:
                raise ValueError('Entity does not exist')
            node = children[step]
        children = node.get('children', {})
        if sorted(names) != sorted(children):
            raise ValueError(
                'Reorder list must be a permutation of the existing children'
            )
        node['children'] = {name: children[name] for name in names}
        self.write_root(uri.purpose, data)

    def get_properties(self, uri: Uri) -> dict | None:
        """Get properties with hierarchical resolution.

        For entity URIs: starts with schema defaults, then deep merges entity
        hierarchy properties from root to leaf. For schema URIs: just merges
        schema hierarchy (no schema-of-schema lookup).

        Memoized per store generation; the caller owns the returned dict.
        """
        with self._coherent():
            # Validate the involved purpose files BEFORE consulting the
            # memo, so an external write bumps the generation and the memo
            # misses instead of serving a stale result.
            self._load(uri.purpose)
            if uri.purpose != 'schemas':
                self._load('schemas')
            generation = self._generation
            key = ('properties', str(uri))
            hit = self._memo.get(key)
            if hit is not None and hit[0] == generation:
                return copy.deepcopy(hit[1])
            result = self._compute_properties(uri)
            self._memo[key] = (generation, result)
            return copy.deepcopy(result)

    def _compute_properties(self, uri: Uri) -> dict | None:
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
        with self._coherent():
            # Calculate inherited properties and store only the difference
            inherited = self._get_inherited_properties(uri)
            sparse_properties = _deep_diff(inherited, properties)

            data = self._load(uri.purpose)
            if data is None:
                data = dict(children=dict())
            _insert(data, sparse_properties, uri.segments)
            self.write_root(uri.purpose, data)

    def list_entity_uris(self, filter: Uri | None = None, closure: bool = False) -> list[Uri]:
        """The URIs ``list_entities`` would return, without resolving
        properties — a pure in-memory tree walk after one coherency check.
        Most listing callers only consume ``.uri``; this spares them the
        per-entity property resolution entirely.
        """
        with self._coherent():
            return self._list_uris(filter, closure)

    def list_entities(self, filter: Uri | None = None, closure: bool = False) -> list[Entity]:
        with self._coherent():
            return [
                Entity(uri=uri, properties=self.get_properties(uri) or {})
                for uri in self._list_uris(filter, closure)
            ]

    def _list_uris(self, filter: Uri | None, closure: bool) -> list[Uri]:
        if filter is None:
            purpose, filter_path = 'entity', None
        else:
            purpose, filter_path = filter.purpose, filter.segments
        root = self._load(purpose)
        if root is None:
            return []
        data = root.get('children', {})
        root_path = Uri.parse_unsafe(f'{purpose}:/')
        if not closure:
            return _list_uri_shallow(data, root_path, filter_path)
        return _list_uri_deep(data, root_path, filter_path)

    # ------------------------------------------------------------------ #
    # Schemas
    # ------------------------------------------------------------------ #
    def get_schema(self, schema_uri: Uri) -> Schema | None:
        if schema_uri.purpose != 'schemas':
            return None
        with self._coherent():
            self._load('schemas')
            generation = self._generation
            key = ('schema', str(schema_uri))
            hit = self._memo.get(key)
            if hit is not None and hit[0] == generation:
                return hit[1]
            properties = self.get_properties(schema_uri)
            schema = (
                None if properties is None
                else schema_from_properties(schema_uri, properties)
            )
            self._memo[key] = (generation, schema)
            return schema

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
        with self._coherent():
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
