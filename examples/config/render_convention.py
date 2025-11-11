import json

from tumblehead.render import RenderConvention
from tumblehead.api import get_config_path

def _load_json(path):
    with open(path, 'r') as f:
        return json.load(f)

class ProjectRenderConvention(RenderConvention):
    def __init__(self):

        # Paths
        self.config_path = get_config_path()
        self.render_info_path = self.config_path / 'render_info.json'

        # Cached data
        self.render_info = None

    def _load_render_info(self):
        self.render_info = _load_json(self.render_info_path)

    def list_included_asset_department_names(self, render_department):
        if self.render_info is None: self._load_render_info()
        return list(self.render_info[render_department]['asset'])

    def list_included_kit_department_names(self, render_department):
        if self.render_info is None: self._load_render_info()
        return list(self.render_info[render_department]['kit'])

    def list_included_shot_department_names(self, render_department):
        if self.render_info is None: self._load_render_info()
        return list(self.render_info[render_department]['shot'])

def create():
    return ProjectRenderConvention()