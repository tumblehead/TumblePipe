from functools import partial

from qtpy.QtCore import (
    QObject,
    QTimer
)

class Delay(QObject):
    def __init__(
        self,
        delay,
        callback,
        parent = None
        ):
        super().__init__(parent = parent)

        # Members
        self._timer = QTimer()
        self._delay = delay
        self._callback = callback
        self._args = None
    
        # Setup timer
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._apply)

    def __del__(self):
        self._timer.stop()
        self._timer.deleteLater()
        self._args = None
    
    def _apply(self):
        self._callback(*self._args)
        self._args = None
    
    def __call__(self, *args):
        self._timer.stop()
        self._args = args
        self._timer.start(self._delay)

class DelayCache:
    def __init__(self, delay):

        # Members
        self._delay = delay
        self._cache = {}
        self._timers = {}

    def __contains__(self, key):
        return key in self._cache
    
    def __getitem__(self, key):
        if key not in self._cache: return None
        timer, value = self._cache[key]
        timer.stop()
        timer.start(self._delay)
        return value
    
    def __setitem__(self, key, value):

        def _get_timer():
            if key in self._cache: return self._cache[key][0]
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(partial(self._pop_entry, key))
            return timer

        timer = _get_timer()
        self._cache[key] = (timer, value)
        timer.start(self._delay)
    
    def _pop_entry(self, key):
        if key not in self._cache: return
        timer, _value = self._cache.pop(key)
        timer.stop()