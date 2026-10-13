"""Itemise the Copernicus layers COP nodes are holding.

Copernicus keeps every cooked node's output (SideFX, 2026: freeing results
no longer needed is "an optimization we don't do yet"), so a long chain at
8K holds a full set of buffers per node — which is exactly what an artist
cannot see anywhere else.

Only nodes that have already cooked are read: ``CopNode.layer()`` cooks a
node that hasn't, and an inspector that starts an 8K cook to measure memory
would be worse than none. Those are counted in :attr:`CopScan.skipped`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import hou

from . import sizes
from .report import CopLayer

#: Output data types that are never image layers.
_NOT_LAYERS = frozenset({"Geometry", "VDB", "Metadata", "Camera", "Light"})


@dataclass
class CopScan:
    layers: list[CopLayer] = field(default_factory=list)
    skipped: int = 0


def cop_nodes() -> list[hou.CopNode]:
    """Every Copernicus node in the scene, found by type instance (fast)
    rather than by walking the whole node tree."""
    nodes: list[hou.CopNode] = []
    for node_type in hou.copNodeTypeCategory().nodeTypes().values():
        nodes.extend(node_type.instances())
    return nodes


def scan() -> CopScan:
    result = CopScan()
    for node in cop_nodes():
        try:
            # A locked HDA's output *is* one of its inner nodes' buffers
            # (gpumem shows one buffer, not two), so the HDA the artist
            # placed carries it and its insides are not listed again.
            if node.isInsideLockedHDA():
                continue
            if node.needsToCook():
                result.skipped += 1
                continue
            names = node.outputNames()
            types = node.outputDataTypes()
        except hou.Error:
            continue
        for index, dtype in enumerate(types):
            if dtype in _NOT_LAYERS:
                continue
            layer = _layer(node, index)
            if layer is None:
                continue
            w, h = layer.bufferResolution()
            channels = layer.channelCount()
            storage = layer.storageType().name()
            data, gpu = sizes.layer_bytes(w, h, channels, storage)
            result.layers.append(CopLayer(
                node=node.path(),
                output=names[index] if index < len(names) else f"output{index}",
                width=w, height=h, channels=channels, storage=storage,
                on_gpu=bool(layer.onGPU()), data_bytes=data, gpu_bytes=gpu))
    return result


def _layer(node: hou.CopNode, index: int):
    try:
        layer = node.layer(index)
    except (hou.Error, TypeError):
        return None
    return layer if isinstance(layer, hou.ImageLayer) else None
