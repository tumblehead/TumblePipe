from copy import deepcopy

from tumblepipe.util.data import deep_merge as _deep_merge
from tumblepipe.util.io import load_json
from tumblepipe.util.uri import Uri


class DatabaseAdapter:
    """Editor-side view over the config store.

    The Database Editor needs a *stable working copy* it can mutate and diff
    while the panel is open: edits must not leak to the rest of the app until
    the user saves, and an external write must be detected (and merged)
    rather than silently overwritten. So the adapter keeps its own working
    copy per purpose, loaded from ``store.snapshot()``, and commits through
    ``store.write_root()``. It never reaches into the store's private cache —
    that cache is kept coherent for *read* consumers and is not the editor's
    scratch space.
    """

    def __init__(self, api):
        self._api = api
        self._config = api.config            # JsonConfigStore
        self._working: dict[str, dict] = {}  # purpose -> editable working copy
        self._file_mtimes: dict[str, float] = {}    # purpose -> last-seen external mtime
        self._base_snapshots: dict[str, dict] = {}  # purpose -> baseline for 3-way merge
        for purpose in self._config.purposes():
            self._ensure_loaded(purpose)

    # ------------------------------------------------------------------ #
    # Working-copy plumbing
    # ------------------------------------------------------------------ #
    def _mtime(self, purpose: str) -> float | None:
        file_path = self._config.db_file(purpose)
        return file_path.stat().st_mtime if file_path.exists() else None

    def _ensure_loaded(self, purpose: str) -> None:
        """Populate the working copy for ``purpose`` from disk, once."""
        if purpose in self._working:
            return
        snapshot = self._config.snapshot(purpose)
        if snapshot is None:
            return
        self._working[purpose] = snapshot
        self._file_mtimes[purpose] = self._mtime(purpose)
        self._base_snapshots[purpose] = deepcopy(snapshot)

    def _commit(self, purpose: str, data: dict) -> None:
        """Persist a purpose's full tree through the store and re-baseline."""
        self._working[purpose] = data
        self._config.write_root(purpose, data)
        self._file_mtimes[purpose] = self._mtime(purpose)
        self._base_snapshots[purpose] = deepcopy(data)

    def list_purposes(self) -> list[str]:
        """List all available purposes from disk."""
        return self._config.purposes()

    def list_entities(self, root_uri: Uri) -> list[Uri]:
        """List all leaf entities under a root URI"""
        purpose = root_uri.purpose
        self._ensure_loaded(purpose)
        data = self._working.get(purpose)
        if data is None:
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

        base_uri = Uri.parse_unsafe(f"{purpose}:/")
        return _collect(data, base_uri)

    def lookup(self, uri: Uri) -> dict | None:
        """Get the raw data for a URI (full node including properties and children)"""
        self._ensure_loaded(uri.purpose)
        data = self._working.get(uri.purpose)
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
        self._ensure_loaded(purpose)
        return self._working.get(purpose)

    def save(self, uri: Uri, data: dict) -> None:
        """Save changes to a URI by updating the working copy and persisting.

        This method merges the new data with existing data to preserve children
        that already exist at the target path.
        """
        purpose = uri.purpose
        self._ensure_loaded(purpose)
        cache_data = self._working.get(purpose)

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

            # Merge with existing data to preserve children
            final_segment = uri.segments[-1]
            children = current.setdefault('children', {})
            existing = children.get(final_segment, {})

            # Build merged data: preserve existing children, update properties.
            # No per-node schema is stored — it's derived from position.
            merged = {
                'properties': data.get('properties', existing.get('properties', {})),
                'children': existing.get('children', {}),  # Preserve existing children
            }

            children[final_segment] = merged

        self._commit(purpose, cache_data)

    def save_root(self, purpose: str, data: dict) -> None:
        """Save changes to a purpose root"""
        self._commit(purpose, data)

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
        # 1. Start with schema defaults (defines base structure and order).
        # The schema is derived from the entity's position (handles the
        # placeholder schema tree correctly) — the old naive
        # schemas:/{purpose}/{segments} join mismatched placeholder paths.
        schema_uri = self._config.get_entity_schema_uri(uri)
        result = self._get_schema_properties(schema_uri) if schema_uri is not None else {}

        # 2. Merge parent entity inheritance on top
        if uri.segments:
            cache_data = self.lookup_root(uri.purpose)
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

    def save_properties_with_merge(self, uri: Uri, properties: dict) -> tuple[bool, str]:
        """Save properties with merge conflict detection. Returns (success, message)."""
        if uri.segments:
            data = self.lookup(uri) or {'properties': {}, 'children': {}}
        else:
            data = self.lookup_root(uri.purpose) or {'properties': {}, 'children': {}}
        data['properties'] = properties
        if uri.segments:
            return self.save_with_merge(uri, data)
        else:
            return self.save_root_with_merge(uri.purpose, data)

    def force_save_properties(self, uri: Uri, properties: dict) -> None:
        """Force save properties without merge (user chose to overwrite)."""
        if uri.segments:
            data = self.lookup(uri) or {'properties': {}, 'children': {}}
        else:
            data = self.lookup_root(uri.purpose) or {'properties': {}, 'children': {}}
        data['properties'] = properties
        if uri.segments:
            self.force_save(uri, data)
        else:
            self.force_save_root(uri.purpose, data)

    def transact(self, uri: Uri) -> 'DatabaseTransaction':
        """Create a transaction context for applying changes"""
        return DatabaseTransaction(self, uri)

    def _record_file_mtimes(self) -> None:
        """Re-baseline the merge state for every loaded working copy."""
        for purpose in list(self._working.keys()):
            mtime = self._mtime(purpose)
            if mtime is not None:
                self._file_mtimes[purpose] = mtime
                self._base_snapshots[purpose] = deepcopy(self._working[purpose])

    def reload_cache(self) -> None:
        """Reload all data from disk, discarding local edits."""
        self._working.clear()
        self._file_mtimes.clear()
        self._base_snapshots.clear()
        self._config.refresh_cache()  # drop the store's cache so snapshots re-read disk
        for purpose in self._config.purposes():
            self._ensure_loaded(purpose)

    def save_with_merge(self, uri: Uri, data: dict) -> tuple[bool, str]:
        """Save with automatic merge. Returns (success, message).

        Returns:
            (True, "") - saved successfully (no conflict or merge succeeded)
            (False, "conflict") - merge conflict, caller should show dialog
            (False, "error: ...") - other error
        """
        purpose = uri.purpose
        file_path = self._config.db_file(purpose)

        # Check if file was modified externally
        if file_path.exists() and purpose in self._file_mtimes:
            current_mtime = file_path.stat().st_mtime
            if current_mtime > self._file_mtimes[purpose]:
                # File changed externally - try to merge
                disk_data = load_json(file_path)
                base = self._base_snapshots.get(purpose, {})

                # Build our full tree with the pending change applied
                ours = deepcopy(self._working.get(purpose, {}))
                self._apply_uri_data(ours, uri, data)

                merged, has_conflict = self._merge_changes(base, ours, disk_data)

                if has_conflict:
                    return False, "conflict"

                # Merge succeeded - commit merged data
                self._commit(purpose, merged)
                return True, ""

        # No external changes - normal save (commit re-baselines)
        self.save(uri, data)
        return True, ""

    def save_root_with_merge(self, purpose: str, data: dict) -> tuple[bool, str]:
        """Save root with automatic merge. Returns (success, message)."""
        file_path = self._config.db_file(purpose)

        if file_path.exists() and purpose in self._file_mtimes:
            current_mtime = file_path.stat().st_mtime
            if current_mtime > self._file_mtimes[purpose]:
                disk_data = load_json(file_path)
                base = self._base_snapshots.get(purpose, {})

                merged, has_conflict = self._merge_changes(base, data, disk_data)

                if has_conflict:
                    return False, "conflict"

                self._commit(purpose, merged)
                return True, ""

        self.save_root(purpose, data)
        return True, ""

    def _apply_uri_data(self, cache_data: dict, uri: Uri, data: dict) -> None:
        """Apply data at a URI path within cache_data (mutates cache_data)."""
        if not uri.segments:
            cache_data.update(data)
            return

        current = cache_data
        for segment in uri.segments[:-1]:
            children = current.setdefault('children', {})
            if segment not in children:
                children[segment] = {'properties': {}, 'children': {}}
            current = children[segment]

        final_segment = uri.segments[-1]
        children = current.setdefault('children', {})
        children[final_segment] = data

    def _merge_changes(self, base: dict, ours: dict, theirs: dict) -> tuple[dict | None, bool]:
        """Three-way merge using dictdiffer. Returns (merged_data, has_conflict)."""
        from dictdiffer import patch
        from dictdiffer.merge import Merger, UnresolvedConflictsException

        merger = Merger(base, ours, theirs, {})
        try:
            merger.run()
        except UnresolvedConflictsException:
            return None, True
        merged = patch(merger.unified_patches, base)
        return merged, False

    def force_save(self, uri: Uri, data: dict) -> None:
        """Force save without merge (user chose to overwrite)."""
        self.save(uri, data)

    def force_save_root(self, purpose: str, data: dict) -> None:
        """Force save root without merge (user chose to overwrite)."""
        self.save_root(purpose, data)


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
    from .json_editor import JsonPathRoot, JsonPathField, JsonPathIndex

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
