"""Snapshot a staged stage for a farm preview, and the job configs that read it.

A render or playblast on the farm never reads the live staged build: it reads
a *collapsed* copy — every ``latest`` reference resolved to a file path,
bundled with the job — so the frames come out of exactly the stage that was
snapshotted. When that snapshot is taken is the whole question:

* **At submit time** (``batch_submit``), when the submission publishes nothing
  for the entity. The stage on disk is what the artist wants to see, and a
  broken one fails in the dialog instead of on the farm.
* **On the farm, after the submission's publishes and staged build**
  (``farm/tasks/collapse``), when it does. Snapshotting at submit time there
  is the bug this module was split out for: the Deadline dependency on the
  publish job delayed the preview until the publish had run, but its input
  had already been collapsed from the *previous* publish, so "publish anim +
  playblast" played the old anim and nothing said so.

Both paths build their inputs and job configs here, so a render from Houdini
and a render after a farm publish cannot drift apart.

``collapse_*`` compose the staged stage through USD and the project's
resolver, so they need ``pxr`` and a resolver-aware environment: Houdini's
own interpreter (in session, or its bundled ``python3.x`` with the package
environment) or hython on the farm. The config builders need neither.
"""

from pathlib import Path

from tumblepipe.api import api, path_str
from tumblepipe.util.uri import Uri
from tumblepipe.util.io import load_json, store_json, store_text
from tumblepipe.pipe.context import (
    get_aov_names_from_context,
    aggregate_aov_names_from_inputs,
)
from tumblepipe.config.department import list_departments, department_names_up_to
from tumblepipe.config.timeline import get_fps, get_frame_range
from tumblepipe.pipe.paths import latest_export_path, get_latest_staged_file_path
from tumblepipe.pipe.usd import (
    LayerCollectionError,
    RenderSettingsError,
    collapse_latest_references,
    excluded_staged_refs,
)

# The local playblast HDAs default to 720p; farm playblasts match unless the
# submission overrides it.
DEFAULT_PLAYBLAST_RES = [1280, 720]

# Relative names the snapshots are bundled under. The render and playblast
# tasks resolve these against their job data dir (job_data_dir()).
RENDER_SETTINGS_NAME = 'render_settings.json'
PLAYBLAST_INPUT_NAME = 'collapsed_playblast.usda'


def render_input_name(channel_name: str) -> str:
    return f'collapsed_stage_{channel_name}.usda'


class PreviewError(Exception):
    """A preview's input or config could not be built for one entity."""


def entity_context(entity_uri: Uri) -> str:
    return 'shots' if str(entity_uri).startswith('entity:/shots/') else 'assets'


def department_cut(context: str, department: str) -> tuple[list[str], list[str]]:
    """``(cut, pool)``: the pool departments up to ``department``, and the pool.

    Sliced over the whole pool, not just the renderable departments: the cut
    is by pipeline position, and a non-renderable upstream department
    (tracking, notes) composes into the staged build like any other.
    """
    pool = [d.name for d in list_departments(context)]
    return department_names_up_to(pool, department), pool


def aov_names(entity_uri: Uri, channels: list[str]) -> list[str]:
    """AOV names from the entity's exports, including its referenced assets.

    Every department is checked, since AOVs can be defined in any of them,
    then the assets each export's context names. Falls back to the root layer
    context (``config:/usd/context.json``) when no export declares any.
    """
    aov_set = set()
    departments = [d.name for d in list_departments(entity_context(entity_uri))]
    for channel in channels:
        for department in departments:
            export_path = latest_export_path(entity_uri, channel, department)
            if export_path is None:
                continue
            context_data = load_json(export_path / 'context.json')
            if context_data is None:
                continue
            aov_set.update(get_aov_names_from_context(context_data, channel))
            aov_set.update(aggregate_aov_names_from_inputs(context_data, channel))
    if aov_set:
        return list(aov_set)

    root_context = load_json(api.storage.resolve(
        Uri.parse_unsafe('config:/usd/context.json')
    ))
    return get_aov_names_from_context(root_context) or []


def write_render_settings(
    temp_path: Path,
    paths: dict,
    channels: list[str],
    aov_names: list[str],
    overrides: dict | None = None,
) -> Path:
    """Write ``render_settings.json`` and register it for bundling.

    ``overrides`` belong here only for a *standalone* render, where the farm
    stage task applies them while building; a direct render bakes them into
    the collapsed stage instead.
    """
    data = dict(variant_names=channels, aov_names=aov_names)
    if overrides is not None:
        data['overrides'] = overrides
    settings_path = temp_path / RENDER_SETTINGS_NAME
    store_json(settings_path, data)
    paths[settings_path] = settings_path.relative_to(temp_path)
    return settings_path


def collapse_render_inputs(
    entity_uri: Uri,
    channels: list[str],
    department: str,
    overrides: dict,
    temp_path: Path,
    paths: dict,
) -> dict[str, str]:
    """Collapse the latest staged stage of every channel, cut at ``department``.

    Returns ``{channel: relative input path}`` for the render config, and
    registers each collapsed file in ``paths``. The render ``overrides`` are
    baked onto the stage's own render-settings prim.
    """
    cut, pool = department_cut(entity_context(entity_uri), department)
    input_paths = {}
    for channel_name in channels:
        staged_path = get_latest_staged_file_path(entity_uri, channel_name)
        if staged_path is None or not staged_path.exists():
            raise PreviewError(
                f"No staged file found for {entity_uri} channel "
                f"'{channel_name}'. Expected staged files at: "
                f"export:/{'/'.join(entity_uri.segments)}/_staged/"
                f"{channel_name}/. Publish the entity first to create "
                "staged files."
            )
        # Drop the department layers past the cut. The staged build composes
        # every department that exported, so this is what makes 'render up
        # to lighting' mean it.
        excluded = excluded_staged_refs(staged_path, cut, pool)
        collapsed_path = temp_path / render_input_name(channel_name)
        try:
            content = collapse_latest_references(
                staged_path, collapsed_path, overrides, excluded_refs=excluded,
            )
        except LayerCollectionError as error:
            # A layer the build records but that is missing from disk: a
            # render around it would have that department silently absent.
            raise PreviewError(
                f"Cannot collapse the staged stage for {entity_uri} "
                f"channel '{channel_name}': {error}"
            ) from error
        store_text(collapsed_path, content)
        relative = collapsed_path.relative_to(temp_path)
        paths[collapsed_path] = relative
        input_paths[channel_name] = path_str(relative)
    return input_paths


def collapse_playblast_input(
    entity_uri: Uri,
    department: str,
    temp_path: Path,
    paths: dict,
) -> str:
    """Collapse the ``default`` staged stage for a playblast, cut at ``department``.

    Playblasts read the same stage husk renders but not through the project's
    Karma settings: ``playblast=True`` authors a Storm-renderable
    RenderSettings aimed at the camera the project's settings name. Returns
    the relative input path and registers the file in ``paths``.
    """
    staged_path = get_latest_staged_file_path(entity_uri, 'default')
    if staged_path is None or not staged_path.exists():
        raise PreviewError(
            f"No staged file found for {entity_uri} channel 'default'. "
            "Publish the shot first to create staged files."
        )
    cut, pool = department_cut(entity_context(entity_uri), department)
    excluded = excluded_staged_refs(staged_path, cut, pool)
    collapsed_path = temp_path / PLAYBLAST_INPUT_NAME
    try:
        content = collapse_latest_references(
            staged_path, collapsed_path, {},
            excluded_refs=excluded, playblast=True,
        )
    except LayerCollectionError as error:
        raise PreviewError(
            f"Cannot collapse the staged stage for the {entity_uri} "
            f"playblast: {error}"
        ) from error
    except RenderSettingsError as error:
        # Refused rather than rendered from whatever camera husk lands on: a
        # playblast of the wrong view reads as a finished preview all the way
        # into dailies.
        raise PreviewError(f"Cannot playblast {entity_uri}: {error}") from error
    store_text(collapsed_path, content)
    relative = collapsed_path.relative_to(temp_path)
    paths[collapsed_path] = relative
    return path_str(relative)


def render_config(
    entity_uri: Uri,
    *,
    department: str,
    channels: list[str],
    input_paths: dict[str, str],
    render_settings_path: Path,
    user_name: str,
    pool_name: str,
    priority: int,
    tile_count: int,
    first_frame: int,
    last_frame: int,
    batch_size: int,
    denoise: bool,
    copy_to_edit: bool,
    task_key: str,
) -> dict:
    """The ``render/job.py`` config for a direct render of collapsed inputs.

    ``render_settings_path`` is absolute: the job builder reads it locally.
    ``input_paths`` are relative, resolved in the job data dir on the farm.
    """
    return dict(
        entity=dict(uri=str(entity_uri), department=department),
        settings=dict(
            user_name=user_name,
            purpose='render',
            pool_name=pool_name,
            variant_names=channels,
            render_department_name=department,
            render_settings_path=path_str(render_settings_path),
            input_paths=input_paths,
            tile_count=tile_count,
            first_frame=first_frame,
            last_frame=last_frame,
            step_size=1,
            batch_size=batch_size,
            copy_to_edit=copy_to_edit,
        ),
        tasks={
            task_key: dict(
                priority=priority,
                denoise=denoise,
                channel_name='renders',
            )
        },
    )


def playblast_config(
    entity_uri: Uri,
    *,
    department: str,
    input_path: str,
    user_name: str,
    pool_name: str,
    priority: int,
    res: list[int] | None = None,
) -> dict:
    """The ``playblast/job.py`` config for one shot.

    The range and fps are the shot's own (rolls included), matching the
    local playblast HDA — never a form value, so each shot in a batch plays
    its own full range.
    """
    frame_range = get_frame_range(entity_uri)
    if frame_range is None:
        raise PreviewError(f"No frame range configured for {entity_uri}")
    playblast_range = frame_range.full_range()
    return dict(
        entity=dict(uri=str(entity_uri), department=department),
        settings=dict(
            user_name=user_name,
            purpose='render',
            pool_name=pool_name,
            priority=priority,
            input_path=input_path,
            first_frame=playblast_range.first_frame,
            last_frame=playblast_range.last_frame,
            step_size=1,
            fps=get_fps(entity_uri) or 24,
            res=list(res or DEFAULT_PLAYBLAST_RES),
            channel_name='renders',
        ),
    )
