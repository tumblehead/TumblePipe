from pathlib import Path
import json

from tumblehead.config import ConfigConvention, FrameRange
from tumblehead.api import get_config_path, path_str

def _load_json(path: Path):
    with path.open('r') as file:
        return json.load(file)

def _store_json(path: Path, data):
    with path.open('w') as file:
        json.dump(data, file, indent=4)

def _contains(data, *key_path):
    for key in key_path[:-1]:
        if not isinstance(data, dict): return False
        if key not in data: return False
        data = data[key]
    return key_path[-1] in data

def _get(data, *key_path):
    for key in key_path:
        if not isinstance(data, dict): return None
        if key not in data: return None
        data = data[key]
    return data

def _set(data, value, *key_path):
    for key in key_path[:-1]:
        if not isinstance(data, dict): return False
        if key not in data:
            data[key] = dict()
        data = data[key]
    data[key_path[-1]] = value
    return True

def _del(data, *key_path):
    for key in key_path[:-1]:
        if not isinstance(data, dict): return False
        if key not in data: return False
        data = data[key]
    if not isinstance(data, dict): return False
    if key_path[-1] not in data: return False
    del data[key_path[-1]]
    return True

def _append(data, value, *key_path):
    for key in key_path[:-1]:
        if not isinstance(data, dict): return False
        if key not in data:
            data[key] = dict()
        data = data[key]
    data[key_path[-1]].append(value)
    return True

def _remove(data, *key_path):
    for key in key_path[:-1]:
        if not isinstance(data, dict): return False
        if key not in data: return False
        data = data[key]
    if not isinstance(data, list): return False
    if key_path[-1] not in data: return False
    data.remove(key_path[-1])
    return True

class ProjectConfigConvention(ConfigConvention):
    def __init__(self):

        # Paths
        self.config_path = get_config_path()
        self.shot_info_path = self.config_path / 'shot_info.json'
        self.asset_info_path = self.config_path / 'asset_info.json'
        self.kit_info_path = self.config_path / 'kit_info.json'
        self.department_info_path = self.config_path / 'department_info.json'
        self.procedural_info_path = self.config_path / 'procedural_info.json'

        # Cached data
        self.shot_info = None
        self.asset_info = None
        self.kit_info = None
        self.department_info = None
        self.default_values = dict()
        self.procedural_info = None

    def _load_shot_info(self):
        self.shot_info = _load_json(self.shot_info_path)

    def _save_shot_info(self):
        _store_json(self.shot_info_path, self.shot_info)

    def _load_asset_info(self):
        self.asset_info = _load_json(self.asset_info_path)

    def _save_asset_info(self):
        _store_json(self.asset_info_path, self.asset_info)

    def _load_kit_info(self):
        self.kit_info = _load_json(self.kit_info_path)

    def _save_kit_info(self):
        _store_json(self.kit_info_path, self.kit_info)

    def _load_department_info(self):
        self.department_info = _load_json(self.department_info_path)

    def _save_department_info(self):
        _store_json(self.department_info_path, self.department_info)

    def _load_procedural_info(self):
        self.procedural_info = _load_json(self.procedural_info_path)

    def list_sequence_names(self):
        if self.shot_info is None: self._load_shot_info()
        return list(self.shot_info.keys())

    def list_shot_names(self, sequence_name):
        if self.shot_info is None: self._load_shot_info()
        return list(self.shot_info.get(sequence_name, dict()).keys())

    def list_category_names(self):
        if self.asset_info is None: self._load_asset_info()
        return list(self.asset_info.keys())

    def list_asset_names(self, category_name):
        if self.asset_info is None: self._load_asset_info()
        assets = self.asset_info.get(category_name, list())
        return [asset['name'] for asset in assets]

    def get_asset_animatable(self, category_name, asset_name):
        if self.asset_info is None: self._load_asset_info()
        assets = self.asset_info.get(category_name, list())
        for asset in assets:
            if asset['name'] == asset_name:
                return asset['animatable']
        return None

    def list_kit_category_names(self):
        if self.kit_info is None: self._load_kit_info()
        return list(self.kit_info.keys())

    def list_kit_names(self, category_name):
        if self.kit_info is None: self._load_kit_info()
        return self.kit_info.get(category_name, list())

    def list_asset_department_names(self):
        if self.department_info is None: self._load_department_info()
        return self.department_info.get('asset', list())

    def list_shot_department_names(self):
        if self.department_info is None: self._load_department_info()
        return self.department_info.get('shot', list())

    def list_kit_department_names(self):
        if self.department_info is None: self._load_department_info()
        return self.department_info.get('kit', list())

    def list_render_department_names(self):
        if self.department_info is None: self._load_department_info()
        return self.department_info.get('render', list())

    def list_render_layer_names(self, sequence_name, shot_name):
        if self.shot_info is None: self._load_shot_info()
        infos = self.shot_info.get(sequence_name, None)
        if infos is None: return list()
        info = infos.get(shot_name, None)
        if info is None: return list()
        return info.get('render_layers', list())

    def get_frame_range(self, sequence_name, shot_name):
        if self.shot_info is None: self._load_shot_info()
        infos = self.shot_info.get(sequence_name, None)
        if infos is None: return None
        info = infos.get(shot_name, None)
        if info is None: return None
        return FrameRange(
            info['frame_start'],
            info['frame_end'],
            info['roll_start'],
            info['roll_end']
        )
    
    def list_asset_procedural_names(self, category_name, asset_name):
        if self.procedural_info is None: self._load_procedural_info()
        included = _get(
            self.procedural_info,
            'asset_includes',
            category_name,
            asset_name
        )
        if included is None: return []
        return included.copy()

    def list_kit_procedural_names(self, category_name, kit_name):
        if self.procedural_info is None: self._load_procedural_info()
        included = _get(
            self.procedural_info,
            'kit_includes',
            category_name,
            kit_name
        )
        if included is None: return []
        return included.copy()
    
    def list_shot_asset_procedural_names(
        self,
        sequence_name,
        shot_name,
        category_name,
        asset_name
        ):
        included = self.list_asset_procedural_names(category_name, asset_name)
        excluded = _get(
            self.procedural_info,
            'shot_excludes',
            sequence_name,
            shot_name,
            "assets",
            category_name,
            asset_name
        )
        if excluded is None: return included
        return [name for name in included if name not in excluded]
    
    def list_shot_kit_procedural_names(
        self,
        sequence_name,
        shot_name,
        category_name,
        kit_name
        ):
        included = self.list_kit_procedural_names(category_name, kit_name)
        excluded = _get(
            self.procedural_info,
            'shot_excludes',
            sequence_name,
            shot_name,
            "kits",
            category_name,
            kit_name
        )
        if excluded is None: return included
        return [name for name in included if name not in excluded]

    def add_sequence_name(self, sequence_name):
        if self.shot_info is None: self._load_shot_info()
        if _contains(self.shot_info, sequence_name): return
        updated = _set(self.shot_info, dict(), sequence_name)
        if updated: self._save_shot_info()

    def add_shot_name(self, sequence_name, shot_name):
        if self.shot_info is None: self._load_shot_info()
        if _contains(self.shot_info, sequence_name, shot_name): return
        fps = self.get_fps()
        default_shot_info = dict(
            frame_start = 1001,
            frame_end = 1100,
            roll_start = 0,
            roll_end = 0,
            fps = fps,
            render_layers = ['main']
        )
        updated = _set(
            self.shot_info,
            default_shot_info,
            sequence_name, shot_name
        )
        if updated: self._save_shot_info()

    def add_category_name(self, category_name):
        if self.asset_info is None: self._load_asset_info()
        if _contains(self.asset_info, category_name): return
        updated = _set(self.asset_info, list(), category_name)
        if updated: self._save_asset_info()

    def add_asset_name(self, category_name, asset_name, animatable=False):
        if self.asset_info is None: self._load_asset_info()
        assets = self.asset_info.get(category_name, list())
        if any(asset['name'] == asset_name for asset in assets):
            return
        asset_obj = {"name": asset_name, "animatable": animatable}
        updated = _append(self.asset_info, asset_obj, category_name)
        if updated: self._save_asset_info()

    def add_kit_category_name(self, kit_category_name):
        if self.kit_info is None: self._load_kit_info()
        if _contains(self.kit_info, kit_category_name): return
        updated = _set(self.kit_info, list(), kit_category_name)
        if updated: self._save_kit_info()

    def add_kit_name(self, kit_category_name, kit_name):
        if self.kit_info is None: self._load_kit_info()
        if _contains(self.kit_info, kit_category_name, kit_name): return
        updated = _append(self.kit_info, kit_name, kit_category_name)
        if updated: self._save_kit_info()

    def remove_sequence_name(self, sequence_name):
        if self.shot_info is None: self._load_shot_info()
        if not _contains(self.shot_info, sequence_name): return
        updated = _del(self.shot_info, sequence_name)
        if updated: self._save_shot_info()

    def remove_shot_name(self, sequence_name, shot_name):
        if self.shot_info is None: self._load_shot_info()
        if not _contains(self.shot_info, sequence_name, shot_name): return
        updated = _del(self.shot_info, sequence_name, shot_name)
        if updated: self._save_shot_info()

    def remove_category_name(self, category_name):
        if self.asset_info is None: self._load_asset_info()
        if not _contains(self.asset_info, category_name): return
        updated = _del(self.asset_info, category_name)
        if updated: self._save_asset_info()

    def remove_asset_name(self, category_name, asset_name):
        if self.asset_info is None: self._load_asset_info()
        assets = self.asset_info.get(category_name, list())
        for i, asset in enumerate(assets):
            if asset['name'] == asset_name:
                assets.pop(i)
                self._save_asset_info()
                return

    def remove_kit_category_name(self, kit_category_name):
        if self.kit_info is None: self._load_kit_info()
        if not _contains(self.kit_info, kit_category_name): return
        updated = _del(self.kit_info, kit_category_name)
        if updated: self._save_kit_info()

    def remove_kit_name(self, kit_category_name, kit_name):
        if self.kit_info is None: self._load_kit_info()
        if not _contains(self.kit_info, kit_category_name, kit_name): return
        updated = _remove(self.kit_info, kit_category_name, kit_name)
        if updated: self._save_kit_info()

    def get_fps(self):
        return 24

    def get_shot_fps(self, sequence_name, shot_name):
        if self.shot_info is None: self._load_shot_info()
        default_fps = self.get_fps()
        infos = self.shot_info.get(sequence_name, None)
        if infos is None: return default_fps
        info = infos.get(shot_name, None)
        if info is None: return default_fps
        return info.get('fps', default_fps)

    def resolve(self, path):
        if not self.is_valid_path(path): return None
        purpose, parts = self.parse_path(path)
        path_parts, file_name = parts[:-1], parts[-1]
        file_path = (
            self.config_path /
            purpose /
            Path(*path_parts) /
            f'{file_name}.json'
        )
        path_key = path_str(file_path)
        if path_key in self.default_values: return self.default_values[path_key]
        result = _load_json(file_path) if file_path.exists() else None
        self.default_values[path_key] = result
        return result

def create():
    return ProjectConfigConvention()