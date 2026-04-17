from string import digits, ascii_letters
from random import choice

def random_name(
    length: int,
    alphabet: str = digits + ascii_letters
    ) -> str:
    return ''.join(choice(alphabet) for _ in range(length))

class NamingConvention:
    def is_valid_entity_name(self, entity_name: str) -> bool:
        raise NotImplementedError()

    def is_valid_version_name(self, version_name: str) -> bool:
        raise NotImplementedError()
    
    def get_version_name(self, version_code: int) -> str:
        raise NotImplementedError()
    
    def get_version_code(self, version_name: str) -> int:
        raise NotImplementedError()
    
    def get_instance_name(self, asset_name: str, instance_index: int) -> str:
        raise NotImplementedError()