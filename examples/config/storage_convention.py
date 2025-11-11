from pathlib import Path

from tumblehead.storage import StorageConvention
from tumblehead.api import (
    get_config_path,
    get_project_path,
    get_pipeline_path,
    get_project_name
)

class ProjectStorageConvention(StorageConvention):
    def __init__(self):
        _primary_path = Path('/mnt/c')
        _secondary_path = Path('/mnt/e')
        _home = _primary_path / 'users' / 'tumblehead'
        project_name = get_project_name()
        self.temp_path = _home / 'th_temp' / project_name
        self.proxy_path = _home / 'th_proxy' / project_name
        self.project_path = get_project_path()
        self.pipeline_path = get_pipeline_path()
        self.config_path = get_config_path()
        self.assets_path = self.project_path / 'assets'
        self.shots_path = self.project_path / 'shots'
        self.kits_path = self.project_path / 'kits'
        self.export_path = self.project_path / 'export'
        self.cache_path = self.project_path.parent / f'{project_name}_cache'
        self.render_path = self.project_path.parent / f'{project_name}_render'
        self.turntable_path = self.project_path.parent / f'{project_name}_turntable'
        self.preset_path = self.config_path / 'presets'
        self.edit_path = _secondary_path / 'edit' / project_name

    def resolve(self, path):
        if not self.is_valid_path(path): return None
        purpose, parts = self.parse_path(path)
        match purpose:
            case 'temp': return self.temp_path / Path(*parts)
            case 'proxy': return self.proxy_path / Path(*parts)
            case 'project': return self.project_path / Path(*parts)
            case 'pipeline': return self.pipeline_path / Path(*parts)
            case 'config': return self.config_path / Path(*parts)
            case 'assets': return self.assets_path / Path(*parts)
            case 'shots': return self.shots_path / Path(*parts)
            case 'kits': return self.kits_path / Path(*parts)
            case 'export': return self.export_path / Path(*parts)
            case 'cache': return self.cache_path / Path(*parts)
            case 'render': return self.render_path / Path(*parts)
            case 'turntable': return self.turntable_path / Path(*parts)
            case 'preset': return self.preset_path / Path(*parts)
            case 'edit': return self.edit_path / Path(*parts)
            case _: assert False, f'Unknown path purpose "{purpose}"'

def create():
    return ProjectStorageConvention()