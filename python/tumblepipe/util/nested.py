"""Read and write nested dicts by a key path."""

from collections.abc import Sequence


def set_in(data: dict, path: Sequence, value) -> None:
    """``data[path[0]]...[path[-1]] = value``, creating the dicts between."""
    for key in path[:-1]:
        data = data.setdefault(key, {})
    data[path[-1]] = value


def get_in(data: dict, path: Sequence):
    """The value at ``path``; None when any key on the way is missing."""
    for key in path:
        if data is None:
            return None
        data = data.get(key)
    return data


def contains_in(data: dict, path: Sequence) -> bool:
    """Whether a non-None value sits at ``path``."""
    return get_in(data, path) is not None
