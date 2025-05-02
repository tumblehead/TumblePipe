from string import digits, ascii_letters
from random import choice

def random_name(
    length: int,
    alphabet: str = digits + ascii_letters
    ) -> str:
    return ''.join(choice(alphabet) for _ in range(length))

class NamingConvention:
    def is_valid_sequence_name(self, sequence_name):
        raise NotImplementedError()
    
    def is_valid_shot_name(self, shot_name):
        raise NotImplementedError()
    
    def is_valid_version_name(self, version_name):
        raise NotImplementedError()
    
    def get_version_name(self, version_code):
        raise NotImplementedError()
    
    def get_version_code(self, version_name):
        raise NotImplementedError()
    
    def get_instance_name(self, asset_name, instance_index):
        raise NotImplementedError()