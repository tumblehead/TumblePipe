"""Which of an entity's render channels render as USD render passes.

A channel with prims in ``channel_prims`` (or ``default`` beside one) is a
render layer (:func:`tumblepipe.config.channels.list_channel_layers`). Every
render of such an entity carries one ``RenderPass`` per layer
(:mod:`tumblepipe.pipe.usd.render_passes`) and each layer's job renders its
own pass with ``husk --pass``.

A layer needs no exports of its own. A channel no department ever exported
is a *pass-only* layer: it renders from the ``default`` channel's stage,
the pass picking its prims out, so splitting a shot into background and
characters is a config entry, not a branched workfile and an export per
channel. A channel that was exported keeps rendering its own stage (its
departments' own content), with its pass on top.
"""

from tumblepipe.util.uri import Uri
from tumblepipe.config.channels import DEFAULT_CHANNEL, list_channel_layers
from tumblepipe.config.department import list_departments
from tumblepipe.pipe.paths import latest_export_path

#: Where the render passes live; husk renders one with ``--pass <path>``.
PASSES_ROOT = '/Render/Passes'


class RenderPassError(ValueError):
    """A render layer's prim pattern cannot be expanded where it is asked."""


def render_pass_path(channel_name: str) -> str:
    """The prim path of ``channel_name``'s render pass."""
    return f'{PASSES_ROOT}/{channel_name}'


def _context(entity_uri: Uri) -> str:
    return 'shots' if entity_uri.segments and entity_uri.segments[0] == 'shots' else 'assets'


def layer_names(entity_uri: Uri) -> list[str]:
    """The entity's render layers, bottom to top; empty when it has none."""
    return [name for name, _ in list_channel_layers(entity_uri)]


def was_exported(entity_uri: Uri, channel_name: str) -> bool:
    """Whether any department has a finished export under ``channel_name``."""
    return any(
        latest_export_path(entity_uri, channel_name, department.name) is not None
        for department in list_departments(_context(entity_uri))
    )


def stage_channel(entity_uri: Uri, channel_name: str, layers: list[str] | None = None) -> str:
    """The channel whose staged build ``channel_name`` renders: its own,
    or ``default`` for a pass-only layer. ``layers`` is
    :func:`layer_names`, when the caller already has it."""
    if channel_name == DEFAULT_CHANNEL:
        return channel_name
    if layers is None:
        layers = layer_names(entity_uri)
    if channel_name in layers and not was_exported(entity_uri, channel_name):
        return DEFAULT_CHANNEL
    return channel_name


def stage_channels(entity_uri: Uri, channels: list[str]) -> list[str]:
    """The staged builds rendering ``channels`` needs, in order, once each:
    every pass-only layer shares the default one."""
    layers = layer_names(entity_uri)
    return list(dict.fromkeys(
        stage_channel(entity_uri, channel, layers) for channel in channels
    ))


def render_pass(entity_uri: Uri, channel_name: str, layers: list[str] | None = None) -> str | None:
    """The ``husk --pass`` of ``channel_name``'s render, or None when the
    entity has no render layers or the channel is not one of them."""
    if layers is None:
        layers = layer_names(entity_uri)
    return render_pass_path(channel_name) if channel_name in layers else None
