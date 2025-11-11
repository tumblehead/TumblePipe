from tumblehead.naming import NamingConvention

class ProjectNamingConvention(NamingConvention):
    def is_valid_sequence_name(self, sequence_name):
        return self.is_valid_sequence_code(sequence_name)

    def is_valid_shot_name(self, shot_name):
        return self.is_valid_shot_code(shot_name)

    def is_valid_version_name(self, version_name):
        if not len(version_name) == 5: return False
        if not version_name.startswith('v'): return False
        if not version_name[1:].isdigit(): return False
        return True

    def get_version_name(self, version_code):
        return f'v{str(version_code).zfill(4)}'

    def get_version_code(self, version_name):
        return int(version_name[1:])

    def get_instance_name(self, asset_name, instance_index):
        if instance_index == 0: return asset_name
        return f'{asset_name}{instance_index}'

def create():
    return ProjectNamingConvention()