from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from tumblehead.api import default_client
from tumblehead.config.timeline import BlockRange
from tumblehead.config.groups import find_group
from tumblehead.util.io import load_json
from tumblehead.util.uri import Uri

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
                        curr_shot_dept = context.get('department') if context else None
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
    entity_uri: Uri,
    render_department_name: str,
    render_layer_name: str,
    version_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Path:
    render_uri = (
        Uri.parse_unsafe(f'{purpose}:/render') /
        entity_uri.segments /
        render_department_name /
        render_layer_name /
        version_name
    )
    render_path = api.storage.resolve(render_uri)
    frame_name = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            render_layer_name,
            version_name
        ]),
        frame_pattern,
        suffix
    ])
    return render_path / frame_name

def get_next_frame_path(
    entity_uri: Uri,
    render_department_name: str,
    render_layer_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Path:
    render_uri = (
        Uri.parse_unsafe(f'{purpose}:/render') /
        entity_uri.segments /
        render_department_name /
        render_layer_name
    )
    render_path = api.storage.resolve(render_uri)
    version_path = get_next_version_path(render_path)
    version_name = version_path.name
    frame_name = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            render_layer_name,
            version_name
        ]),
        frame_pattern,
        suffix
    ])
    return version_path / frame_name

def get_latest_frame_path(
    entity_uri: Uri,
    render_department_name: str,
    render_layer_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Optional[Path]:
    render_uri = (
        Uri.parse_unsafe(f'{purpose}:/render') /
        entity_uri.segments /
        render_department_name /
        render_layer_name
    )
    render_path = api.storage.resolve(render_uri)
    version_path = get_latest_version_path(render_path)
    if version_path is None: return None
    version_name = version_path.name
    frame_name = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            render_layer_name,
            version_name
        ]),
        frame_pattern,
        suffix
    ])
    return version_path / frame_name

def get_aov_frame_path(
    entity_uri: Uri,
    render_department_name: str,
    render_layer_name: str,
    version_name: str,
    aov_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'   
    ) -> Path:
    render_uri = (
        Uri.parse_unsafe(f'{purpose}:/render') /
        entity_uri.segments /
        render_department_name /
        render_layer_name /
        version_name /
        aov_name
    )
    render_path = api.storage.resolve(render_uri)
    frame_name = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            render_layer_name,
            aov_name,
            version_name
        ]),
        frame_pattern,
        suffix
    ])
    return render_path / frame_name

def get_next_aov_frame_path(
    entity_uri: Uri,
    render_department_name: str,
    render_layer_name: str,
    aov_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Path:
    render_uri = (
        Uri.parse_unsafe(f'{purpose}:/render') /
        entity_uri.segments /
        render_department_name /
        render_layer_name
    )
    render_path = api.storage.resolve(render_uri)
    version_path = get_next_version_path(render_path)
    version_name = version_path.name
    frame_name = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            render_layer_name,
            aov_name,
            version_name
        ]),
        frame_pattern,
        suffix
    ])
    return version_path / aov_name / frame_name

def get_latest_aov_frame_path(
    entity_uri: Uri,
    render_department_name: str,
    render_layer_name: str,
    aov_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Optional[Path]:
    render_uri = (
        Uri.parse_unsafe(f'{purpose}:/render') /
        entity_uri.segments /
        render_department_name /
        render_layer_name
    )
    render_path = api.storage.resolve(render_uri)
    version_path = get_latest_version_path(render_path)
    if version_path is None: return None
    version_name = version_path.name
    frame_name = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            render_layer_name,
            aov_name,
            version_name
        ]),
        frame_pattern,
        suffix
    ])
    return version_path / aov_name / frame_name

def get_layer_playblast_path(
    entity_uri: Uri,
    department_name: str,
    render_layer_name: str,
    version_name: str,
    purpose: str = 'render'
    ) -> Path:
    playblast_uri = (
        Uri.parse_unsafe(f'{purpose}:/playblast') /
        entity_uri.segments /
        department_name /
        render_layer_name
    )
    playblast_path = api.storage.resolve(playblast_uri)
    return playblast_path / f'{version_name}.mp4'

def get_playblast_path(
    entity_uri: Uri,
    department_name: str,
    version_name: str,
    purpose: str = 'render'
    ) -> Path:
    playblast_uri = (
        Uri.parse_unsafe(f'{purpose}:/playblast') /
        entity_uri.segments /
        department_name
    )
    playblast_path = api.storage.resolve(playblast_uri)
    return playblast_path / f'{version_name}.mp4'

def get_next_layer_playblast_path(
    entity_uri: Uri,
    department_name: str,
    render_layer_name: str,
    purpose: str = 'render'
    ) -> Path:
    playblast_uri = (
        Uri.parse_unsafe(f'{purpose}:/playblast') /
        entity_uri.segments /
        department_name /
        render_layer_name
    )
    playblast_path = api.storage.resolve(playblast_uri)
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
    entity_uri: Uri,
    department_name: str,
    purpose: str = 'render'
    ) -> Path:
    playblast_uri = (
        Uri.parse_unsafe(f'{purpose}:/playblast') /
        entity_uri.segments /
        department_name
    )
    playblast_path = api.storage.resolve(playblast_uri)
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
    entity_uri: Uri,
    department_name: str,
    render_layer_name: str,
    purpose: str = 'render'
    ) -> Optional[Path]:
    playblast_uri = (
        Uri.parse_unsafe(f'{purpose}:/playblast') /
        entity_uri.segments /
        department_name /
        render_layer_name
    )
    playblast_path = api.storage.resolve(playblast_uri)
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
    entity_uri: Uri,
    department_name: str,
    purpose: str = 'render'
    ) -> Optional[Path]:
    playblast_uri = (
        Uri.parse_unsafe(f'{purpose}:/playblast') /
        entity_uri.segments /
        department_name
    )
    playblast_path = api.storage.resolve(playblast_uri)
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
    entity_uri: Uri,
    department_name: str,
    render_layer_name: str,
    purpose: str = 'render'
    ) -> Path:
    daily_uri = (
        Uri.parse_unsafe(f'{purpose}:/daily') /
        entity_uri.segments[0] /
        department_name /
        render_layer_name
    )
    daily_path = api.storage.resolve(daily_uri)
    daily_name = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            render_layer_name
        ]),
        'mp4'
    ])
    return daily_path / daily_name

def get_daily_path(
    entity_uri: Uri,
    department_name: str,
    purpose: str = 'render'
    ) -> Path:
    daily_uri = (
        Uri.parse_unsafe(f'{purpose}:/daily') /
        entity_uri.segments[0] /
        department_name
    )
    daily_path = api.storage.resolve(daily_uri)
    daily_name = '.'.join([
        '_'.join(entity_uri.segments[1:]),
        'mp4'
    ])
    return daily_path / daily_name

def get_render(
    entity_uri: Uri,
    render_department_name: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Optional[Render]:

    # Get the render path
    render_uri = (
        Uri.parse_unsafe(f'{purpose}:/render') /
        entity_uri.segments /
        render_department_name
    )
    render_department_path = api.storage.resolve(render_uri)
    if not render_department_path.exists(): return None
    
    # Collect render layers
    render_layers = dict()
    properties = api.config.get_properties(entity_uri)
    if 'render_layers' not in properties:
        raise ValueError('Invalid shot entity, "render_layer" property missing')
    render_layer_names = properties['render_layers']
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
                    entity_uri,
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
                entity_uri,
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
    entity_uri: Uri,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> RenderContext:
    renders = dict()
    render_department_names = api.config.list_render_department_names()
    for department_name in render_department_names:
        render = get_render(
            entity_uri,
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
    return api.naming.is_valid_version_name(file_path.stem.rsplit('_', 1)[-1])

def _get_file_path_version_code(file_path: Path) -> bool:
    return api.naming.get_version_code(file_path.stem.rsplit('_', 1)[-1])

def list_hip_file_paths(
    entity_uri: Uri,
    department_name: str
    ) -> list[Path]:
    if entity_uri.purpose == 'groups':
        workfile_uri = entity_uri
        workspace_uri = (
            Uri.parse_unsafe('groups:/') /
            workfile_uri.segments /
            department_name
        )
    else:
        group = find_group(entity_uri.segments[0], entity_uri, department_name)
        workfile_uri = entity_uri if group is None else group.uri
        if group is not None:
            workspace_uri = (
                Uri.parse_unsafe('groups:/') /
                workfile_uri.segments /
                department_name
            )
        else:
            workspace_uri = (
                Uri.parse_unsafe('project:/') /
                workfile_uri.segments /
                department_name
            )
    workspace_path = api.storage.resolve(workspace_uri)
    hip_file_name_pattern = '.'.join([
        '_'.join(workfile_uri.segments[1:] + [
            department_name,
            '*'
        ]),
        'hip'
    ])
    hip_file_paths = list(sorted(
        filter(
            _valid_file_path_version_name,
            workspace_path.glob(hip_file_name_pattern)
        ),
        key = _get_file_path_version_code
    ))
    return hip_file_paths

def get_hip_file_path(
    entity_uri: Uri,
    department_name: str,
    version_name: str
    ) -> Path:
    if entity_uri.purpose == 'groups':
        workfile_uri = entity_uri
        workspace_uri = (
            Uri.parse_unsafe('groups:/') /
            workfile_uri.segments /
            department_name
        )
    else:
        group = find_group(entity_uri.segments[0], entity_uri, department_name)
        workfile_uri = entity_uri if group is None else group.uri
        if group is not None:
            workspace_uri = (
                Uri.parse_unsafe('groups:/') /
                workfile_uri.segments /
                department_name
            )
        else:
            workspace_uri = (
                Uri.parse_unsafe('project:/') /
                workfile_uri.segments /
                department_name
            )
    workspace_path = api.storage.resolve(workspace_uri)
    hip_file_name = '.'.join([
        '_'.join(workfile_uri.segments[1:] + [
            department_name,
            version_name
        ]),
        'hip'
    ])
    return workspace_path / hip_file_name

def latest_hip_file_path(
    entity_uri: Uri,
    department_name: str
    ) -> Optional[Path]:
    if entity_uri.purpose == 'groups':
        workfile_uri = entity_uri
        workspace_uri = (
            Uri.parse_unsafe('groups:/') /
            workfile_uri.segments /
            department_name
        )
    else:
        group = find_group(entity_uri.segments[0], entity_uri, department_name)
        workfile_uri = entity_uri if group is None else group.uri
        if group is not None:
            workspace_uri = (
                Uri.parse_unsafe('groups:/') /
                workfile_uri.segments /
                department_name
            )
        else:
            workspace_uri = (
                Uri.parse_unsafe('project:/') /
                workfile_uri.segments /
                department_name
            )
    workspace_path = api.storage.resolve(workspace_uri)
    hip_file_name_pattern = '.'.join([
        '_'.join(workfile_uri.segments[1:] + [
            department_name,
            '*'
        ]),
        'hip'
    ])
    hip_file_paths = list(sorted(
        filter(
            _valid_file_path_version_name,
            workspace_path.glob(hip_file_name_pattern)
        ),
        key = _get_file_path_version_code
    ))
    if len(hip_file_paths) == 0: return None
    return hip_file_paths[-1]

def next_hip_file_path(
    entity_uri: Uri,
    department_name: str
    ) -> Path:
    if entity_uri.purpose == 'groups':
        workfile_uri = entity_uri
        workspace_uri = (
            Uri.parse_unsafe('groups:/') /
            workfile_uri.segments /
            department_name
        )
    else:
        group = find_group(entity_uri.segments[0], entity_uri, department_name)
        workfile_uri = entity_uri if group is None else group.uri
        if group is not None:
            workspace_uri = (
                Uri.parse_unsafe('groups:/') /
                workfile_uri.segments /
                department_name
            )
        else:
            workspace_uri = (
                Uri.parse_unsafe('project:/') /
                workfile_uri.segments /
                department_name
            )
    workspace_path = api.storage.resolve(workspace_uri)
    hip_file_name_pattern = '.'.join([
        '_'.join(workfile_uri.segments[1:] + [
            department_name,
            '*'
        ]),
        'hip'
    ])
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

@dataclass(frozen=True)
class Context:
    entity_uri: Uri
    department_name: str
    version_name: str | None = None

def load_entity_context(context_json_path: Path) -> Optional[Context]:
    """
    Load entity context from a context.json file.

    Returns Context with entity_uri from the 'entity' field,
    department_name from 'department' field, and version_name
    from 'version' field (or empty string if not present).
    """
    context_data = load_json(context_json_path)
    if context_data is None:
        return None

    uri_str = context_data.get('uri')
    if not uri_str:
        return None

    try:
        entity_uri = Uri.parse_unsafe(uri_str)
        department_name = context_data.get('department', '')
        version_name = context_data.get('version', '')

        return Context(
            entity_uri=entity_uri,
            department_name=department_name,
            version_name=version_name
        )
    except:
        return None

def get_workfile_context(hip_file_path: Path) -> Optional[Context]:

    # Parse the file name
    hip_file_name = hip_file_path.stem
    if '_' not in hip_file_name: return None
    version_name = hip_file_name.rsplit('_', 1)[-1]
    if not api.naming.is_valid_version_name(version_name): return None

    # Read the workfile context
    context_path = hip_file_path.parent / 'context.json'
    context_data = load_json(context_path)
    if context_data is None: return None

    # Read entity URI from context
    entity_uri = Uri.parse_unsafe(context_data['uri'])

    department_name = context_data['department']

    # Return context
    return Context(
        entity_uri = entity_uri,
        department_name = department_name,
        version_name = version_name
    )

###############################################################################
# Export Paths
###############################################################################
def get_export_path(
    entity_uri: Uri,
    department_name: str,
    version_name: str
    ) -> Path:
    export_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        department_name /
        version_name
    )
    return api.storage.resolve(export_uri)

def get_render_layer_export_path(
    entity_uri: Uri,
    department_name: str,
    render_layer_name: str,
    version_name: str
    ) -> Path:
    export_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        'render_layers' /
        department_name /
        render_layer_name /
        version_name
    )
    return api.storage.resolve(export_uri)

def get_export_file_path(
    entity_uri: Uri,
    department_name: str,
    version_name: str
    ) -> Path:
    version_path = get_export_path(
        entity_uri,
        department_name,
        version_name
    )
    usd_file_name = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            department_name,
            version_name
        ]),
        'usd'
    ])
    return version_path / usd_file_name

def latest_export_path(
    entity_uri: Uri,
    department_name: str
    ) -> Optional[Path]:
    export_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        department_name
    )
    export_path = api.storage.resolve(export_uri)
    version_paths = list_version_paths(export_path)
    if len(version_paths) == 0: return None
    latest_version_path = version_paths[-1]
    return latest_version_path

def latest_render_layer_export_path(
    entity_uri: Uri,
    department_name: str,
    render_layer_name: str
    ) -> Optional[Path]:
    export_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        'render_layers' /
        department_name /
        render_layer_name
    )
    export_path = api.storage.resolve(export_uri)
    version_paths = list_version_paths(export_path)
    if len(version_paths) == 0: return None
    latest_version_path = version_paths[-1]
    return latest_version_path

def get_render_layer_export_file_path(
    entity_uri: Uri,
    department_name: str,
    render_layer_name: str,
    version_name: str
    ) -> Path:
    version_path = get_render_layer_export_path(
        entity_uri,
        department_name,
        render_layer_name,
        version_name
    )
    usd_file_name = '_'.join(entity_uri.segments[1:] + [
        department_name,
        render_layer_name,
        version_name
    ]) + '.usd'
    return version_path / usd_file_name

def next_render_layer_export_file_path(
    entity_uri: Uri,
    department_name: str,
    render_layer_name: str
    ) -> Path:
    export_path = latest_render_layer_export_path(
        entity_uri,
        department_name,
        render_layer_name
    )
    if export_path is None:
        export_path = get_render_layer_export_path(
            entity_uri,
            department_name,
            render_layer_name,
            'v0000'
        )
    next_version_path = get_next_version_path(export_path)
    version_name = next_version_path.name
    usd_file_name = '_'.join(entity_uri.segments[1:] + [
        department_name,
        render_layer_name,
        version_name
    ]) + '.usd'
    return next_version_path / usd_file_name

def latest_render_layer_export_file_path(
    entity_uri: Uri,
    department_name: str,
    render_layer_name: str
    ) -> Optional[Path]:
    export_path = latest_render_layer_export_path(
        entity_uri,
        department_name,
        render_layer_name
    )
    if export_path is None: return None
    version_name = export_path.name
    usd_file_name = '_'.join(entity_uri.segments[1:] + [
        department_name,
        render_layer_name,
        version_name
    ]) + '.usd'
    return export_path / usd_file_name

def latest_export_file_path(
    entity_uri: Uri,
    department_name: str
    ) -> Optional[Path]:
    usd_file_name_pattern = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            department_name,
            '*'
        ]),
        'usd'
    ])
    latest_version_path = latest_export_path(
        entity_uri,
        department_name
    )
    if latest_version_path is None: return None
    version_name = latest_version_path.name
    usd_file_name = usd_file_name_pattern.replace('*', version_name)
    return latest_version_path / usd_file_name

def next_export_path(
    entity_uri: Uri,
    department_name: str
    ) -> Path:
    export_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        department_name
    )
    export_path = api.storage.resolve(export_uri)
    return get_next_version_path(export_path)

def next_export_file_path(
    entity_uri: Uri,
    department_name: str
    ) -> Path:
    usd_file_name_pattern = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            department_name,
            '*'
        ]),
        'usd'
    ])
    version_path = next_export_path(
        entity_uri,
        department_name
    )
    version_name = version_path.name
    usd_file_name = usd_file_name_pattern.replace('*', version_name)
    return version_path / usd_file_name

###############################################################################
# Staged Paths
###############################################################################
def get_staged_path(
    entity_uri: Uri,
    version_name: str
    ) -> Path:
    staged_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        '_staged' /
        version_name
    )
    return api.storage.resolve(staged_uri)

def get_staged_file_path(
    entity_uri: Uri,
    version_name: str
    ) -> Path:
    version_path = get_staged_path(
        entity_uri,
        version_name
    )
    usd_file_name = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            version_name
        ]),
        'usda'
    ])
    return version_path / usd_file_name

def latest_staged_path(
    entity_uri: Uri
    ) -> Optional[Path]:
    staged_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        '_staged'
    )
    staged_path = api.storage.resolve(staged_uri)
    version_paths = list_version_paths(staged_path)
    if len(version_paths) == 0: return None
    latest_version_path = version_paths[-1]
    return latest_version_path

def latest_staged_file_path(
    entity_uri: Uri
    ) -> Optional[Path]:
    usd_file_name_pattern = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            '*'
        ]),
        'usda'
    ])
    latest_version_path = latest_staged_path(
        entity_uri
    )
    if latest_version_path is None: return None
    version_name = latest_version_path.name
    usd_file_name = usd_file_name_pattern.replace('*', version_name)
    return latest_version_path / usd_file_name

def next_staged_path(
    entity_uri: Uri
    ) -> Path:
    staged_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        '_staged'
    )
    staged_path = api.storage.resolve(staged_uri)
    return get_next_version_path(staged_path)

def next_staged_file_path(
    entity_uri: Uri
    ) -> Path:
    usd_file_name_pattern = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            '*'
        ]),
        'usda'
    ])
    version_path = next_staged_path(
        entity_uri
    )
    version_name = version_path.name
    usd_file_name = usd_file_name_pattern.replace('*', version_name)
    return version_path / usd_file_name

def get_rig_export_path(asset_uri: Uri) -> Path:
    export_uri = Uri.parse_unsafe('export:/') / asset_uri.segments / 'rig'
    return api.storage.resolve(export_uri)