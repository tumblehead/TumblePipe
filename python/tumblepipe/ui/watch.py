from dataclasses import dataclass
from typing import Optional
from pathlib import Path
from enum import Enum

from qtpy.QtCore import QObject, Signal

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

class EventTag(Enum):
    Created = 'created'
    Deleted = 'deleted'
    Modified = 'modified'
    Moved = 'moved'

@dataclass
class Event:
    tag: EventTag
    src_path: Path
    dest_path: Optional[Path]

class Watcher(QObject, FileSystemEventHandler):
    change = Signal(object)

    def __init__(
        self,
        path: Path,
        parent: Optional[QObject] = None
        ):
        QObject.__init__(self, parent = parent)
        FileSystemEventHandler.__init__(self)

        # Ensure the path exists
        path.mkdir(parents = True, exist_ok = True)

        # Members
        self._path = path
        self._watching = False
        self._observer = Observer()
        self._file_watchers = dict()
    
    def __del__(self):
        self.stop()
    
    def get_path(self):
        return self._path
    
    def is_watching(self):
        return self._watching

    def start(self):
        if self._watching: return
        self._observer.schedule(self, str(self._path))
        self._observer.start()
        self._watching = True

    def stop(self):
        if not self._watching: return
        self._observer.stop()
        self._observer.join()
        self._watching = False
    
    def set_watching(self, watching: bool):
        if watching: self.start()
        else: self.stop()
    
    def on_created(self, event):
        self.change.emit(Event(
            tag = EventTag.Created,
            src_path = Path(event.src_path),
            dest_path = None
        ))
    
    def on_deleted(self, event):
        self.change.emit(Event(
            tag = EventTag.Deleted,
            src_path = Path(event.src_path),
            dest_path = None
        ))
    
    def on_modified(self, event):
        self.change.emit(Event(
            tag = EventTag.Modified,
            src_path = Path(event.src_path),
            dest_path = None
        ))
    
    def on_moved(self, event):
        self.change.emit(Event(
            tag = EventTag.Moved,
            src_path = Path(event.src_path),
            dest_path = Path(event.dest_path)
        ))

class PathWatcher(QObject):
    change = Signal(object)

    def __init__(
        self,
        path: Path,
        watcher: Optional[Watcher] = None,
        parent: Optional[QObject] = None
        ):
        super().__init__(parent = parent)

        # Check that the path is a child of the watcher
        if watcher is not None and path.parent != watcher.get_path():
            raise ValueError('Path must be a child of the watcher path')

        # Members
        self._path = path
        self._watcher = (
            Watcher(path.parent)
            if watcher is None else watcher
        )

        # Connect signals
        self._watcher.change.connect(self._on_change)
    
    def __del__(self):
        self._watcher.change.disconnect(self._on_change)
    
    def start(self):
        self._watcher.start()
    
    def stop(self):
        self._watcher.stop()
    
    def is_watching(self):
        return self._watcher.is_watching()

    def set_watching(self, watching: bool):
        self._watcher.set_watching(watching)
    
    def get_path(self):
        return self._path
    
    def _on_change(self, event):
        if self._path != event.src_path: return
        self.change.emit(event)