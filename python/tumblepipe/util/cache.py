class Cache:
    def __init__(self):
        self._data = dict()

    def clear(self):
        self._data.clear()
    
    def contains(self, keys):
        assert len(keys) > 0, 'keys must not be empty'
        _data = self._data
        for key in keys:
            if key not in _data: return False
            _data = _data[key]
        return True

    def insert(self, keys, value):
        assert len(keys) > 0, 'keys must not be empty'
        _data = self._data
        for key in keys[:-1]:
            if key not in _data: _data[key] = dict()
            _data = _data[key]
        _data[keys[-1]] = value

    def remove(self, keys):
        assert len(keys) > 0, 'keys must not be empty'
        _data = self._data
        for key in keys[:-1]:
            if key not in _data: return
            _data = _data[key]
        if keys[-1] in _data: del _data[keys[-1]]
    
    def lookup(self, keys):
        assert len(keys) > 0, 'keys must not be empty'
        _data = self._data
        for key in keys:
            if key not in _data: return None
            _data = _data[key]
        return _data