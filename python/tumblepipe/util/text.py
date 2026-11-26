"""Small text forms the tools print the same way everywhere."""

_BYTE_UNITS = ('B', 'KB', 'MB', 'GB', 'TB')


def human_bytes(count: float) -> str:
    """``1023 B``, ``1.0 KB``, ``12.3 GB``: whole bytes under a kilobyte, one
    decimal above, in binary steps, TB the largest unit."""
    size = float(count)
    rank = 0
    while size >= 1024 and rank < len(_BYTE_UNITS) - 1:
        size /= 1024
        rank += 1
    return f'{int(size)} B' if rank == 0 else f'{size:.1f} {_BYTE_UNITS[rank]}'


def ordinal(number: int) -> str:
    """``number`` as an English ordinal: 1st, 2nd, 3rd, 4th, 11th, 21st, 111th."""
    if number % 100 in (11, 12, 13):
        return f'{number}th'
    return f'{number}' + {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
