"""How the pipeline names entities, versions and asset instances.

These rules are the package's, not a project's: version names in particular
are spelled ``vNNNN`` by the resolver and by every path builder, so a project
could never have changed them. Each project used to carry a copy of this
module as ``_config/naming_convention.py``; migration v11 retired the copies.
"""

from random import choice
from string import ascii_letters, digits


def random_name(
    length: int,
    alphabet: str = digits + ascii_letters
    ) -> str:
    return ''.join(choice(alphabet) for _ in range(length))


def is_valid_entity_name(entity_name: str) -> bool:
    # ASCII-only: str.isalnum() accepts Unicode letters/digits, but an
    # entity name becomes a URI segment and uri.py's NAME_ALPHABET is
    # ASCII, so a Unicode name would validate here yet fail to address.
    return entity_name.isascii() and entity_name.isalnum()


def is_valid_version_name(version_name: str) -> bool:
    # 'v' followed by >= 4 ASCII digits. A hard len==5 rejected v10000
    # (so the >9999th version vanished from listings and the next-version
    # picker re-issued a colliding name). str.isdigit() also accepts
    # Unicode digits, which then crash int() in get_version_code, so
    # require isascii() too.
    if len(version_name) < 5:
        return False
    if not version_name.startswith('v'):
        return False
    code = version_name[1:]
    return code.isascii() and code.isdigit()


def get_version_name(version_code: int) -> str:
    return f'v{str(version_code).zfill(4)}'


def get_version_code(version_name: str) -> int:
    return int(version_name[1:])


def get_instance_name(asset_name: str, instance_index: int) -> str:
    if instance_index == 0:
        return asset_name
    return f'{asset_name}{instance_index}'
