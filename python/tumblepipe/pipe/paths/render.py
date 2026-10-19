"""Render output: where frames go, and the Render / Layer / AOV model read back.

``{purpose}:/render/{entity}/{render dept}/{layer}/{version}/`` holds a
layer's frames, or one directory per AOV. The path builders write it; the
model (``get_render``, ``get_render_context``) walks it back. Playblasts and
dailies live in ``paths.review``.
"""

from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from tumblepipe.api import api
from tumblepipe import naming
from tumblepipe.config.timeline import BlockRange
from tumblepipe.config.department import list_departments
from tumblepipe.config.channels import list_channels
from tumblepipe.util.io import load_json
from tumblepipe.util.uri import Uri

from tumblepipe.pipe.paths.naming import entity_stem
from tumblepipe.pipe.paths.version import (
    list_version_paths,
    get_next_version_path,
)

###############################################################################
# Render model
###############################################################################
def _load_frame_range(context_path: Path) -> Optional[BlockRange]:
    """The frame range a render version records, None when it has no record.

    The render job writes ``context.json`` into the version directory when
    it submits; a version directory without one was not made by a render
    submission and has no range to check frames against.
    """
    if not context_path.exists(): return None
    context = load_json(context_path)
    return BlockRange(
        context['first_frame'],
        context['last_frame'],
        context['step_size']
    )

@dataclass(frozen=True)
class AOV:
    path: Path
    label: str
    name: str
    suffix: str

    def get_aov_frame_path(self, index_pattern: str) -> Path:
        return self.path / f'{self.name}.{index_pattern}.{self.suffix}'

    def get_frame_range(self) -> Optional[BlockRange]:
        return _load_frame_range(self.path.parent / 'context.json')

    def is_complete(self, expected_frame_range: BlockRange) -> bool:
        frame_path = self.get_aov_frame_path('*')
        actual_frame_indices = sorted(
            int(path.stem.split('.')[-1])
            for path in frame_path.parent.glob(frame_path.name)
        )
        return actual_frame_indices == list(expected_frame_range)

@dataclass(frozen=True)
class Layer:
    path: Path
    label: str
    version: str
    aovs: dict[str, AOV]
    name: str
    suffix: str

    def get_frame_range(self) -> Optional[BlockRange]:
        return _load_frame_range(self.path / 'context.json')

    def get_aov_frame_path(
        self,
        aov_name: str,
        index_pattern: str
        ) -> Optional[Path]:
        if aov_name not in self.aovs: return None
        return self.aovs[aov_name].get_aov_frame_path(index_pattern)

    def is_complete(self, expected_frame_range: Optional[BlockRange] = None) -> bool:
        frame_range = expected_frame_range
        if frame_range is None:
            frame_range = self.get_frame_range()
        if frame_range is None: return False
        if len(self.aovs) == 0:
            # AOV-less layers write frames directly at the layer path
            layer_frame = AOV(
                path = self.path,
                label = self.label,
                name = self.name,
                suffix = self.suffix
            )
            return layer_frame.is_complete(frame_range)
        return all(aov.is_complete(frame_range) for aov in self.aovs.values())

@dataclass(frozen=True)
class Render:
    path: Path
    layers: dict[str, dict[str, Layer]]

    def get_latest_complete_layer(
        self,
        layer_name: str,
        expected_frame_range: Optional[BlockRange] = None
        ) -> Optional[Layer]:
        if layer_name not in self.layers: return None
        version_names = sorted(
            self.layers[layer_name].keys(),
            key = naming.get_version_code
        )
        for version_name in reversed(version_names):
            layer = self.layers[layer_name][version_name]
            if layer.is_complete(expected_frame_range):
                return layer
        return None

    def list_latest_complete_aovs(self) -> dict[str, dict[str, AOV]]:
        aovs = dict()
        for layer_name in self.layers.keys():
            layer = self.get_latest_complete_layer(layer_name)
            if layer is None: continue
            aovs[layer_name] = layer.aovs.copy()
        return aovs

@dataclass(frozen=True)
class RenderContext:
    """Every render department's ``Render`` of one entity."""
    renders: dict[str, Render]

    def list_latest_complete_aovs(self) -> dict[str, dict[str, dict[str, AOV]]]:
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

        def get_dept_priority(dept, priority_list):
            if dept is None: return -1
            try:
                return priority_list.index(dept)
            except ValueError:
                return -1

        def get_latest_layer_with_frame_range(layer_versions):
            version_names = sorted(
                layer_versions.keys(),
                key = naming.get_version_code
            )
            for version_name in reversed(version_names):
                layer = layer_versions[version_name]
                frame_range = layer.get_frame_range()
                if frame_range is None: continue
                return version_name, layer, frame_range
            return None

        def get_shot_department(layer):
            # Present: the layer was picked for having a recorded range.
            return load_json(layer.path / 'context.json')['department']

        min_shot_idx = get_dept_priority(min_shot_department, shot_department_priority)
        min_render_idx = get_dept_priority(min_render_department, render_department_priority)

        latest_aovs = {}
        # Rank of each stored entry: (shot dept, render dept, version) priority,
        # compared lexicographically - higher wins.
        ranks = {}

        for render_department_name, render in self.renders.items():
            # Department must meet the minimum threshold in at least one priority list
            shot_idx = get_dept_priority(render_department_name, shot_department_priority)
            render_idx = get_dept_priority(render_department_name, render_department_priority)
            if shot_idx < min_shot_idx and render_idx < min_render_idx:
                continue

            for layer_name, layer_versions in render.layers.items():
                # Only the latest version with a frame range is considered per layer
                latest = get_latest_layer_with_frame_range(layer_versions)
                if latest is None: continue
                version_name, layer, frame_range = latest

                shot_dept = get_shot_department(layer)
                rank = (
                    get_dept_priority(shot_dept, shot_department_priority),
                    get_dept_priority(render_department_name, render_department_priority),
                    naming.get_version_code(version_name)
                )

                for aov_name, aov in layer.aovs.items():
                    if aov_filter is not None and not aov_filter(aov_name): continue
                    if not aov.is_complete(frame_range): continue
                    key = (layer_name, aov_name)
                    if key in ranks and rank <= ranks[key]: continue
                    ranks[key] = rank
                    latest_aovs.setdefault(layer_name, {})[aov_name] = (
                        render_department_name, version_name, aov, shot_dept
                    )

        return latest_aovs

###############################################################################
# Render paths
###############################################################################
def _render_dir(
    entity_uri: Uri,
    render_department_name: str,
    render_layer_name: str,
    purpose: str
    ) -> Path:
    """``{purpose}:/render/{entity}/{render dept}/{layer}`` -- the layer's versions."""
    return api.storage.resolve(
        Uri.parse(f'{purpose}:/render') /
        entity_uri.segments /
        render_department_name /
        render_layer_name
    )

def get_frame_path(
    entity_uri: Uri,
    render_department_name: str,
    render_layer_name: str,
    version_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Path:
    render_path = _render_dir(
        entity_uri, render_department_name, render_layer_name, purpose
    ) / version_name
    stem = entity_stem(entity_uri, render_layer_name, version_name)
    return render_path / f'{stem}.{frame_pattern}.{suffix}'

def get_next_frame_path(
    entity_uri: Uri,
    render_department_name: str,
    render_layer_name: str,
    frame_pattern: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Path:
    version_path = get_next_version_path(_render_dir(
        entity_uri, render_department_name, render_layer_name, purpose
    ))
    return get_frame_path(
        entity_uri, render_department_name, render_layer_name,
        version_path.name, frame_pattern, suffix, purpose
    )

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
    render_path = _render_dir(
        entity_uri, render_department_name, render_layer_name, purpose
    ) / version_name / aov_name
    stem = entity_stem(entity_uri, render_layer_name, aov_name, version_name)
    return render_path / f'{stem}.{frame_pattern}.{suffix}'

###############################################################################
# Reading renders back
###############################################################################
def get_render(
    entity_uri: Uri,
    render_department_name: str,
    suffix: str = 'exr',
    purpose: str = 'render'
    ) -> Optional[Render]:
    """The ``Render`` of one render department, None when it never rendered."""
    render_department_path = api.storage.resolve(
        Uri.parse(f'{purpose}:/render') /
        entity_uri.segments /
        render_department_name
    )
    if not render_department_path.exists(): return None

    # Collect render layers (one per entity channel, plus the slapcomp layer)
    render_layers = dict()
    render_layer_names = [*list_channels(entity_uri), 'slapcomp']
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
                aovs[aov_name] = AOV(
                    path = aov_path,
                    label = aov_name,
                    name = entity_stem(
                        entity_uri, render_layer_name, aov_name, version_name
                    ),
                    suffix = suffix
                )

            # Collect layer
            layer_versions[version_name] = Layer(
                path = version_path,
                label = render_layer_name,
                version = version_name,
                aovs = aovs,
                name = entity_stem(entity_uri, render_layer_name, version_name),
                suffix = suffix
            )
        render_layers[render_layer_name] = layer_versions

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
    render_department_names = [d.name for d in list_departments('render')]
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
# Proxies
###############################################################################
def mirror_aov(aov: AOV, from_root: Path, to_root: Path) -> AOV:
    """``aov`` at the same relative place under ``to_root`` as under
    ``from_root``, with the same file names."""
    return AOV(
        path = to_root / aov.path.relative_to(from_root),
        label = aov.label,
        name = aov.name,
        suffix = aov.suffix
    )

def get_proxy_aov(aov: AOV, resolution_name: str) -> AOV:
    """The ``resolution_name`` proxy of a rendered AOV.

    A proxy mirrors its render: ``render:/render/{rest}`` is proxied at
    ``proxy:/render/{resolution}/{rest}``, the same department, channel,
    version and file names, its version directory holding a copy of the
    render's ``context.json``. A comp on proxies therefore reads exactly the
    versions a comp on the renders would.
    """
    return mirror_aov(
        aov,
        api.storage.resolve(Uri.parse('render:/render')),
        api.storage.resolve(Uri.parse('proxy:/render')) / resolution_name
    )

###############################################################################
# Comp inputs
###############################################################################
# The AOVs build_comp wires every channel's comp from: beauty feeds the grade
# subnet. The beauty is RGBA and its A is the grade subnet's alpha input, so
# no separate alpha AOV is required.
REQUIRED_COMP_AOVS = ('beauty',)

def comp_input_problems(
    aov_labels: dict[str, list[str]],
    channel_names: list[str],
    ) -> list[str]:
    """What stops a comp being built from these renders; empty when nothing does.

    ``aov_labels`` maps a channel name to the AOV labels of its latest
    complete render (what build_comp found); ``channel_names`` are the shot's
    channels. A comp needs at least one channel rendered, and each rendered
    channel needs every ``REQUIRED_COMP_AOVS`` label.
    """
    rendered = [
        channel_name for channel_name in channel_names
        if aov_labels.get(channel_name)
    ]
    if not rendered:
        return ['no complete render was found for any channel']
    problems = []
    for channel_name in rendered:
        labels = set(aov_labels[channel_name])
        missing = [name for name in REQUIRED_COMP_AOVS if name not in labels]
        if missing:
            problems.append(
                f'channel "{channel_name}" has no {" or ".join(missing)} AOV'
            )
    return problems
