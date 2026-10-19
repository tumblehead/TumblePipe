"""Houdini parameter-menu helpers: the flat [token, label, ...] lists menu scripts return."""

def list_to_menu(items: list[str]) -> list[str]:
    result = []
    for item in items:
        result.append(item)
        result.append(item)
    return result


def list_to_checked_menu(items: list[str], checked: list[str]) -> list[str]:
    result = list()
    for item in items:
        result.append(item)
        result.append(f'{item} <--' if item in checked else item)
    return result
