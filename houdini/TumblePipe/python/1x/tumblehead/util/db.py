import asyncio
import json

from watchdog.observers import Observer
from watchdog.events import (
    FileSystemEventHandler,
    FileMovedEvent,
    DirMovedEvent,
    FileModifiedEvent,
    DirModifiedEvent,
    FileCreatedEvent,
    DirCreatedEvent,
    FileDeletedEvent,
    DirDeletedEvent
)

from tumblehead.util import uri as muri

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

def _is_item(path):
    if isinstance(path, muri.WildcardItem): return True
    if isinstance(path, muri.NamedItem): return True
    return False

class MemoryStore(Store):
    def __init__(self):
        self._data = dict()
    
    def insert(self, uri, datum):

        # Check if source does not exist
        curr_path = uri.path
        curr_data = self._data
        while not _is_item(curr_path):
            assert isinstance(curr_path, muri.NamedSection), f'{uri} is a valid uri for insertion'
            if curr_path.name not in curr_data:
                curr_data[curr_path.name] = dict()
            curr_data = curr_data[curr_path.name]
            curr_path = curr_path.path
        assert isinstance(curr_path, muri.NamedItem), f'{uri} is a valid uri for insertion'
        assert curr_path.name not in curr_data, f'{uri} already defined'
        
        # Insert
        curr_data[curr_path.name] = datum

    def update(self, uri, datum):

        # Check if source exists
        curr_path = uri.path
        curr_data = self._data
        while not _is_item(curr_path):
            assert isinstance(curr_path, muri.NamedSection), f'{uri} is a valid uri for update'
            assert curr_path.name in curr_data, f'{uri} not defined'
            curr_data = curr_data[curr_path.name]
            curr_path = curr_path.path
        assert isinstance(curr_path, muri.NamedItem), f'{uri} is a valid uri for update'
        assert curr_path.name in curr_data, f'{uri} not defined'
        
        # Update
        curr_data[curr_path.name] = datum

    def rename(self, src_uri, dst_uri):

        # Check if source exists
        src_curr_path = src_uri.path
        src_curr_data = self._data
        while not _is_item(src_curr_path):
            assert isinstance(src_curr_path, muri.NamedSection), f'{src_uri} is a valid uri for rename'
            assert src_curr_path.name in src_curr_data, f'{src_uri} not defined'
            src_curr_data = src_curr_data[src_curr_path.name]
            src_curr_path = src_curr_path.path
        assert isinstance(src_curr_path, muri.NamedItem), f'{src_uri} is a valid uri for rename'
        assert src_curr_path.name in src_curr_data, f'{src_uri} not defined'

        # Check if destination does not exist
        dst_curr_path = dst_uri.path
        dst_curr_data = self._data
        while not _is_item(dst_curr_path):
            assert isinstance(dst_curr_path, muri.NamedSection), f'{dst_uri} is a valid uri for rename'
            if dst_curr_path.name not in dst_curr_data:
                dst_curr_data[dst_curr_path.name] = dict()
            dst_curr_data = dst_curr_data[dst_curr_path.name]
            dst_curr_path = dst_curr_path.path
        assert isinstance(dst_curr_path, muri.NamedItem), f'{dst_uri} is a valid uri for rename'
        assert dst_curr_path.name not in dst_curr_data, f'{dst_uri} already defined'
        
        # Copy and delete
        dst_curr_data[dst_curr_path.name] = src_curr_data[src_curr_path.name].copy()
        del src_curr_data[src_curr_path.name]
    
    def delete(self, uri):
        
        # Check if source exists
        curr_path = uri.path
        curr_data = self._data
        while not _is_item(curr_path):
            assert isinstance(curr_path, muri.NamedSection), f'{uri} is a valid uri for deletion'
            assert curr_path.name in curr_data, f'{uri} not defined'
            curr_data = curr_data[curr_path.name]
            curr_path = curr_path.path
        assert isinstance(curr_path, muri.NamedItem), f'{uri} is a valid uri for deletion'
        assert curr_path.name in curr_data, f'{uri} not defined'
        
        # Delete
        del curr_data[curr_path.name]
    
    def query(self, uri, params):

        def _flatten(list_of_lists):
            return [item for sublist in list_of_lists for item in sublist]
        
        def _section(path, data):
            if isinstance(path, muri.WildcardSection):
                return list(data.values())
            if isinstance(path, muri.NamedSection):
                return [data[path.name]]
            assert False, f'{path} is not a valid section'
        
        def _item(path, data):
            if isinstance(path, muri.WildcardItem):
                return list(data.values())
            if isinstance(path, muri.NamedItem):
                return [data[path.name]]
            assert False, f'{path} is not a valid item'
        
        def _included(params, datum):
            for key, value in params.items():
                if key not in datum: return False
                if datum[key] != value: return False
            return True

        # Step through the sections
        curr_path = uri.path
        curr_data = [self._data]
        while not _is_item(curr_path):
            curr_data = _flatten(map(
                lambda data: _section(curr_path, data),
                curr_data
            ))
            curr_path = curr_path.path
        
        # Final item step
        curr_data = _flatten(map(
            lambda data: _item(curr_path, data),
            curr_data
        ))
        
        # Filter by params
        return list(filter(
            lambda datum: _included(params, datum),
            curr_data
        ))

def _path_to_uri(root_path, item_path):
    _item_path = item_path.relative_to(root_path)
    _parts = str(_item_path.parent).replace('\\', '/').split('/')
    _parts.append(_item_path.stem)
    return muri.parse('/'.join(_parts))

def _uri_to_path(root_path, item_uri):
    _parts = [item_uri.path.name]
    while isinstance(item_uri.path, muri.Section):
        item_uri = item_uri.path
        _parts.append(item_uri.name)
    return root_path / '/'.join(_parts) / f'{item_uri.name}.json'

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
        for file_path in self._path.rglob('*.json'):
            if not file_path.is_file(): continue
            uri = _path_to_uri(self._path, file_path)
            with file_path.open('r') as file:
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
                    for file_path in src_path.rglob('*.json'):
                        if not file_path.is_file(): continue
                        src_uri = _path_to_uri(self._path, file_path)
                        dst_uri = _path_to_uri(self._path, dst_path / file_path.relative_to(src_path))
                        self._store.rename(src_uri, dst_uri)
                case FileModifiedEvent(src_path):
                    uri = _path_to_uri(self._path, src_path)
                    with src_path.open('r') as file:
                        datum = json.load(file)
                    self._store.update(uri, datum)
                case DirModifiedEvent(src_path):
                    for file_path in src_path.rglob('*.json'):
                        if not file_path.is_file(): continue
                        uri = _path_to_uri(self._path, file_path)
                        with file_path.open('r') as file:
                            datum = json.load(file)
                        self._store.update(uri, datum)
                case FileCreatedEvent(src_path):
                    uri = _path_to_uri(self._path, src_path)
                    with src_path.open('r') as file:
                        datum = json.load(file)
                    self._store.insert(uri, datum)
                case DirCreatedEvent(src_path):
                    for file_path in src_path.rglob('*.json'):
                        if not file_path.is_file(): continue
                        uri = _path_to_uri(self._path, file_path)
                        with file_path.open('r') as file:
                            datum = json.load(file)
                        self._store.insert(uri, datum)
                case FileDeletedEvent(src_path):
                    uri = _path_to_uri(self._path, src_path)
                    self._store.delete(uri)
                case DirDeletedEvent(src_path):
                    for file_path in src_path.rglob('*.json'):
                        if not file_path.is_file(): continue
                        uri = _path_to_uri(self._path, file_path)
                        self._store.delete(uri)
                case _:
                    raise NotImplementedError()

    def start(self):
        assert self._task is None, "Indexer is already running"
        self._task = asyncio.create_task(asyncio.gather([
            self._loop.run_in_executor(None, self._schedule),
            self._process()
        ]))
    
    async def stop(self):
        assert self._task is not None, "Indexer is not running"
        self._observer.stop()
        await self._task
        self._task = None

def _prune_empty(root_path, item_path):
    relative_path = item_path.relative_to(root_path)
    while len(relative_path.parts) > 1:
        current_path = root_path / relative_path
        if len(list(current_path.iterdir())) > 0: break
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
        assert not file_path.exists(), f'{uri} already defined'
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open('w+') as file:
            json.dump(datum, file)
    
    def update(self, uri, datum):
        file_path = _uri_to_path(self._path, uri)
        assert file_path.exists(), f'{uri} not defined'
        with file_path.open('w') as file:
            json.dump(datum, file)

    def rename(self, src_uri, dst_uri):
        src_path = _uri_to_path(self._path, src_uri)
        dst_path = _uri_to_path(self._path, dst_uri)
        assert src_path.exists(), f'{src_uri} not defined'
        assert not dst_path.exists(), f'{dst_uri} already defined'
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        src_path.rename(dst_path)
        _prune_empty(self._path, src_path.parent)

    def delete(self, uri):
        file_path = _uri_to_path(self._path, uri)
        assert file_path.exists(), f'{uri} not defined'
        file_path.unlink()
        _prune_empty(self._path, file_path.parent)

    def query(self, uri, params):
        return self._cache.query(uri, params)

if __name__ == '__main__':
    from pathlib import Path

    async def test():
        test_path = Path('~/Desktop/db').expanduser()
        store = FileStore(test_path)
        store.insert(muri.parse('/test'), {'hello': 'world'})
        await asyncio.sleep(10)
        print(store.query(muri.parse('/test'), {}))

    asyncio.run(test())