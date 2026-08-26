"""Deprecated alias for :mod:`tumblepipe.config.channels`.

The pipeline's publish-tree fork is now called a **channel** — "variant" is
reserved for USD's own ``variantSet``, which is a different mechanism (see the
:mod:`~tumblepipe.config.channels` docstring for why, and for the list of
places where ``variant`` remains the frozen wire token).

This module stays so that already-published packages and any out-of-tree
caller importing ``tumblepipe.config.variants`` keep working. New code should
import from :mod:`tumblepipe.config.channels`.
"""

from tumblepipe.config.channels import (
    DEFAULT_CHANNEL,
    add_channel,
    has_channel,
    list_channels,
    remove_channel,
)
from tumblepipe.config.entities import get_entity_type

DEFAULT_VARIANT = DEFAULT_CHANNEL

list_variants = list_channels
add_variant = add_channel
remove_variant = remove_channel
has_variant = has_channel

__all__ = [
    'DEFAULT_VARIANT',
    'list_variants',
    'add_variant',
    'remove_variant',
    'has_variant',
    'get_entity_type',
]
