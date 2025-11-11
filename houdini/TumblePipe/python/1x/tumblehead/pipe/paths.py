from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

from tumblehead.api import default_client
from tumblehead.config import BlockRange
from tumblehead.util.io import load_json

api = default_client()

###############################################################################
# Version Paths
###############################################################################
def get_next_version_name(version_name: str) -> str:
    version_code = api.naming.get_version_code(version_name) + 1
    return api.naming.get_version_name(version_code)

def list_version_paths(path: Path) -> list[Path]:
    if not path.exists(): return []
    version_paths = [
        version_path
        for version_path in path.iterdir()
        if (version_path.is_dir() and
            api.naming.is_valid_version_name(version_path.name))
    ]
    version_paths.sort(key = lambda version_path: api.naming.get_version_code(version_path.name))
    return version_paths

def get_latest_version_path(path: Path) -> Optional[Path]:
    version_paths = list_version_paths(path)
    if len(version_paths) == 0: return None
    return version_paths[-1]

def get_next_version_path(path: Path) -> Path:
    version_paths = list_version_paths(path)
    if len(version_paths) == 0: return path / 'v0001'
    version_name = version_paths[-1].name
    next_version_name = get_next_version_name(version_name)
    return path / next_version_name

###############################################################################
# Entities
###############################################################################
@dataclass(frozen=True)
class Entity:

    @staticmethod
    def from_json(data: dict) -> Optional['Entity']:
        match data.get('tag'):
            case 'shot': return ShotEntity.from_json(data)
            case 'kit': return KitEntity.from_json(data)
            case 'asset': return AssetEntity.from_json(data)
        return None

@dataclass(frozen=True)
class ShotEntity(Entity):
    sequence_name: str
    shot_name: str
    department_name: Optional[str] = field(default = None)

    def __str__(self) -> str:
        return (
            f'shot:/'
            f'{self.sequence_name}/'
            f'{self.shot_name}/'
            f'{self.department_name}'
        )

    @staticmethod
    def from_json(data: dict) -> Optional['ShotEntity']:
        assert data.get('tag') == 'shot'
        assert data.get('sequence_name') is not None
        assert data.get('shot_name') is not None
        return ShotEntity(
            sequence_name = data.get('sequence_name'),
            shot_name = data.get('shot_name'),
            department_name = data.get('department_name')
        )

    def to_json(self) -> dict:
        return dict(
            tag = 'shot',
            sequence_name = self.sequence_name,
            shot_name = self.shot_name,
            department_name = self.department_name
        )

@dataclass(frozen=True)
class KitEntity(Entity):
    category_name: str
    kit_name: str
    department_name: Optional[str] = field(default = None)

    def __str__(self) -> str:
        return (
            f'kit:/'
            f'{self.category_name}/'
            f'{self.kit_name}/'
            f'{self.department_name}'
        )

    @staticmethod
    def from_json(data: dict) -> Optional['KitEntity']:
        assert data.get('tag') == 'kit'
        assert data.get('category_name') is not None
        assert data.get('kit_name') is not None
        return KitEntity(
            category_name = data.get('category_name'),
            kit_name = data.get('kit_name'),
            department_name = data.get('department_name')
        )
    
    def to_json(self) -> dict:
        return dict(
            tag = 'kit',
            category_name = self.category_name,
            kit_name = self.kit_name,
            department_name = self.department_name
        )

@dataclass(frozen=True)
class AssetEntity(Entity):
    category_name: str
    asset_name: str
    department_name: Optional[str] = field(default = None)

    def __str__(self) -> str:
        return (
            f'asset:/'
            f'{self.category_name}/'
            f'{self.asset_name}/'
            f'{self.department_name}'
        )

    @staticmethod
    def from_json(data: dict) -> Optional['AssetEntity']:
        assert data.get('tag') == 'asset'
        assert data.get('category_name') is not None
        assert data.get('asset_name') is not None
        return AssetEntity(
            category_name = data.get('category_name'),
            asset_name = data.get('asset_name'),
            department_name = data.get('department_name')
        )

    def to_json(self) -> dict:
        return dict(
            tag = 'asset',
            category_name = self.category_name,
            asset_name = self.asset_name,
            department_name = self.department_name
        )

###############################################################################
# Render Paths
###############################################################################
@dataclass(frozen=True)
class AOV:
    path: Path
    label: str
    name: str
    suffix: str

    def get_aov_frame_path(self, index_pattern: str) -> Path:
        return self.path / f'{self.name}.{index_pattern}.{self.suffix}'
    
    def get_frame_range(self) -> Optional[BlockRange]:
        context_path = self.path.parent / 'context.json'
        context = load_json(context_path)
        if context is None: return None
        first_frame = context.get('first_frame')
        if first_frame is None: return None
        last_frame = context.get('last_frame')
        if last_frame is None: return None
        step_size = context.get('step_size')
        if step_size is None: return None
        return BlockRange(
            first_frame,
            last_frame,
            step_size
        )

    def is_complete(self, expected_frame_range: BlockRange) -> bool:
        frame_path = self.get_aov_frame_path('*')
        actual_frame_indices = list(sorted(map(
            lambda path: int(path.stem.split('.')[-1]),
            frame_path.parent.glob(frame_path.name)
        )))
        expected_count = len(expected_frame_range)
        actual_count = len(actual_frame_indices)
        if expected_count != actual_count: return False
        for expected_frame, actual_frame in zip(
            list(expected_frame_range),
            actual_frame_indices):
            if expected_frame != actual_frame: return False
        return True

@dataclass(frozen=True)
class Layer:
    path: Path
    label: str
    version: str
    aovs: dict[str, AOV]
    name: str
    suffix: str
    
    def get_frame_range(self) -> Optional[BlockRange]:
        context_path = self.path / 'context.json'
        context = load_json(context_path)
        if context is None: return None
        first_frame = context.get('first_frame')
        if first_frame is None: return None
        last_frame = context.get('last_frame')
        if last_frame is None: return None
        step_size = context.get('step_size')
        if step_size is None: return None
        return BlockRange(
            first_frame,
            last_frame,
            step_size
        )

    def get_frame_path(self, index_pattern: str) -> Path:
        return self.path / f'{self.name}.{index_pattern}.{self.suffix}'

    def get_aov_frame_path(
        self,
        aov_name: str,
        index_pattern: str
        ) -> Optional[Path]:
        if aov_name not in self.aovs: return None
        return self.aovs[aov_name].get_aov_frame_path(index_pattern)
    
    def get_aov(self, aov_name: str) -> Optional[AOV]:
        return self.aovs.get(aov_name)
    
    def get_complete_aov(self, aov_name: str) -> Optional[AOV]:
        frame_range = self.get_frame_range()
        if frame_range is None: return None
        aov = self.aovs.get(aov_name)
        if aov is None: return None
        return aov if aov.is_complete(frame_range) else None

    def is_complete(self) -> bool:
        frame_range = self.get_frame_range()
        if frame_range is None: return False
        if len(self.aovs) == 0:
            layer_frame = AOV(
                path = self.path,
                label = self.label,
                name = self.name,
                suffix = self.suffix
            )
            return layer_frame.is_complete(frame_range)
        for aov in self.aovs.values():
            if not aov.is_complete(frame_range): return False
        return True

@dataclass(frozen=True)
class Render:
    path: Path
    layers: dict[str, dict[str, Layer]]

    def get_frame_path(
        self,
        layer_name: str,
        version_name: str,
        index_pattern: str
        ) -> Optional[Path]:
        if layer_name not in self.layers: return None
        if version_name not in self.layers[layer_name]: return None
        return self.layers[layer_name][version_name].get_frame_path(index_pattern)

    def get_aov_frame_path(
        self,
        layer_name: str,
        version_name: str,
        aov_name: str,
        index_pattern: str
        ) -> Optional[Path]:
        if layer_name not in self.layers: return None
        if version_name not in self.layers[layer_name]: return None
        return self.layers[layer_name][version_name].get_aov_frame_path(aov_name, index_pattern)
    
    def get_layer(
        self,
        layer_name: str,
        version_name: str
        ) -> Optional[Layer]:
        if layer_name not in self.layers: return None
        return self.layers[layer_name].get(version_name)

    def get_complete_layer(
        self,
        layer_name: str,
        version_name: str
        ) -> Optional[Layer]:
        if layer_name not in self.layers: return None
        if version_name not in self.layers[layer_name]: return None
        layer = self.layers[layer_name][version_name]
        return layer if layer.is_complete() else None

    def get_latest_layer(
        self,
        layer_name: str
        ) -> Optional[Layer]:
        if layer_name not in self.layers: return None
        version_names = list(self.layers[layer_name].keys())
        if len(version_names) == 0: return None
        version_names.sort(key = api.naming.get_version_code)
        version_name = version_names[-1]
        return self.layers[layer_name][version_name]
    
    def get_latest_complete_layer(
        self,
        layer_name: str,
        expected_frame_range: Optional[BlockRange] = None
        ) -> Optional[Layer]:
        if layer_name not in self.layers: return None
        version_names = list(self.layers[layer_name].keys())
        if len(version_names) == 0: return None
        version_names.sort(key = api.naming.get_version_code)
        for version_name in reversed(version_names):
            layer = self.layers[layer_name][version_name]
            if expected_frame_range is not None:
                # Validate against expected frame range
                if len(layer.aovs) == 0:
                    layer_frame = AOV(
                        path = layer.path,
                        label = layer.label,
                        name = layer.name,
                        suffix = layer.suffix
                    )
                    if not layer_frame.is_complete(expected_frame_range):
                        continue
                else:
                    all_complete = True
                    for aov in layer.aovs.values():
                        if not aov.is_complete(expected_frame_range):
                            all_complete = False
                            break
                    if not all_complete:
                        continue
            else:
                # Use layer's own frame range from context.json
                if not layer.is_complete():
                    continue
            return layer
        return None

    def get_newer_latest_complete_layer(
        self,
        layer_name: str,
        current_version_name: str
        ) -> Optional[Layer]:
        if layer_name not in self.layers: return None
        current_version_code = api.naming.get_version_code(current_version_name)
        candidate_version_codes = list(filter(
            lambda version_code: version_code > current_version_code,
            map(
                api.naming.get_version_code,
                self.layers[layer_name].keys()
            )
        ))
        if len(candidate_version_codes) == 0: return None
        candidate_version_codes.sort()
        for candidate_version_code in reversed(candidate_version_codes):
            candidate_version_name = api.naming.get_version_name(
                candidate_version_code
            )
            candidate_layer = self.layers[layer_name].get(
                candidate_version_name
            )
            if candidate_layer.is_complete(): return candidate_layer
        return None

    def get_layer_aov(
        self,
        layer_name: str,
        version_name: str,
        aov_name: str
        ) -> Optional[AOV]:
        layer = self.get_layer(layer_name, version_name)
        if layer is None: return None
        return layer.get_aov(aov_name)
    
    def get_complete_layer_aov(
        self,
        layer_name: str,
        version_name: str,
        aov_name: str
        ) -> Optional[AOV]:
        layer = self.get_complete_layer(layer_name, version_name)
        if layer is None: return None
        return layer.get_aov(aov_name)
    
    def get_latest_layer_aov(
        self,
        layer_name: str,
        aov_name: str
        ) -> Optional[AOV]:
        layer = self.get_latest_layer(layer_name)
        if layer is None: return None
        return layer.get_aov(aov_name)
    
    def get_latest_complete_layer_aov(
        self,
        layer_name: str,
        aov_name: str
        ) -> Optional[AOV]:
        layer = self.get_latest_complete_layer(layer_name)
        if layer is None: return None
        return layer.get_aov(aov_name)
    
    def get_newer_latest_complete_layer_aov(
        self,
        layer_name: str,
        current_version_name: str,
        aov_name: str
        ) -> Optional[AOV]:
        layer = self.get_newer_latest_complete_layer(
            layer_name,
            current_version_name
        )
        if layer is None: return None
        return layer.get_aov(aov_name)

    def is_complete(
        self,
        version_name: str
        ) -> bool:
        for layer_versions in self.layers.values():
            if version_name not in layer_versions: return False
            layer = layer_versions[version_name]
            if not layer.is_complete(): return False
        return True
    
    def list_latest_complete_aovs(self) -> dict[str, AOV]:
        aovs = dict()
        for layer_name in self.layers.keys():
            layer = self.get_latest_complete_layer(layer_name)
            if layer is None: continue
            aovs[layer_name] = layer.aovs.copy()
        return aovs

@dataclass(frozen=True)
class AOVContext:
    aovs: dict[str, AOV]

    def get_aov(self, render_department_name: str) -> Optional[AOV]:
        return self.aovs.get(render_department_name)

@dataclass(frozen=True)
class LayerContext:
    layers: dict[str, Layer]

    def get_layer(self, render_department_name: str) -> Optional[Layer]:
        return self.layers.get(render_department_name)
    
    def get_aov(self, aov_name: str) -> AOVContext:
        aovs = dict()
        for render_department_name, layer in self.layers.items():
            aov = layer.get_aov(aov_name)
            if aov is None: continue
            aovs[render_department_name] = aov
        return AOVContext(aovs = aovs)
    
    def get_complete_aov(self, aov_name: str) -> AOVContext:
        aovs = dict()
        for render_department_name, layer in self.layers.items():
            aov = layer.get_complete_aov(aov_name)
            if aov is None: continue
            aovs[render_department_name] = aov
        return AOVContext(aovs = aovs)

@dataclass(frozen=True)
class RenderContext:
    renders: dict[str, Render]

    def get_render(self, render_department_name: str) -> Optional[Render]:
        return self.renders.get(render_department_name)

    def get_layer(
        self,
        render_layer_name: str,
        version_name: str
        ) -> LayerContext:
        layers = dict()
        for render_department_name, render in self.renders.items():
            layer = render.get_layer(render_layer_name, version_name)
            if layer is None: continue
            layers[render_department_name] = layer
        return LayerContext(layers = layers)
    
    def get_complete_layer(
        self,
        render_layer_name: str,
        version_name: str
        ) -> LayerContext:
        layers = dict()
        for render_department_name, render in self.renders.items():
            layer = render.get_complete_layer(render_layer_name, version_name)
            if layer is None: continue
            layers[render_department_name] = layer
        return LayerContext(layers = layers)
    
    def get_latest_layer(self, render_layer_name: str) -> LayerContext:
        layers = dict()
        for render_department_name, render in self.renders.items():
            layer = render.get_latest_layer(render_layer_name)
            if layer is None: continue
            layers[render_department_name] = layer
        return LayerContext(layers = layers)
    
    def get_latest_complete_layer(self, render_layer_name: str) -> LayerContext:
        layers = dict()
        for render_department_name, render in self.renders.items():
            layer = render.get_latest_complete_layer(render_layer_name)
            if layer is None: continue
            layers[render_department_name] = layer
        return LayerContext(layers = layers)
    
    def get_layer_aov(
        self,
        render_layer_name: str,
        version_name: str,
        aov_name: str
        ) -> AOVContext:
        aovs = dict()
        for render_department_name, render in self.renders.items():
            aov = render.get_layer_aov(render_layer_name, version_name, aov_name)
            if aov is None: continue
            aovs[render_department_name] = aov
        return AOVContext(aovs = aovs)
    
    def get_complete_layer_aov(
        self,
        render_layer_name: str,
        version_name: str,
        aov_name: str
        ) -> AOVContext:
        aovs = dict()
        for render_department_name, render in self.renders.items():
            aov = render.get_complete_layer_aov(render_layer_name, version_name, aov_name)
            if aov is None: continue
            aovs[render_department_name] = aov
        return AOVContext(aovs = aovs)
    
    def get_latest_layer_aov(
        self,
        render_layer_name: str,
        aov_name: str
        ) -> AOVContext:
        aovs = dict()
        for render_department_name, render in self.renders.items():
            aov = render.get_latest_layer_aov(render_layer_name, aov_name)
            if aov is None: continue
            aovs[render_department_name] = aov
        return AOVContext(aovs = aovs)

    def get_latest_complete_layer_aov(
        self,
        render_layer_name: str,
        aov_name: str
        ) -> AOVContext:
        aovs = dict()
        for render_department_name, render in self.renders.items():
            aov = render.get_latest_complete_layer_aov(render_layer_name, aov_name)
            if aov is None: continue
            aovs[render_department_name] = aov
        return AOVContext(aovs = aovs)
    
    def list_latest_complete_aovs(self) -> dict[str, dict[str, AOV]]:
        aovs = dict()
        for render_department_name, render in self.renders.items():
            aovs[render_department_name] = render.list_latest_complete_aovs()
        return aovs

    def resolve_latest_aovs(
        self,
        shot_department_priority: list[str],
        render_department_priority: list[str],
        min_shot_department: Optional[str] = None,
        min_render_department: Optional[str] = None,
        aov_filter: Optional[callable] = None
        ) -> dict[str, dict[str, tuple[str, str, AOV, str]]]:
        """Resolve the latest version of each layer/AOV across shot and render departments.

        Returns a dict structure: result[layer_name][aov_name] = (render_dept, version, aov, shot_dept)
        where the AOV is selected using hierarchical comparison: shot dept > render dept > version.

        Args:
            shot_department_priority: Shot department names in priority order (e.g., light < render < composite)
            render_department_priority: Render department names in priority order (e.g., render < denoise < composite)
            min_shot_department: Minimum shot department to consider (inclusive)
            min_render_department: Minimum render department to consider (inclusive)
            aov_filter: Optional function to filter which AOVs to include. Takes aov_name, returns bool.

        Returns:
            Nested dict mapping layer_name -> aov_name -> (render_dept, version, aov, shot_dept)
        """
        latest_aovs = {}

        # Helper to get department priority index, returns -1 if not in list
        def get_dept_priority(dept, priority_list):
            try:
                return priority_list.index(dept)
            except ValueError:
                return -1

        # Determine minimum thresholds
        min_shot_idx = get_dept_priority(min_shot_department, shot_department_priority) if min_shot_department else -1
        min_render_idx = get_dept_priority(min_render_department, render_department_priority) if min_render_department else -1

        # Scan all available render departments
        for render_department_name in self.renders.keys():
            # Check if this department meets minimum threshold
            shot_idx = get_dept_priority(render_department_name, shot_department_priority)
            render_idx = get_dept_priority(render_department_name, render_department_priority)

            # Department must be in at least one priority list and meet minimum threshold
            is_valid_shot = shot_idx >= min_shot_idx
            is_valid_render = render_idx >= min_render_idx

            if not (is_valid_shot or is_valid_render):
                continue

            render = self.renders[render_department_name]

            for layer_name, layer_versions in render.layers.items():
                # Get all versions and find the latest complete one
                version_names = list(layer_versions.keys())
                if len(version_names) == 0:
                    continue
                version_names.sort(key=api.naming.get_version_code)

                # Try versions from newest to oldest
                for version_name in reversed(version_names):
                    layer = layer_versions[version_name]
                    frame_range = layer.get_frame_range()
                    if frame_range is None:
                        continue

                    for aov_name, aov in layer.aovs.items():
                        # Apply filter if provided
                        if aov_filter is not None and not aov_filter(aov_name):
                            continue

                        # Verify AOV is complete
                        if not aov.is_complete(frame_range):
                            continue

                        # Initialize layer dict if needed
                        if layer_name not in latest_aovs:
                            latest_aovs[layer_name] = {}

                        # Get shot department from layer context
                        context_path = layer.path / 'context.json'
                        context = load_json(context_path)
                        curr_shot_dept = context.get('entity', {}).get('department_name') if context else None
                        curr_shot_idx = get_dept_priority(curr_shot_dept, shot_department_priority) if curr_shot_dept else -1
                        curr_render_idx = get_dept_priority(render_department_name, render_department_priority)

                        # Update if this is a better version
                        if aov_name not in latest_aovs[layer_name]:
                            latest_aovs[layer_name][aov_name] = (
                                render_department_name, version_name, aov, curr_shot_dept
                            )
                        else:
                            prev_render_dept, prev_version, _, prev_shot_dept = latest_aovs[layer_name][aov_name]
                            prev_shot_idx = get_dept_priority(prev_shot_dept, shot_department_priority) if prev_shot_dept else -1
                            prev_render_idx = get_dept_priority(prev_render_dept, render_department_priority)

                            # Hierarchical comparison: shot dept > render dept > version
                            should_update = False
                            if curr_shot_idx > prev_shot_idx:
                                should_update = True
                            elif curr_shot_idx == prev_shot_idx:
                                if curr_render_idx > prev_render_idx:
                                    should_update = True
                                elif curr_render_idx == prev_render_idx:
                                    prev_version_code = api.naming.get_version_code(prev_version)
                                    curr_version_code = api.naming.get_version_code(version_name)
                                    if curr_version_code > prev_version_code:
                                        should_update = True

                            if should_update:
                                latest_aovs[layer_name][aov_name] = (
                                    render_department_name, version_name, aov, curr_shot_dept
                                )

                    # Only take the latest version from this layer
                    break

        return latest_aovs

def get_frame_path(
    entity: Entity,
    render_department_name: str,
    render_layer_name: str,
    version_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Path:
    match entity:
        case ShotEntity(sequence_name, shot_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'shots/'
                f'{sequence_name}/'
                f'{shot_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}/'
                f'{version_name}'
            )
            frame_name = (
                f'{sequence_name}_'
                f'{shot_name}_'
                f'{render_layer_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return render_path / frame_name
        case KitEntity(category_name, kit_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'kits/'
                f'{category_name}/'
                f'{kit_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}/'
                f'{version_name}'
            )
            frame_name = (
                f'{category_name}_'
                f'{kit_name}_'
                f'{render_layer_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return render_path / frame_name
        case AssetEntity(category_name, asset_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'assets/'
                f'{category_name}/'
                f'{asset_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}/'
                f'{version_name}'
            )
            frame_name = (
                f'{category_name}_'
                f'{asset_name}_'
                f'{render_layer_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return render_path / frame_name
        case _:
            assert False, f'Invalid entity: {entity}'

def get_next_frame_path(
    entity: Entity,
    render_department_name: str,
    render_layer_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Path:
    match entity:
        case ShotEntity(sequence_name, shot_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'shots/'
                f'{sequence_name}/'
                f'{shot_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}'
            )
            version_path = get_next_version_path(render_path)
            version_name = version_path.name
            frame_name = (
                f'{sequence_name}_'
                f'{shot_name}_'
                f'{render_layer_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return version_path / frame_name
        case KitEntity(category_name, kit_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'kits/'
                f'{category_name}/'
                f'{kit_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}'
            )
            version_path = get_next_version_path(render_path)
            version_name = version_path.name
            frame_name = (
                f'{category_name}_'
                f'{kit_name}_'
                f'{render_layer_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return version_path / frame_name
        case AssetEntity(category_name, asset_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'assets/'
                f'{category_name}/'
                f'{asset_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}'
            )
            version_path = get_next_version_path(render_path)
            version_name = version_path.name
            frame_name = (
                f'{category_name}_'
                f'{asset_name}_'
                f'{render_layer_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return version_path / frame_name
        case _:
            assert False, f'Invalid entity: {entity}'

def get_latest_frame_path(
    entity: Entity,
    render_department_name: str,
    render_layer_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Optional[Path]:
    match entity:
        case ShotEntity(sequence_name, shot_name, department_name):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'shots/'
                f'{sequence_name}/'
                f'{shot_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}'
            )
            version_path = get_latest_version_path(render_path)
            if version_path is None: return None
            version_name = version_path.name
            frame_name = (
                f'{sequence_name}_'
                f'{shot_name}_'
                f'{render_layer_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return version_path / frame_name
        case KitEntity(category_name, kit_name, department_name):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'kits/'
                f'{category_name}/'
                f'{kit_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}'
            )
            version_path = get_latest_version_path(render_path)
            if version_path is None: return None
            version_name = version_path.name
            frame_name = (
                f'{category_name}_'
                f'{kit_name}_'
                f'{render_layer_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return version_path / frame_name
        case AssetEntity(category_name, asset_name, department_name):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'assets/'
                f'{category_name}/'
                f'{asset_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}'
            )
            version_path = get_latest_version_path(render_path)
            if version_path is None: return None
            version_name = version_path.name
            frame_name = (
                f'{category_name}_'
                f'{asset_name}_'
                f'{render_layer_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return version_path / frame_name
        case _:
            assert False, f'Invalid entity: {entity}'

def get_aov_frame_uri(
    entity: Entity,
    render_department_name: str,
    render_layer_name: str,
    version_name: str,
    aov_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'   
    ) -> str:
    match entity:
        case ShotEntity(sequence_name, shot_name, _):
            render_path = (
                f'{purpose}:/'
                'render/'
                'shots/'
                f'{sequence_name}/'
                f'{shot_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}/'
                f'{version_name}/'
                f'{aov_name}'
            )
            frame_name = (
                f'{sequence_name}_'
                f'{shot_name}_'
                f'{render_layer_name}_'
                f'{aov_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return f'{render_path}/{frame_name}'
        case KitEntity(category_name, kit_name, _):
            render_path = (
                f'{purpose}:/'
                'render/'
                'kits/'
                f'{category_name}/'
                f'{kit_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}/'
                f'{version_name}/'
                f'{aov_name}'
            )
            frame_name = (
                f'{category_name}_'
                f'{kit_name}_'
                f'{render_layer_name}_'
                f'{aov_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return f'{render_path}/{frame_name}'
        case AssetEntity(category_name, asset_name, _):
            render_path = (
                f'{purpose}:/'
                'render/'
                'assets/'
                f'{category_name}/'
                f'{asset_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}/'
                f'{version_name}/'
                f'{aov_name}'
            )
            frame_name = (
                f'{category_name}_'
                f'{asset_name}_'
                f'{render_layer_name}_'
                f'{aov_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return f'{render_path}/{frame_name}'
        case _:
            assert False, f'Invalid entity: {entity}'

def get_aov_frame_path(
    entity: Entity,
    render_department_name: str,
    render_layer_name: str,
    version_name: str,
    aov_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Path:
    match entity:
        case ShotEntity(sequence_name, shot_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'shots/'
                f'{sequence_name}/'
                f'{shot_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}/'
                f'{version_name}/'
                f'{aov_name}'
            )
            frame_name = (
                f'{sequence_name}_'
                f'{shot_name}_'
                f'{render_layer_name}_'
                f'{aov_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return render_path / frame_name
        case KitEntity(category_name, kit_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'kits/'
                f'{category_name}/'
                f'{kit_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}/'
                f'{version_name}/'
                f'{aov_name}'
            )
            frame_name = (
                f'{category_name}_'
                f'{kit_name}_'
                f'{render_layer_name}_'
                f'{aov_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return render_path / frame_name
        case AssetEntity(category_name, asset_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'assets/'
                f'{category_name}/'
                f'{asset_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}/'
                f'{version_name}/'
                f'{aov_name}'
            )
            frame_name = (
                f'{category_name}_'
                f'{asset_name}_'
                f'{render_layer_name}_'
                f'{aov_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return render_path / frame_name
        case _:
            assert False, f'Invalid entity: {entity}'

def get_next_aov_frame_path(
    entity: Entity,
    render_department_name: str,
    render_layer_name: str,
    aov_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Path:
    match entity:
        case ShotEntity(sequence_name, shot_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'shots/'
                f'{sequence_name}/'
                f'{shot_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}'
            )
            version_path = get_next_version_path(render_path)
            version_name = version_path.name
            frame_name = (
                f'{sequence_name}_'
                f'{shot_name}_'
                f'{render_layer_name}_'
                f'{aov_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return version_path / aov_name / frame_name
        case KitEntity(category_name, kit_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'kits/'
                f'{category_name}/'
                f'{kit_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}'
            )
            version_path = get_next_version_path(render_path)
            version_name = version_path.name
            frame_name = (
                f'{category_name}_'
                f'{kit_name}_'
                f'{render_layer_name}_'
                f'{aov_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return version_path / aov_name / frame_name
        case AssetEntity(category_name, asset_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'assets/'
                f'{category_name}/'
                f'{asset_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}'
            )
            version_path = get_next_version_path(render_path)
            version_name = version_path.name
            frame_name = (
                f'{category_name}_'
                f'{asset_name}_'
                f'{render_layer_name}_'
                f'{aov_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return version_path / aov_name / frame_name
        case _:
            assert False, f'Invalid entity: {entity}'

def get_latest_aov_frame_path(
    entity: Entity,
    render_department_name: str,
    render_layer_name: str,
    aov_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Optional[Path]:
    match entity:
        case ShotEntity(sequence_name, shot_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'shots/'
                f'{sequence_name}/'
                f'{shot_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}'
            )
            version_path = get_latest_version_path(render_path)
            if version_path is None: return None
            version_name = version_path.name
            frame_name = (
                f'{sequence_name}_'
                f'{shot_name}_'
                f'{render_layer_name}_'
                f'{aov_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return version_path / aov_name / frame_name
        case KitEntity(category_name, kit_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'kits/'
                f'{category_name}/'
                f'{kit_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}'
            )
            version_path = get_latest_version_path(render_path)
            if version_path is None: return None
            version_name = version_path.name
            frame_name = (
                f'{category_name}_'
                f'{kit_name}_'
                f'{render_layer_name}_'
                f'{aov_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return version_path / aov_name / frame_name
        case AssetEntity(category_name, asset_name, _):
            render_path = api.storage.resolve(
                f'{purpose}:/'
                'render/'
                'assets/'
                f'{category_name}/'
                f'{asset_name}/'
                f'{render_department_name}/'
                f'{render_layer_name}'
            )
            version_path = get_latest_version_path(render_path)
            if version_path is None: return None
            version_name = version_path.name
            frame_name = (
                f'{category_name}_'
                f'{asset_name}_'
                f'{render_layer_name}_'
                f'{aov_name}_'
                f'{version_name}.'
                f'{frame_pattern}.'
                f'{suffix}'
            )
            return version_path / aov_name / frame_name
        case _:
            assert False, f'Invalid entity: {entity}'

def get_layer_playblast_path(
    entity: Entity,
    render_layer_name: str,
    version_name: str,
    purpose: str = 'render'
    ) -> Path:
    match entity:
        case ShotEntity(sequence_name, shot_name, department_name):
            assert department_name is not None
            return api.storage.resolve(
                f'{purpose}:/'
                'playblast/'
                'shots/'
                f'{sequence_name}/'
                f'{shot_name}/'
                f'{department_name}/'
                f'{render_layer_name}/'
                f'{version_name}.mp4'
            )
        case KitEntity(category_name, kit_name, department_name):
            assert department_name is not None
            return api.storage.resolve(
                f'{purpose}:/'
                'playblast/'
                'kits/'
                f'{category_name}/'
                f'{kit_name}/'
                f'{department_name}/'
                f'{render_layer_name}/'
                f'{version_name}.mp4'
            )
        case AssetEntity(category_name, asset_name, department_name):
            assert department_name is not None
            return api.storage.resolve(
                f'{purpose}:/'
                'playblast/'
                'assets/'
                f'{category_name}/'
                f'{asset_name}/'
                f'{department_name}/'
                f'{render_layer_name}/'
                f'{version_name}.mp4'
            )
        case _:
            assert False, f'Invalid entity: {entity}'

def get_playblast_path(
    entity: Entity,
    version_name: str,
    purpose: str = 'render'
    ) -> Path:
    match entity:
        case ShotEntity(sequence_name, shot_name, department_name):
            assert department_name is not None
            return api.storage.resolve(
                f'{purpose}:/'
                'playblast/'
                'shots/'
                f'{sequence_name}/'
                f'{shot_name}/'
                f'{department_name}/'
                f'{version_name}.mp4'
            )
        case KitEntity(category_name, kit_name, department_name):
            assert department_name is not None
            return api.storage.resolve(
                f'{purpose}:/'
                'playblast/'
                'kits/'
                f'{category_name}/'
                f'{kit_name}/'
                f'{department_name}/'
                f'{version_name}.mp4'
            )
        case AssetEntity(category_name, asset_name, department_name):
            assert department_name is not None
            return api.storage.resolve(
                f'{purpose}:/'
                'playblast/'
                'assets/'
                f'{category_name}/'
                f'{asset_name}/'
                f'{department_name}/'
                f'{version_name}.mp4'
            )
        case _:
            assert False, f'Invalid entity: {entity}'

def get_next_layer_playblast_path(
    entity: Entity,
    render_layer_name: str,
    purpose: str = 'render'
    ) -> Path:

    def _layer_playblast_path(
        entity: Entity,
        render_layer_name: str,
        purpose: str
        ) -> Path:
        match entity:
            case ShotEntity(sequence_name, shot_name, department_name):
                assert department_name is not None
                return api.storage.resolve(
                    f'{purpose}:/'
                    'playblast/'
                    'shots/'
                    f'{sequence_name}/'
                    f'{shot_name}/'
                    f'{department_name}/'
                    f'{render_layer_name}'
                )
            case KitEntity(category_name, kit_name, department_name):
                assert department_name is not None
                return api.storage.resolve(
                    f'{purpose}:/'
                    'playblast/'
                    'kits/'
                    f'{category_name}/'
                    f'{kit_name}/'
                    f'{department_name}/'
                    f'{render_layer_name}'
                )
            case AssetEntity(category_name, asset_name, department_name):
                assert department_name is not None
                return api.storage.resolve(
                    f'{purpose}:/'
                    'playblast/'
                    'assets/'
                    f'{category_name}/'
                    f'{asset_name}/'
                    f'{department_name}/'
                    f'{render_layer_name}'
                )
            case _:
                assert False, f'Invalid entity: {entity}'

    playblast_path = _layer_playblast_path(entity, render_layer_name, purpose)
    version_names = list(filter(
        api.naming.is_valid_version_name,
        map(
            lambda path: path.stem,
            playblast_path.glob('*.mp4')
        )
    ))
    version_names.sort(key = api.naming.get_version_code)
    if len(version_names) == 0: return playblast_path / 'v0001.mp4'
    version_name = version_names[-1]
    next_version_name = get_next_version_name(version_name)
    return playblast_path / f'{next_version_name}.mp4'

def get_next_playblast_path(
    entity: Entity,
    purpose: str = 'render'
    ) -> Path:

    def _playblast_path(
        entity: Entity,
        purpose: str
        ) -> Path:
        match entity:
            case ShotEntity(sequence_name, shot_name, department_name):
                assert department_name is not None
                return api.storage.resolve(
                    f'{purpose}:/'
                    'playblast/'
                    'shots/'
                    f'{sequence_name}/'
                    f'{shot_name}/'
                    f'{department_name}'
                )
            case KitEntity(category_name, kit_name, department_name):
                assert department_name is not None
                return api.storage.resolve(
                    f'{purpose}:/'
                    'playblast/'
                    'kits/'
                    f'{category_name}/'
                    f'{kit_name}/'
                    f'{department_name}'
                )
            case AssetEntity(category_name, asset_name, department_name):
                assert department_name is not None
                return api.storage.resolve(
                    f'{purpose}:/'
                    'playblast/'
                    'assets/'
                    f'{category_name}/'
                    f'{asset_name}/'
                    f'{department_name}'
                )
            case _:
                assert False, f'Invalid entity: {entity}'

    playblast_path = _playblast_path(entity, purpose)
    version_names = list(filter(
        api.naming.is_valid_version_name,
        map(
            lambda path: path.stem,
            playblast_path.glob('*.mp4')
        )
    ))
    version_names.sort(key = api.naming.get_version_code)
    if len(version_names) == 0: return playblast_path / 'v0001.mp4'
    version_name = version_names[-1]
    next_version_name = get_next_version_name(version_name)
    return playblast_path / f'{next_version_name}.mp4'

def get_latest_layer_playblast_path(
    entity: Entity,
    render_layer_name: str,
    purpose: str = 'render'
    ) -> Optional[Path]:

    def _layer_playblast_path(
        entity: Entity,
        render_layer_name: str,
        purpose: str
        ) -> Path:
        match entity:
            case ShotEntity(sequence_name, shot_name, department_name):
                assert department_name is not None
                return api.storage.resolve(
                    f'{purpose}:/'
                    'playblast/'
                    'shots/'
                    f'{sequence_name}/'
                    f'{shot_name}/'
                    f'{department_name}/'
                    f'{render_layer_name}'
                )
            case KitEntity(category_name, kit_name, department_name):
                assert department_name is not None
                return api.storage.resolve(
                    f'{purpose}:/'
                    'playblast/'
                    'kits/'
                    f'{category_name}/'
                    f'{kit_name}/'
                    f'{department_name}/'
                    f'{render_layer_name}'
                )
            case AssetEntity(category_name, asset_name, department_name):
                assert department_name is not None
                return api.storage.resolve(
                    f'{purpose}:/'
                    'playblast/'
                    'assets/'
                    f'{category_name}/'
                    f'{asset_name}/'
                    f'{department_name}/'
                    f'{render_layer_name}'
                )
            case _:
                assert False, f'Invalid entity: {entity}'

    playblast_path = _layer_playblast_path(entity, render_layer_name, purpose)
    version_names = list(filter(
        api.naming.is_valid_version_name,
        map(
            lambda path: path.stem,
            playblast_path.glob('*.mp4')
        )
    ))
    version_names.sort(key = api.naming.get_version_code)
    if len(version_names) == 0: return None
    latest_version_name = version_names[-1]
    return playblast_path / f'{latest_version_name}.mp4'

def get_latest_playblast_path(
    entity: Entity,
    purpose: str = 'render'
    ) -> Optional[Path]:

    def _playblast_path(
        entity: Entity,
        purpose: str
        ) -> Path:
        match entity:
            case ShotEntity(sequence_name, shot_name, department_name):
                assert department_name is not None
                return api.storage.resolve(
                    f'{purpose}:/'
                    'playblast/'
                    'shots/'
                    f'{sequence_name}/'
                    f'{shot_name}/'
                    f'{department_name}'
                )
            case KitEntity(category_name, kit_name, department_name):
                assert department_name is not None
                return api.storage.resolve(
                    f'{purpose}:/'
                    'playblast/'
                    'kits/'
                    f'{category_name}/'
                    f'{kit_name}/'
                    f'{department_name}'
                )
            case AssetEntity(category_name, asset_name, department_name):
                assert department_name is not None
                return api.storage.resolve(
                    f'{purpose}:/'
                    'playblast/'
                    'assets/'
                    f'{category_name}/'
                    f'{asset_name}/'
                    f'{department_name}'
                )
            case _:
                assert False, f'Invalid entity: {entity}'

    playblast_path = _playblast_path(entity, purpose)
    version_names = list(filter(
        api.naming.is_valid_version_name,
        map(
            lambda path: path.stem,
            playblast_path.glob('*.mp4')
        )
    ))
    version_names.sort(key = api.naming.get_version_code)
    if len(version_names) == 0: return None
    latest_version_name = version_names[-1]
    return playblast_path / f'{latest_version_name}.mp4'

def get_layer_daily_path(
    entity: Entity,
    render_layer_name: str,
    purpose: str = 'render'
    ) -> Path:
    match entity:
        case ShotEntity(sequence_name, shot_name, department_name):
            assert department_name is not None
            return api.storage.resolve(
                f'{purpose}:/'
                'daily/'
                'shots/'
                f'{department_name}/'
                f'{render_layer_name}/'
                f'{sequence_name}_{shot_name}_{render_layer_name}.mp4'
            )
        case KitEntity(category_name, kit_name, department_name):
            assert department_name is not None
            return api.storage.resolve(
                f'{purpose}:/'
                'daily/'
                'kits/'
                f'{department_name}/'
                f'{render_layer_name}/'
                f'{category_name}_{kit_name}_{render_layer_name}.mp4'
            )
        case AssetEntity(category_name, asset_name, department_name):
            assert department_name is not None
            return api.storage.resolve(
                f'{purpose}:/'
                'daily/'
                'assets/'
                f'{department_name}/'
                f'{render_layer_name}/'
                f'{category_name}_{asset_name}_{render_layer_name}.mp4'
            )
        case _:
            assert False, f'Invalid entity: {entity}'

def get_daily_path(
    entity: Entity,
    purpose: str = 'render'
    ) -> Path:
    match entity:
        case ShotEntity(sequence_name, shot_name, department_name):
            assert department_name is not None
            return api.storage.resolve(
                f'{purpose}:/'
                'daily/'
                'shots/'
                f'{department_name}/'
                f'{sequence_name}_{shot_name}.mp4'
            )
        case KitEntity(category_name, kit_name, department_name):
            assert department_name is not None
            return api.storage.resolve(
                f'{purpose}:/'
                'daily/'
                'kits/'
                f'{department_name}/'
                f'{category_name}_{kit_name}.mp4'
            )
        case AssetEntity(category_name, asset_name, department_name):
            assert department_name is not None
            return api.storage.resolve(
                f'{purpose}:/'
                'daily/'
                'assets/'
                f'{department_name}/'
                f'{category_name}_{asset_name}.mp4'
            )
        case _:
            assert False, f'Invalid entity: {entity}'

def get_render(
    sequence_name: str,
    shot_name: str,
    render_department_name: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Optional[Render]:

    # Get the render path
    render_department_path = api.storage.resolve(
        f'{purpose}:/'
        'render/'
        'shots/'
        f'{sequence_name}/'
        f'{shot_name}/'
        f'{render_department_name}'
    )
    if not render_department_path.exists(): return None
    
    # Collect render layers
    render_layers = dict()
    render_layer_names = api.config.list_render_layer_names(
        sequence_name,
        shot_name
    )
    render_layer_names.append('slapcomp')
    for render_layer_path in render_department_path.iterdir():
        render_layer_name = render_layer_path.name
        if render_layer_name not in render_layer_names: continue
        layer_versions = dict()
        for version_path in list_version_paths(render_layer_path):
            version_name = version_path.name

            # Collect AOVs
            aovs = dict()
            for aov_path in version_path.iterdir():
                if aov_path.suffix != '': continue
                aov_name = aov_path.name
                aov_frame_path = get_aov_frame_path(
                    ShotEntity(
                        sequence_name,
                        shot_name
                    ),
                    render_department_name,
                    render_layer_name,
                    version_name,
                    aov_name,
                    '*',
                    suffix
                )
                aovs[aov_name] = AOV(
                    path = aov_path,
                    label = aov_name,
                    name = aov_frame_path.name.split('.')[0],
                    suffix = suffix
                )

            # Collect frames
            layer_frame_path = get_frame_path(
                ShotEntity(
                    sequence_name,
                    shot_name
                ),
                render_department_name,
                render_layer_name,
                version_name,
                '*',
                suffix if render_layer_name != 'slapcomp' else 'jpg',
            )

            # Collect layer
            layer_versions[version_name] = Layer(
                path = version_path,
                label = render_layer_name,
                version = version_name,
                aovs = aovs,
                name = layer_frame_path.name.split('.')[0],
                suffix = suffix
            )
        render_layers[render_layer_name] = layer_versions
    
    # Return the render
    return Render(
        path = render_department_path,
        layers = render_layers
    )

def get_render_context(
    sequence_name: str,
    shot_name: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> RenderContext:
    renders = dict()
    render_department_names = api.config.list_render_department_names()
    for department_name in render_department_names:
        render = get_render(
            sequence_name,
            shot_name,
            department_name,
            suffix,
            purpose
        )
        if render is None: continue
        renders[department_name] = render
    return RenderContext(renders = renders)

###############################################################################
# Workspace Paths
###############################################################################
def _valid_file_path_version_name(file_path: Path) -> bool:
    return api.naming.is_valid_version_name(file_path.stem.split('_')[-1])

def _get_file_path_version_code(file_path: Path) -> bool:
    return api.naming.get_version_code(file_path.stem.split('_')[-1])

def list_asset_hip_file_paths(
    category_name: str,
    asset_name: str,
    department_name: str
    ) -> list[Path]:
    workspace_path = api.storage.resolve(
        f'assets:/'
        f'{category_name}/'
        f'{asset_name}/'
        f'{department_name}'
    )
    hip_file_name_pattern = (
        f'{category_name}_'
        f'{asset_name}_'
        f'{department_name}_'
        '*.'
        'hip'
    )
    hip_file_paths = list(sorted(
        filter(
            _valid_file_path_version_name,
            workspace_path.glob(hip_file_name_pattern)
        ),
        key = _get_file_path_version_code
    ))
    return hip_file_paths

def list_shot_hip_file_paths(
    sequence_name: str,
    shot_name: str,
    department_name: str
    ) -> list[Path]:
    workspace_path = api.storage.resolve(
        f'shots:/'
        f'{sequence_name}/'
        f'{shot_name}/'
        f'{department_name}'
    )
    hip_file_name_pattern = (
        f'{sequence_name}_'
        f'{shot_name}_'
        f'{department_name}_'
        '*.'
        'hip'
    )
    hip_file_paths = list(sorted(
        filter(
            _valid_file_path_version_name,
            workspace_path.glob(hip_file_name_pattern)
        ),
        key = _get_file_path_version_code
    ))
    return hip_file_paths

def list_kit_hip_file_paths(
    category_name: str,
    kit_name: str,
    department_name: str
    ) -> list[Path]:
    workspace_path = api.storage.resolve(
        f'kits:/'
        f'{category_name}/'
        f'{kit_name}/'
        f'{department_name}'
    )
    hip_file_name_pattern = (
        f'{category_name}_'
        f'{kit_name}_'
        f'{department_name}_'
        '*.'
        'hip'
    )
    hip_file_paths = list(sorted(
        filter(
            _valid_file_path_version_name,
            workspace_path.glob(hip_file_name_pattern)
        ),
        key = _get_file_path_version_code
    ))
    return hip_file_paths

def get_asset_hip_file_path(
    category_name: str,
    asset_name: str,
    department_name: str,
    version_name: str
    ) -> Path:
    workspace_path = api.storage.resolve(
        f'assets:/'
        f'{category_name}/'
        f'{asset_name}/'
        f'{department_name}'
    )
    hip_file_name = (
        f'{category_name}_'
        f'{asset_name}_'
        f'{department_name}_'
        f'{version_name}.'
        'hip'
    )
    return workspace_path / hip_file_name

def get_shot_hip_file_path(
    sequence_name: str,
    shot_name: str,
    department_name: str,
    version_name: str
    ) -> Path:
    workspace_path = api.storage.resolve(
        f'shots:/'
        f'{sequence_name}/'
        f'{shot_name}/'
        f'{department_name}'
    )
    hip_file_name = (
        f'{sequence_name}_'
        f'{shot_name}_'
        f'{department_name}_'
        f'{version_name}.'
        'hip'
    )
    return workspace_path / hip_file_name

def get_kit_hip_file_path(
    category_name: str,
    kit_name: str,
    department_name: str,
    version_name: str
    ) -> Path:
    workspace_path = api.storage.resolve(
        f'kits:/'
        f'{category_name}/'
        f'{kit_name}/'
        f'{department_name}'
    )
    hip_file_name = (
        f'{category_name}_'
        f'{kit_name}_'
        f'{department_name}_'
        f'{version_name}.'
        'hip'
    )
    return workspace_path / hip_file_name

def latest_asset_hip_file_path(
    category_name: str,
    asset_name: str,
    department_name: str
    ) -> Optional[Path]:
    workspace_path = api.storage.resolve(
        f'assets:/'
        f'{category_name}/'
        f'{asset_name}/'
        f'{department_name}'
    )
    hip_file_name_pattern = (
        f'{category_name}_'
        f'{asset_name}_'
        f'{department_name}_'
        '*.'
        'hip'
    )
    hip_file_paths = list(sorted(
        filter(
            _valid_file_path_version_name,
            workspace_path.glob(hip_file_name_pattern)
        ),
        key = _get_file_path_version_code
    ))
    if len(hip_file_paths) == 0: return None
    return hip_file_paths[-1]

def latest_shot_hip_file_path(
    sequence_name: str,
    shot_name: str,
    department_name: str
    ) -> Optional[Path]:
    workspace_path = api.storage.resolve(
        f'shots:/'
        f'{sequence_name}/'
        f'{shot_name}/'
        f'{department_name}'
    )
    hip_file_name_pattern = (
        f'{sequence_name}_'
        f'{shot_name}_'
        f'{department_name}_'
        '*.'
        'hip'
    )
    hip_file_paths = list(sorted(
        filter(
            _valid_file_path_version_name,
            workspace_path.glob(hip_file_name_pattern)
        ),
        key = _get_file_path_version_code
    ))
    if len(hip_file_paths) == 0: return None
    return hip_file_paths[-1]

def latest_kit_hip_file_path(
    category_name: str,
    kit_name: str,
    department_name: str
    ) -> Optional[Path]:
    workspace_path = api.storage.resolve(
        f'kits:/'
        f'{category_name}/'
        f'{kit_name}/'
        f'{department_name}'
    )
    hip_file_name_pattern = (
        f'{category_name}_'
        f'{kit_name}_'
        f'{department_name}_'
        '*.'
        'hip'
    )
    hip_file_paths = list(sorted(
        filter(
            _valid_file_path_version_name,
            workspace_path.glob(hip_file_name_pattern)
        ),
        key = _get_file_path_version_code
    ))
    if len(hip_file_paths) == 0: return None
    return hip_file_paths[-1]

def next_asset_hip_file_path(
    category_name: str,
    asset_name: str,
    department_name: str
    ) -> Path:
    workspace_path = api.storage.resolve(
        f'assets:/'
        f'{category_name}/'
        f'{asset_name}/'
        f'{department_name}'
    )
    hip_file_name_pattern = (
        f'{category_name}_'
        f'{asset_name}_'
        f'{department_name}_'
        '*.'
        'hip'
    )
    version_codes = list(sorted(map(
        _get_file_path_version_code,
        filter(
            _valid_file_path_version_name,
            workspace_path.glob(hip_file_name_pattern))
        )
    ))
    latest_version_code = 0 if len(version_codes) == 0 else version_codes[-1]
    next_version_code = latest_version_code + 1
    next_version_name = api.naming.get_version_name(next_version_code)
    hip_file_name = hip_file_name_pattern.replace('*', next_version_name)
    return workspace_path / hip_file_name

def next_shot_hip_file_path(
    sequence_name: str,
    shot_name: str,
    department_name: str
    ) -> Path:
    workspace_path = api.storage.resolve(
        f'shots:/'
        f'{sequence_name}/'
        f'{shot_name}/'
        f'{department_name}'
    )
    hip_file_name_pattern = (
        f'{sequence_name}_'
        f'{shot_name}_'
        f'{department_name}_'
        '*.'
        'hip'
    )
    version_codes = list(sorted(map(
        _get_file_path_version_code,
        filter(
            _valid_file_path_version_name,
            workspace_path.glob(hip_file_name_pattern))
        )
    ))
    latest_version_code = 0 if len(version_codes) == 0 else version_codes[-1]
    next_version_code = latest_version_code + 1
    next_version_name = api.naming.get_version_name(next_version_code)
    hip_file_name = hip_file_name_pattern.replace('*', next_version_name)
    return workspace_path / hip_file_name

def next_kit_hip_file_path(
    category_name: str,
    kit_name: str,
    department_name: str
    ) -> Path:
    workspace_path = api.storage.resolve(
        f'kits:/'
        f'{category_name}/'
        f'{kit_name}/'
        f'{department_name}'
    )
    hip_file_name_pattern = (
        f'{category_name}_'
        f'{kit_name}_'
        f'{department_name}_'
        '*.'
        'hip'
    )
    version_codes = list(sorted(map(
        _get_file_path_version_code,
        filter(
            _valid_file_path_version_name,
            workspace_path.glob(hip_file_name_pattern))
        )
    ))
    latest_version_code = 0 if len(version_codes) == 0 else version_codes[-1]
    next_version_code = latest_version_code + 1
    next_version_name = api.naming.get_version_name(next_version_code)
    hip_file_name = hip_file_name_pattern.replace('*', next_version_name)
    return workspace_path / hip_file_name

def latest_hip_file_path(entity: Entity):
    match entity:
        case AssetEntity(category_name, asset_name, department_name):
            return latest_asset_hip_file_path(
                category_name = category_name,
                asset_name = asset_name,
                department_name = department_name
            )
        case ShotEntity(sequence_name, shot_name, department_name):
            return latest_shot_hip_file_path(
                sequence_name = sequence_name,
                shot_name = shot_name,
                department_name = department_name
            )
        case KitEntity(category_name, kit_name, department_name):
            return latest_kit_hip_file_path(
                category_name = category_name,
                kit_name = kit_name,
                department_name = department_name
            )
        case _:
            return None

def next_hip_file_path(entity: Entity):
    match entity:
        case AssetEntity(category_name, asset_name, department_name):
            return next_asset_hip_file_path(
                category_name = category_name,
                asset_name = asset_name,
                department_name = department_name
            )
        case ShotEntity(sequence_name, shot_name, department_name):
            return next_shot_hip_file_path(
                sequence_name = sequence_name,
                shot_name = shot_name,
                department_name = department_name
            )
        case KitEntity(category_name, kit_name, department_name):
            return next_kit_hip_file_path(
                category_name = category_name,
                kit_name = kit_name,
                department_name = department_name
            )
        case _:
            return None

@dataclass(frozen=True)
class Context: pass

@dataclass(frozen=True)
class ShotContext(Context):
    department_name: str
    sequence_name: str
    shot_name: str
    version_name: str

@dataclass(frozen=True)
class KitContext(Context):
    department_name: str
    category_name: str
    kit_name: str
    version_name: str

@dataclass(frozen=True)
class AssetContext(Context):
    department_name: str
    category_name: str
    asset_name: str
    version_name: str

def get_workfile_context(hip_file_path: Path) -> Optional[Context]:

    # Parse the file name
    hip_file_name = hip_file_path.stem
    if '_' not in hip_file_name: return None
    version_name = hip_file_name.rsplit('_', 1)[-1]
    if not api.naming.is_valid_version_name(version_name): return None

    # Parse the path
    workspace, *path = hip_file_path.parent.parts[-4:]
    match workspace:
        case 'assets':
            category_name, asset_name, department_name = path
            return AssetContext(
                department_name = department_name,
                category_name = category_name,
                asset_name = asset_name,
                version_name = version_name
            )
        case 'shots':
            sequence_name, shot_name, department_name = path
            return ShotContext(
                department_name = department_name,
                sequence_name = sequence_name,
                shot_name = shot_name,
                version_name = version_name
            )
        case 'kits':
            category_name, kit_name, department_name = path
            return KitContext(
                department_name = department_name,
                category_name = category_name,
                kit_name = kit_name,
                version_name = version_name
            )
        case _:
            return None

def entity_from_context(context: Context) -> Entity:
    match context:
        case ShotContext(
            department_name,
            sequence_name,
            shot_name,
            _version_name
            ):
            return ShotEntity(
                sequence_name,
                shot_name,
                department_name
            )
        case KitContext(
            department_name,
            category_name,
            kit_name,
            _version_name
            ):
            return KitEntity(
                category_name,
                kit_name,
                department_name
            )
        case AssetContext(
            department_name,
            category_name,
            asset_name,
            _version_name
            ):
            return AssetEntity(
                category_name,
                asset_name,
                department_name
            )
        case _:
            assert False, f'Invalid context: {context}'

###############################################################################
# Export Paths
###############################################################################
def get_asset_export_path(
    category_name: str,
    asset_name: str,
    department_name: str,
    version_name: str
    ) -> Path:
    return api.storage.resolve(
        'export:/'
        'assets/'
        f'{category_name}/'
        f'{asset_name}/'
        f'{department_name}/'
        f'{version_name}'
    )

def get_shot_export_path(
    sequence_name: str,
    shot_name: str,
    department_name: str,
    version_name: str
    ) -> Path:
    return api.storage.resolve(
        'export:/'
        'shots/'
        f'{sequence_name}/'
        f'{shot_name}/'
        f'{department_name}/'
        f'{version_name}'
    )

def get_kit_export_path(
    category_name: str,
    kit_name: str,
    department_name: str,
    version_name: str
    ) -> Path:
    return api.storage.resolve(
        'export:/'
        'kits/'
        f'{category_name}/'
        f'{kit_name}/'
        f'{department_name}/'
        f'{version_name}'
    )

def get_render_layer_export_path(
    sequence_name: str,
    shot_name: str,
    department_name: str,
    render_layer_name: str,
    version_name: str
    ) -> Path:
    return api.storage.resolve(
        'export:/'
        'shots/'
        f'{sequence_name}/'
        f'{shot_name}/'
        'render_layers/'
        f'{department_name}/'
        f'{render_layer_name}/'
        f'{version_name}'
    )

def get_asset_export_file_path(
    category_name: str,
    asset_name: str,
    department_name: str,
    version_name: str
    ) -> Path:
    version_path = get_asset_export_path(
        category_name,
        asset_name,
        department_name,
        version_name
    )
    usd_file_name = (
        f'{category_name}_'
        f'{asset_name}_'
        f'{department_name}_'
        f'{version_name}.'
        'usd'
    )
    return version_path / usd_file_name

def get_kit_export_file_path(
    category_name: str,
    kit_name: str,
    department_name: str,
    version_name: str
    ) -> Path:
    version_path = get_kit_export_path(
        category_name,
        kit_name,
        department_name,
        version_name
    )
    usd_file_name = (
        f'{category_name}_'
        f'{kit_name}_'
        f'{department_name}_'
        f'{version_name}.'
        'usd'
    )
    return version_path / usd_file_name

def latest_asset_export_path(
    category_name: str,
    asset_name: str,
    department_name: str
    ) -> Optional[Path]:
    export_path = api.storage.resolve(
        'export:/'
        'assets/'
        f'{category_name}/'
        f'{asset_name}/'
        f'{department_name}'
    )
    version_paths = list_version_paths(export_path)
    if len(version_paths) == 0: return None
    latest_version_path = version_paths[-1]
    return latest_version_path

def latest_shot_export_path(
    sequence_name: str,
    shot_name: str,
    department_name: str
    ) -> Optional[Path]:
    export_path = api.storage.resolve(
        'export:/'
        'shots/'
        f'{sequence_name}/'
        f'{shot_name}/'
        f'{department_name}'
    )
    version_paths = list_version_paths(export_path)
    if len(version_paths) == 0: return None
    latest_version_path = version_paths[-1]
    return latest_version_path

def latest_kit_export_path(
    category_name: str,
    kit_name: str,
    department_name: str
    ) -> Optional[Path]:
    export_path = api.storage.resolve(
        'export:/'
        'kits/'
        f'{category_name}/'
        f'{kit_name}/'
        f'{department_name}'
    )
    version_paths = list_version_paths(export_path)
    if len(version_paths) == 0: return None
    latest_version_path = version_paths[-1]
    return latest_version_path

def latest_render_layer_export_path(
    sequence_name: str,
    shot_name: str,
    department_name: str,
    render_layer_name: str
    ) -> Optional[Path]:
    export_path = api.storage.resolve(
        'export:/'
        'shots/'
        f'{sequence_name}/'
        f'{shot_name}'
        '/render_layers/'
        f'{department_name}/'
        f'{render_layer_name}'
    )
    version_paths = list_version_paths(export_path)
    if len(version_paths) == 0: return None
    latest_version_path = version_paths[-1]
    return latest_version_path

def latest_asset_export_file_path(
    category_name: str,
    asset_name: str,
    department_name: str
    ) -> Optional[Path]:
    usd_file_name_pattern = (
        f'{category_name}_'
        f'{asset_name}_'
        f'{department_name}_'
        '*.'
        'usd'
    )
    latest_version_path = latest_asset_export_path(
        category_name,
        asset_name,
        department_name
    )
    if latest_version_path is None: return None
    version_name = latest_version_path.name
    usd_file_name = usd_file_name_pattern.replace('*', version_name)
    return latest_version_path / usd_file_name

def latest_kit_export_file_path(
    category_name: str,
    kit_name: str,
    department_name: str
    ) -> Optional[Path]:
    usd_file_name_pattern = (
        f'{category_name}_'
        f'{kit_name}_'
        f'{department_name}_'
        '*.'
        'usd'
    )
    latest_version_path = latest_kit_export_path(
        category_name,
        kit_name,
        department_name
    )
    if latest_version_path is None: return None
    version_name = latest_version_path.name
    usd_file_name = usd_file_name_pattern.replace('*', version_name)
    return latest_version_path / usd_file_name

def next_asset_export_path(
    category_name: str,
    asset_name: str,
    department_name: str
    ) -> Path:
    export_path = api.storage.resolve(
        'export:/'
        'assets/'
        f'{category_name}/'
        f'{asset_name}/'
        f'{department_name}'
    )
    return get_next_version_path(export_path)

def next_shot_export_path(
    sequence_name: str,
    shot_name: str,
    department_name: str
    ) -> Path:
    export_path = api.storage.resolve(
        'export:/'
        'shots/'
        f'{sequence_name}/'
        f'{shot_name}/'
        f'{department_name}'
    )
    return get_next_version_path(export_path)

def next_kit_export_path(
    category_name: str,
    kit_name: str,
    department_name: str
    ) -> Path:
    export_path = api.storage.resolve(
        'export:/'
        'kits/'
        f'{category_name}/'
        f'{kit_name}/'
        f'{department_name}'
    )
    return get_next_version_path(export_path)

def next_asset_export_file_path(
    category_name: str,
    asset_name: str,
    department_name: str
    ) -> Path:
    usd_file_name_pattern = (
        f'{category_name}_'
        f'{asset_name}_'
        f'{department_name}_'
        '*.'
        'usd'
    )
    version_path = next_asset_export_path(
        category_name,
        asset_name,
        department_name
    )
    version_name = version_path.name
    usd_file_name = usd_file_name_pattern.replace('*', version_name)
    return version_path / usd_file_name

def next_kit_export_file_path(
    category_name: str,
    kit_name: str,
    department_name: str
    ) -> Path:
    usd_file_name_pattern = (
        f'{category_name}_'
        f'{kit_name}_'
        f'{department_name}_'
        '*.'
        'usd'
    )
    version_path = next_kit_export_path(
        category_name,
        kit_name,
        department_name
    )
    version_name = version_path.name
    usd_file_name = usd_file_name_pattern.replace('*', version_name)
    return version_path / usd_file_name