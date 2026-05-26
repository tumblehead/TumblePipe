from pathlib import Path

from tumblepipe.storage import StorageConvention
from tumblepipe.util.uri import Uri
from tumblepipe.api import (
    get_config_path,
    get_project_path,
    get_pipeline_path,
    get_project_name,
)


class ProjectStorageConvention(StorageConvention):
    def __init__(self):
        project_name = get_project_name()
        self.project_path = get_project_path()
        self.pipeline_path = get_pipeline_path()
        self.config_path = get_config_path()
        self.assets_path = self.project_path / 'assets'
        self.shots_path = self.project_path / 'shots'
        self.groups_path = self.project_path / 'groups'
        self.kits_path = self.project_path / 'kits'
        self.export_path = self.project_path / 'export'
        self.cache_path = self.project_path.parent / f'{project_name}_cache'
        self.render_path = self.project_path.parent / f'{project_name}_render'
        self.turntable_path = self.project_path.parent / f'{project_name}_turntable'
        self.temp_path = self.project_path.parent / f'{project_name}_temp'
        self.proxy_path = self.project_path.parent / f'{project_name}_proxy'
        self.preset_path = self.config_path / 'presets'
        self.edit_path = self.project_path / 'edit'

    def resolve(self, uri: Uri):
        normalized = self._normalize_input(uri)
        if normalized is None:
            return None
        purpose, parts = normalized
        match purpose:
            case 'temp': return self.temp_path / Path(*parts) if parts else self.temp_path
            case 'proxy': return self.proxy_path / Path(*parts) if parts else self.proxy_path
            case 'project': return self.project_path / Path(*parts) if parts else self.project_path
            case 'pipeline': return self.pipeline_path / Path(*parts) if parts else self.pipeline_path
            case 'config': return self.config_path / Path(*parts) if parts else self.config_path
            case 'assets': return self.assets_path / Path(*parts) if parts else self.assets_path
            case 'shots': return self.shots_path / Path(*parts) if parts else self.shots_path
            case 'groups': return self.groups_path / Path(*parts) if parts else self.groups_path
            case 'kits': return self.kits_path / Path(*parts) if parts else self.kits_path
            case 'export': return self.export_path / Path(*parts) if parts else self.export_path
            case 'cache': return self.cache_path / Path(*parts) if parts else self.cache_path
            case 'render': return self.render_path / Path(*parts) if parts else self.render_path
            case 'turntable': return self.turntable_path / Path(*parts) if parts else self.turntable_path
            case 'preset': return self.preset_path / Path(*parts) if parts else self.preset_path
            case 'edit': return self.edit_path / Path(*parts) if parts else self.edit_path
            case _: return None


def create():
    return ProjectStorageConvention()
