"""th::build_comp's proxies: the comp's AOVs, scaled down for a lighter comp.

With Quality on Proxy, Update cooks the TOP network inside the node
(``proxy/tops``). Its steps call this module:

1. ``generate`` -- one work item per AOV the comp reads whose proxy is
   missing frames, split by how it is read and written (RGBA, RGB, mono);
2. ``generate_frames`` -- one work item per missing frame, each cooking a
   File COP -> ROP Image pair that writes the frame at the proxy's pixel
   scale;
3. ``read_proxies`` -- once every frame is written, the comp's imports are
   pointed at the proxies.

The AOVs proxied are the ones a comp on the renders reads (the newest
complete version of each, departments up to the node's Input), and a proxy
mirrors its render's place on disk (``paths.get_proxy_aov``), so the comp's
own resolution finds the proxies of exactly those versions.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

from tumblepipe.api import path_str
from tumblepipe.config.timeline import BlockRange
from tumblepipe.pipe.houdini.cops import comp_aovs
from tumblepipe.pipe.houdini.cops.build_comp import BuildComp
from tumblepipe.pipe.houdini.cops.comp_aovs import AOVType
from tumblepipe.pipe.paths import get_proxy_aov


class TaskType:
    """Which File COP / ROP Image pair a proxy is cooked through: the beauty
    keeps its alpha (the comp's alpha is the beauty's A), mattes, holdouts
    and ramps are one channel, everything else three."""
    RGBA = 'rgba'
    RGB = 'rgb'
    Mono = 'mono'


# Where a frame number goes in a work item's paths, filled in per frame.
FRAME_TOKEN = '$F4'


def task_type(label):
    """The ``TaskType`` an AOV label is proxied as."""
    match comp_aovs.aov_type(label):
        case AOVType.LPE:
            return TaskType.RGBA if label == 'beauty' else TaskType.RGB
        case AOVType.Mask | AOVType.Mono:
            return TaskType.Mono
        case AOVType.Util:
            return TaskType.RGB


def frame_path(path_template, frame):
    """``path_template`` with ``FRAME_TOKEN`` replaced by ``frame``, padded
    to four digits like the renders."""
    return path_template.replace(FRAME_TOKEN, str(frame).zfill(4))


@dataclass(frozen=True)
class ProxyJob:
    """One AOV's proxy: read ``import_plane`` of ``import_path``'s frames,
    write them as ``label`` to ``export_path`` at ``scale``."""
    channel: str
    label: str
    task_type: str
    import_plane: str
    import_path: str
    export_path: str
    scale: float
    frames: BlockRange


def plan(aovs, resolution_name, proxy_aov, plane_name):
    """The ``ProxyJob`` of every AOV the comp reads, in channel then label
    order.

    ``aovs`` is ``{channel: {label: AOV}}`` of the resolved renders;
    ``proxy_aov(aov, resolution_name)`` gives an AOV's proxy and
    ``plane_name(aov)`` the plane its File COP reads. Every resolved AOV is
    complete, so each has the frame range it was checked against.
    """
    scale = comp_aovs.resolution_scale(resolution_name)
    jobs = []
    for channel_name in sorted(aovs):
        channel_aovs = aovs[channel_name]
        for label in sorted(channel_aovs):
            if not comp_aovs.aov_included(label): continue
            aov = channel_aovs[label]
            proxy = proxy_aov(aov, resolution_name)
            jobs.append(ProxyJob(
                channel = channel_name,
                label = label,
                task_type = task_type(label),
                import_plane = plane_name(aov),
                import_path = path_str(aov.get_aov_frame_path(FRAME_TOKEN)),
                export_path = path_str(proxy.get_aov_frame_path(FRAME_TOKEN)),
                scale = scale,
                frames = aov.get_frame_range(),
            ))
    return jobs


def missing_frames(export_path, frames, exists):
    """The frames of ``frames`` whose ``export_path`` frame ``exists`` says
    is not written yet."""
    return [
        frame for frame in frames
        if not exists(frame_path(export_path, frame))
    ]


def _copy_context(aov, proxy):
    """Give the proxy's version directory the render's ``context.json``: the
    frame range a proxy version is complete against."""
    source = aov.path.parent / 'context.json'
    target = proxy.path.parent / 'context.json'
    target.parent.mkdir(parents = True, exist_ok = True)
    shutil.copyfile(source, target)


def _build_comp_of(top_node):
    """The th::build_comp a node of its ``proxy/tops`` network belongs to."""
    return BuildComp(top_node.parent().parent().parent())


def _exists(path):
    return Path(path).exists()


def generate(top_node, item_holder):
    """The ``generate`` step: a work item per AOV whose proxy misses frames."""
    node = _build_comp_of(top_node)
    resolution_name = node.get_proxy_resolution()
    aovs = node.resolve_render_aovs()
    for job in plan(aovs, resolution_name, get_proxy_aov, comp_aovs.aov_plane_name):
        if not missing_frames(job.export_path, job.frames, _exists): continue
        aov = aovs[job.channel][job.label]
        _copy_context(aov, get_proxy_aov(aov, resolution_name))
        item = item_holder.addWorkItem()
        item.setStringAttrib('task_type', job.task_type)
        item.setStringAttrib('aov_name', job.label)
        item.setStringAttrib('import_plane', job.import_plane)
        item.setStringAttrib('import_path', job.import_path)
        item.setStringAttrib('export_path', job.export_path)
        item.setFloatAttrib('scale', job.scale)
        item.setIntAttrib('start_frame', job.frames.first_frame)
        item.setIntAttrib('end_frame', job.frames.last_frame)
        item.setIntAttrib('step_size', job.frames.step_size)


def generate_frames(upstream_items, item_holder):
    """The ``frames_*`` steps: a work item per missing frame of each AOV,
    its ``import_path`` and ``export_path`` that frame's files."""
    for upstream_item in upstream_items:
        import_path = upstream_item.stringAttribValue('import_path')
        export_path = upstream_item.stringAttribValue('export_path')
        frames = BlockRange(
            upstream_item.intAttribValue('start_frame'),
            upstream_item.intAttribValue('end_frame'),
            upstream_item.intAttribValue('step_size'),
        )
        for frame in missing_frames(export_path, frames, _exists):
            item = item_holder.addWorkItem(parent = upstream_item)
            item.setStringAttrib('import_path', frame_path(import_path, frame))
            item.setStringAttrib('export_path', frame_path(export_path, frame))


def read_proxies(top_node):
    """The ``update`` step: point the comp's imports at the proxies."""
    _build_comp_of(top_node).update_imports()
