from tumblepipe.naming import NamingConvention

class ProjectNamingConvention(NamingConvention):
    def is_valid_entity_name(self, entity_name: str) -> bool:
        # ASCII-only: str.isalnum() accepts Unicode letters/digits, but an
        # entity name becomes a URI segment and uri.py's NAME_ALPHABET is
        # ASCII, so a Unicode name would validate here yet fail to address.
        return entity_name.isascii() and entity_name.isalnum()

    def is_valid_version_name(self, version_name: str) -> bool:
        # 'v' followed by >= 4 ASCII digits. A hard len==5 rejected v10000
        # (so the >9999th version vanished from listings and the next-version
        # picker re-issued a colliding name). str.isdigit() also accepts
        # Unicode digits, which then crash int() in get_version_code, so
        # require isascii() too.
        if len(version_name) < 5: return False
        if not version_name.startswith('v'): return False
        digits = version_name[1:]
        if not (digits.isascii() and digits.isdigit()): return False
        return True
    
    def get_version_name(self, version_code: int) -> str:
        return f'v{str(version_code).zfill(4)}'
    
    def get_version_code(self, version_name: str) -> int:
        return int(version_name[1:])

    def get_instance_name(self, asset_name: str, instance_index: int) -> str:
        if instance_index == 0: return asset_name
        return f'{asset_name}{instance_index}'

def create():
    return ProjectNamingConvention()