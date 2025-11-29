import asyncio
import json
import re
from contextlib import ExitStack

from pymongo import MongoClient
from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from tumblehead.util.uri import (
    NamedItem,
    NamedSection,
    Uri,
    WildcardItem,
    WildcardSection,
)


class Transaction:
    def __enter__(self):
        raise NotImplementedError()

    def __exit__(self, exc_type, exc_value, traceback):
        raise NotImplementedError()

    def array_insert(self, path, index, value):
        raise NotImplementedError()

    def array_update(self, path, index, value):
        raise NotImplementedError()

    def array_reorder(self, path, from_index, to_index):
        raise NotImplementedError()

    def array_remove(self, path, index):
        raise NotImplementedError()

    def object_insert(self, path, key, value):
        raise NotImplementedError()

    def object_update(self, path, key, value):
        raise NotImplementedError()

    def object_rename(self, path, old_key, new_key):
        raise NotImplementedError()

    def object_remove(self, path, key):
        raise NotImplementedError()


class Store:
    def __init__(self):
        raise NotImplementedError()

    def insert(self, uri, datum):
        raise NotImplementedError()

    def update(self, uri, datum):
        raise NotImplementedError()

    def rename(self, src_uri, dst_uri):
        raise NotImplementedError()

    def delete(self, uri):
        raise NotImplementedError()

    def query(self, uri, params):
        raise NotImplementedError()

    def transact(self, uri):
        raise NotImplementedError()


def _is_section(path):
    if isinstance(path, WildcardSection):
        return True
    if isinstance(path, NamedSection):
        return True
    return False


def _is_item(path):
    if isinstance(path, WildcardItem):
        return True
    if isinstance(path, NamedItem):
        return True
    return False


def _iter_uri(uri: Uri):
    purpose, parts = uri.parts()
    result = Uri(purpose, None)
    yield result
    for part in parts:
        result = result / part
        yield result


class MemoryStore(Store):
    def __init__(self):
        self._data = dict()

    def insert(self, uri, datum):
        # Check if source does not exist
        curr_path = uri.path
        curr_data = self._data
        while not _is_item(curr_path):
            assert isinstance(curr_path, NamedSection), (
                f"{uri} is a valid uri for insertion"
            )
            if curr_path.name not in curr_data:
                curr_data[curr_path.name] = dict()
            curr_data = curr_data[curr_path.name]
            curr_path = curr_path.path
        assert isinstance(curr_path, NamedItem), (
            f"{uri} is a valid uri for insertion"
        )
        assert curr_path.name not in curr_data, f"{uri} already defined"

        # Insert
        curr_data[curr_path.name] = datum

    def update(self, uri, datum):
        # Check if source exists
        curr_path = uri.path
        curr_data = self._data
        while not _is_item(curr_path):
            assert isinstance(curr_path, NamedSection), (
                f"{uri} is a valid uri for update"
            )
            assert curr_path.name in curr_data, f"{uri} not defined"
            curr_data = curr_data[curr_path.name]
            curr_path = curr_path.path
        assert isinstance(curr_path, NamedItem), (
            f"{uri} is a valid uri for update"
        )
        assert curr_path.name in curr_data, f"{uri} not defined"

        # Update
        curr_data[curr_path.name] = datum

    def rename(self, src_uri, dst_uri):
        # Check if source exists
        src_curr_path = src_uri.path
        src_curr_data = self._data
        while not _is_item(src_curr_path):
            assert isinstance(src_curr_path, NamedSection), (
                f"{src_uri} is a valid uri for rename"
            )
            assert src_curr_path.name in src_curr_data, f"{src_uri} not defined"
            src_curr_data = src_curr_data[src_curr_path.name]
            src_curr_path = src_curr_path.path
        assert isinstance(src_curr_path, NamedItem), (
            f"{src_uri} is a valid uri for rename"
        )
        assert src_curr_path.name in src_curr_data, f"{src_uri} not defined"

        # Check if destination does not exist
        dst_curr_path = dst_uri.path
        dst_curr_data = self._data
        while not _is_item(dst_curr_path):
            assert isinstance(dst_curr_path, NamedSection), (
                f"{dst_uri} is a valid uri for rename"
            )
            if dst_curr_path.name not in dst_curr_data:
                dst_curr_data[dst_curr_path.name] = dict()
            dst_curr_data = dst_curr_data[dst_curr_path.name]
            dst_curr_path = dst_curr_path.path
        assert isinstance(dst_curr_path, NamedItem), (
            f"{dst_uri} is a valid uri for rename"
        )
        assert dst_curr_path.name not in dst_curr_data, (
            f"{dst_uri} already defined"
        )

        # Copy and delete
        dst_curr_data[dst_curr_path.name] = src_curr_data[
            src_curr_path.name
        ].copy()
        del src_curr_data[src_curr_path.name]

    def delete(self, uri):
        # Check if source exists
        curr_path = uri.path
        curr_data = self._data
        while not _is_item(curr_path):
            assert isinstance(curr_path, NamedSection), (
                f"{uri} is a valid uri for deletion"
            )
            assert curr_path.name in curr_data, f"{uri} not defined"
            curr_data = curr_data[curr_path.name]
            curr_path = curr_path.path
        assert isinstance(curr_path, NamedItem), (
            f"{uri} is a valid uri for deletion"
        )
        assert curr_path.name in curr_data, f"{uri} not defined"

        # Delete
        del curr_data[curr_path.name]

    def lookup(self, uri):
        """Lookup a single item by URI. Returns the datum or None if not found."""
        try:
            # Navigate to the item using the same logic as update/delete
            curr_path = uri.path
            curr_data = self._data
            while not _is_item(curr_path):
                if not isinstance(curr_path, NamedSection):
                    return None
                if curr_path.name not in curr_data:
                    return None
                curr_data = curr_data[curr_path.name]
                curr_path = curr_path.path

            if not isinstance(curr_path, NamedItem):
                return None
            if curr_path.name not in curr_data:
                return None

            return curr_data[curr_path.name]
        except (AttributeError, KeyError):
            return None

    def query(self, uri, **params):
        def _flatten(list_of_lists):
            return [item for sublist in list_of_lists for item in sublist]

        def _section(path, data):
            if isinstance(path, WildcardSection):
                return list(data.values())
            if isinstance(path, NamedSection):
                return [data[path.name]]
            assert False, f"{path} is not a valid section"

        def _item(path, data):
            if isinstance(path, WildcardItem):
                return list(data.values())
            if isinstance(path, NamedItem):
                return [data[path.name]]
            assert False, f"{path} is not a valid item"

        def _included(params, datum):
            for key, value in params.items():
                if key not in datum:
                    return False
                if datum[key] != value:
                    return False
            return True

        # Step through the sections
        curr_path = uri.path
        curr_data = [self._data]
        while not _is_item(curr_path):
            curr_data = _flatten(
                map(lambda data: _section(curr_path, data), curr_data)
            )
            curr_path = curr_path.path

        # Final item step
        curr_data = _flatten(
            map(lambda data: _item(curr_path, data), curr_data)
        )

        # Filter by params
        return list(filter(lambda datum: _included(params, datum), curr_data))


def _path_to_uri(root_path, item_path):
    _item_path = item_path.relative_to(root_path)
    _parts = str(_item_path.parent).replace("\\", "/").split("/")
    _parts.append(_item_path.stem)
    return Uri.parse_unsafe("/".join(_parts))


def _uri_to_path(root_path, item_uri):
    _parts = [item_uri.path.name]
    while isinstance(item_uri.path, NamedSection):
        item_uri = item_uri.path
        _parts.append(item_uri.name)
    return root_path / "/".join(_parts) / f"{item_uri.name}.json"


class _Indexer(FileSystemEventHandler):
    def __init__(self, path, store):
        # Members
        self._path = path
        self._store = store
        self._loop = asyncio.get_event_loop()
        self._queue = asyncio.Queue()
        self._observer = Observer()
        self._task = None

        # Schedule observer
        self._observer.schedule(self, str(self._path), recursive=True)

    def _schedule(self):
        # Initial scan
        for file_path in self._path.rglob("*.json"):
            if not file_path.is_file():
                continue
            uri = _path_to_uri(self._path, file_path)
            with file_path.open("r") as file:
                datum = json.load(file)
            self._store.insert(uri, datum)

        # Start observer
        self._observer.start()
        self._observer.join()
        self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    def on_created(self, event):
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def on_modified(self, event):
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def on_deleted(self, event):
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def on_moved(self, event):
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration()
        return item

    async def _process(self):
        async for event in self:
            match event:
                case FileMovedEvent(src_path, dst_path):
                    src_uri = _path_to_uri(self._path, src_path)
                    dst_uri = _path_to_uri(self._path, dst_path)
                    self._store.rename(src_uri, dst_uri)
                case DirMovedEvent(src_path, dst_path):
                    for file_path in src_path.rglob("*.json"):
                        if not file_path.is_file():
                            continue
                        src_uri = _path_to_uri(self._path, file_path)
                        dst_uri = _path_to_uri(
                            self._path,
                            dst_path / file_path.relative_to(src_path),
                        )
                        self._store.rename(src_uri, dst_uri)
                case FileModifiedEvent(src_path):
                    uri = _path_to_uri(self._path, src_path)
                    with src_path.open("r") as file:
                        datum = json.load(file)
                    self._store.update(uri, datum)
                case DirModifiedEvent(src_path):
                    for file_path in src_path.rglob("*.json"):
                        if not file_path.is_file():
                            continue
                        uri = _path_to_uri(self._path, file_path)
                        with file_path.open("r") as file:
                            datum = json.load(file)
                        self._store.update(uri, datum)
                case FileCreatedEvent(src_path):
                    uri = _path_to_uri(self._path, src_path)
                    with src_path.open("r") as file:
                        datum = json.load(file)
                    self._store.insert(uri, datum)
                case DirCreatedEvent(src_path):
                    for file_path in src_path.rglob("*.json"):
                        if not file_path.is_file():
                            continue
                        uri = _path_to_uri(self._path, file_path)
                        with file_path.open("r") as file:
                            datum = json.load(file)
                        self._store.insert(uri, datum)
                case FileDeletedEvent(src_path):
                    uri = _path_to_uri(self._path, src_path)
                    self._store.delete(uri)
                case DirDeletedEvent(src_path):
                    for file_path in src_path.rglob("*.json"):
                        if not file_path.is_file():
                            continue
                        uri = _path_to_uri(self._path, file_path)
                        self._store.delete(uri)
                case _:
                    raise NotImplementedError()

    def start(self):
        assert self._task is None, "Indexer is already running"
        self._task = asyncio.create_task(
            asyncio.gather(
                [
                    self._loop.run_in_executor(None, self._schedule),
                    self._process(),
                ]
            )
        )

    async def stop(self):
        assert self._task is not None, "Indexer is not running"
        self._observer.stop()
        await self._task
        self._task = None


def _prune_empty(root_path, item_path):
    relative_path = item_path.relative_to(root_path)
    while len(relative_path.parts) > 1:
        current_path = root_path / relative_path
        if len(list(current_path.iterdir())) > 0:
            break
        current_path.rmdir()
        relative_path = relative_path.parent


class FileStore(Store):
    def __init__(self, store_path):
        # Members
        self._path = store_path
        self._cache = MemoryStore()
        self._indexer = _Indexer(self._path, self._cache)

        # Start indexer
        self._indexer.start()

    def __del__(self):
        asyncio.run(self._indexer.stop())

    def insert(self, uri, datum):
        file_path = _uri_to_path(self._path, uri)
        assert not file_path.exists(), f"{uri} already defined"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("w+") as file:
            json.dump(datum, file)

    def update(self, uri, datum):
        file_path = _uri_to_path(self._path, uri)
        assert file_path.exists(), f"{uri} not defined"
        with file_path.open("w") as file:
            json.dump(datum, file)

    def rename(self, src_uri, dst_uri):
        src_path = _uri_to_path(self._path, src_uri)
        dst_path = _uri_to_path(self._path, dst_uri)
        assert src_path.exists(), f"{src_uri} not defined"
        assert not dst_path.exists(), f"{dst_uri} already defined"
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        src_path.rename(dst_path)
        _prune_empty(self._path, src_path.parent)

    def delete(self, uri):
        file_path = _uri_to_path(self._path, uri)
        assert file_path.exists(), f"{uri} not defined"
        file_path.unlink()
        _prune_empty(self._path, file_path.parent)

    def lookup(self, uri):
        """Lookup a single item by URI. Returns the datum or None if not found."""
        return self._cache.lookup(uri)

    def query(self, uri, **params):
        return self._cache.query(uri, **params)


def _namespace():
    return dict(subpath=dict(), datum=dict())


def _set(data, path, datum):
    while len(path) > 0:
        step = path.pop(0)
        if step not in data["subpath"]:
            data["subpath"][step] = _namespace()
        data = data["subpath"][step]
    data["datum"] = datum


def _get(data, path, params):
    def _update(datum, params, result):
        if params is None:
            result.update(datum)
        else:
            for param in params:
                if param not in datum:
                    continue
                result[param] = datum[param]

    # Traverse the path
    _data = list()
    while len(path) > 0:
        _data.append(data["datum"])
        step = path.pop(0)
        if step not in data["subpath"]:
            break
        data = data["subpath"][step]
    _data.append(data["datum"])

    # Update the result with the final data
    result = dict()
    for datum in _data:
        _update(datum, params, result)

    # Check if all params are defined
    if params is None:
        return result
    for param in params:
        if param in result:
            continue
        raise ValueError(f"{param} not defined")
    return result


def _uri_to_regex(uri):
    def _map_part(part: str) -> str:
        if part == "*":
            return "[^/]+"
        return part

    def _map_last(part: str) -> str:
        if part == "*":
            return ".*"
        return part

    purpose, parts = uri.parts()
    match len(parts):
        case 0:
            return re.compile(f"^{purpose}:/$")
        case 1:
            last = parts[0]
            _last = _map_last(last)
            return re.compile(f"^{purpose}:/{_last}$")
        case _:
            remain, last = parts[:-1], parts[-1]
            _remain = "/".join(map(_map_part, remain))
            _last = _map_last(last)
            result = f"^{purpose}:/{_remain}/{_last}$"
            return re.compile(result)


class MongoTransaction(Transaction):
    def __init__(self, client, collection):
        self._client = client
        self._collection = collection
        self._stack = ExitStack()
        self._session = None

    def __enter__(self):
        assert self._session is None, "Transaction already started"
        self._session = self._client.start_session()
        self._session.start_transaction()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        assert self._session is not None, "Transaction not started"
        try:
            if exc_type is not None:
                raise exc_value
            self._session.commit_transaction()
        except Exception as e:
            print(f"Failed to commit transaction: {e}")
            self._session.abort_transaction()
        finally:
            self._session.end_session()
            self._session = None

    def array_insert(self, path, index, value):
        assert self._session is not None, "Transaction not started"
        self._collection.update_one(
            {"uri": str(path)},
            {"$push": {"datum": {"$each": [value], "$position": index}}},
            session=self._session,
        )

    def array_update(self, path, index, value):
        assert self._session is not None, "Transaction not started"
        self._collection.update_one(
            {"uri": str(path)},
            {"$set": {f"datum.{index}": value}},
            session=self._session,
        )

    def array_reorder(self, path, from_index, to_index, from_value, to_value):
        assert self._session is not None, "Transaction not started"
        self._collection.update_one(
            {"uri": str(path)},
            {
                "$set": {
                    f"datum.{from_index}": to_value,
                    f"datum.{to_index}": from_value,
                }
            },
            session=self._session,
        )

    def array_remove(self, path, index):
        assert self._session is not None, "Transaction not started"
        self._collection.update_one(
            {"uri": str(path)},
            {"$unset": {f"datum.{index}": ""}},
            session=self._session,
        )

    def object_insert(self, path, key, value):
        assert self._session is not None, "Transaction not started"
        self._collection.update_one(
            {"uri": str(path)},
            {"$set": {f"datum.{key}": value}},
            session=self._session,
        )

    def object_update(self, path, key, value):
        assert self._session is not None, "Transaction not started"
        self._collection.update_one(
            {"uri": str(path)},
            {"$set": {f"datum.{key}": value}},
            session=self._session,
        )

    def object_rename(self, path, old_key, new_key):
        assert self._session is not None, "Transaction not started"
        self._collection.update_one(
            {"uri": str(path)},
            {"$rename": {f"datum.{old_key}": f"datum.{new_key}"}},
            session=self._session,
        )

    def object_remove(self, path, key):
        assert self._session is not None, "Transaction not started"
        self._collection.update_one(
            {"uri": str(path)},
            {"$unset": {f"datum.{key}": ""}},
            session=self._session,
        )


class MongoStore(Store):
    def __init__(self, host: str, port: int, db_name: str):
        # Members
        self._client = MongoClient(host, port)
        self._db = self._client[db_name]
        self._initialized = set()

    def _collection(self, purpose: str):
        if purpose not in self._db.list_collection_names():
            raise ValueError(f"{purpose} not defined")
        collection = self._db[purpose]
        if purpose not in self._initialized:
            collection.create_index("uri", unique=True)
            self._initialized.add(purpose)
        return collection

    def contains(self, uri: Uri) -> bool:
        # Check if the Uri is wild
        if uri.is_wild():
            raise ValueError(
                f"{uri} is a wildcard URI and cannot be checked for existence"
            )

        # Query the collection
        collection = self._collection(uri.purpose)
        result = collection.find_one({"uri": str(uri)})
        return result is not None

    def insert(self, uri: Uri, **params):
        # Check if the Uri is wild
        if uri.is_wild():
            raise ValueError(f"{uri} is a wildcard URI and cannot be inserted")

        # Insert the object
        collection = self._collection(uri.purpose)
        result = collection.insert_one(dict(uri=str(uri), datum=params))
        if result.acknowledged:
            return
        raise RuntimeError(f"Failed to insert {uri}")

    def update(self, uri: Uri, **params):
        # Check if the Uri is wild
        if uri.is_wild():
            raise ValueError(f"{uri} is a wildcard URI and cannot be updated")

        # Update the object
        collection = self._collection(uri.purpose)
        result = collection.replace_one(
            {"uri": str(uri)}, {"$set": {"datum": params}}
        )
        if result.acknowledged:
            return
        raise RuntimeError(f"Failed to update {uri}")

    def rename(self, src_uri: Uri, dst_uri: Uri):
        # Check if the URIs are valid
        if src_uri.is_wild() or dst_uri.is_wild():
            raise ValueError(f"Cannot rename wildcards: {src_uri} to {dst_uri}")

        # Check if the URIs are the same
        src_uri_raw = str(src_uri)
        dst_uri_raw = str(dst_uri)
        if src_uri_raw == dst_uri_raw:
            return

        # Check if the source exists
        src_collection = self._db[src_uri.purpose]
        src_datum = src_collection.find_one({"uri": src_uri_raw})
        if src_datum is None:
            raise ValueError(f"{src_uri} not defined")

        # Check if the destination already exists
        dst_collection = self._db[dst_uri.purpose]
        dst_datum = dst_collection.find_one({"uri": dst_uri_raw})
        if dst_datum is not None:
            raise ValueError(f"{dst_uri} already defined")

        # Insert the destination datum and delete the source
        result = dst_collection.insert_one(
            dict(uri=dst_uri_raw, datum=src_datum["datum"])
        )
        if not result.acknowledged:
            raise RuntimeError(f"Failed to rename {src_uri} to {dst_uri}")
        result = src_collection.delete_one({"uri": src_uri_raw})
        if result.acknowledged:
            return
        raise RuntimeError(
            f"Failed to delete {src_uri} after renaming to {dst_uri}"
        )

    def lookup(self, uri: Uri, params: set[str] | None = None):
        # Check if the Uri is wild
        if uri.is_wild():
            raise ValueError(f"{uri} is a wildcard URI and cannot be looked up")

        # Check if the purpose is defined
        if uri.purpose not in self._db.list_collection_names():
            return None

        # Build query
        query = {"uri": {"$in": list(map(str, _iter_uri(uri)))}}
        if params is not None:
            query.update(
                {f"datum.{field}": {"$exists": True} for field in params}
            )

        # Query the collection
        collection = self._collection(uri.purpose)
        results = collection.find(query)

        # Build the heirarchy of data
        data = _namespace()
        for result in results:
            _, parts = Uri.parse_unsafe(result["uri"]).parts()
            _set(data, parts, result["datum"])

        # Get the param values
        _, parts = uri.parts()
        return _get(data, parts, params)

    def unset(self, uri: Uri, params: set[str]):
        # Check if the Uri is wild
        if uri.is_wild():
            raise ValueError(f"{uri} is a wildcard URI and cannot be removed")

        # Unset the parameters
        collection = self._collection(uri.purpose)
        collection.update_one(
            {"uri": str(uri)},
            {"$unset": {f"datum.{param}": "" for param in params}},
        )

    def delete(self, uri: Uri) -> list[Uri]:
        # Find the objects that would be deleted
        collection = self._collection(uri.purpose)
        regex = _uri_to_regex(uri)
        results = list(collection.find({"uri": {"$regex": regex}}))
        if len(results) == 0:
            return []

        # Delete the objects
        result = collection.delete_many({"uri": {"$regex": regex}})
        if not result.acknowledged:
            raise RuntimeError(f"Failed to delete {uri}")

        # Return the URIs of the deleted objects
        return [Uri.parse_unsafe(result["uri"]) for result in results]

    def query(self, uri: Uri, **params):
        # Check if the purpose is defined
        if uri.purpose not in self._db.list_collection_names():
            return dict()

        # Build the query
        regex = _uri_to_regex(uri)
        query = {"uri": {"$regex": regex}}
        for key, value in params.items():
            query[f"datum.{key}"] = value

        # Query the collection
        collection = self._collection(uri.purpose)
        results = list(collection.find(query))
        return {Uri.parse_unsafe(result["uri"]): result["datum"] for result in results}

    def transact(self, uri: Uri) -> MongoTransaction:
        # Check if the Uri is wild
        if uri.is_wild():
            raise ValueError(
                f"{uri} is a wildcard URI and cannot be used for transactions"
            )

        # Open a transaction for the collection
        collection = self._collection(uri.purpose)
        return MongoTransaction(self._client, collection)

    def list_purposes(self) -> list[str]:
        return self._db.list_collection_names()

    def list_entities(self, uri: Uri) -> list[Uri]:
        # Validate input URI
        if uri is None:
            raise ValueError("URI cannot be None")
        if uri.purpose is None:
            raise ValueError("URI must have a purpose")

        # Build query
        regex = _uri_to_regex(uri / "*")
        query = {"uri": {"$regex": regex}}

        # Query the collection
        collection = self._collection(uri.purpose)
        results = list(collection.find(query, projection={"uri": 1}))

        # Return the URIs of the entities, filtering out None results
        parsed_uris = []
        for result in results:
            parsed_uri = Uri.parse_unsafe(result["uri"])
            if parsed_uri is not None:
                parsed_uris.append(parsed_uri)
        return parsed_uris

    def close(self):
        """Close the MongoDB connection to free resources."""
        if hasattr(self, "_client") and self._client is not None:
            self._client.close()
            self._client = None

    def __del__(self):
        """Ensure MongoDB connection is closed on garbage collection."""
        self.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.close()
