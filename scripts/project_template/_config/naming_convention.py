from tumblehead.naming import NamingConvention

class ProjectNamingConvention(NamingConvention):
    def is_valid_entity_name(self, entity_name: str) -> bool:
        return entity_name.isalnum()

    def is_valid_version_name(self, version_name: str) -> bool:
        if not len(version_name) == 5: return False
        if not version_name.startswith('v'): return False
        if not version_name[1:].isdigit(): return False
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