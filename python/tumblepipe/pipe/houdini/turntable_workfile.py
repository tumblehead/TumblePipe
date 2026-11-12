"""Build the workfile a Farm Submit asset turntable renders from (#53).

The farm collapse job (``farm/workers/collapse_houdini.py``) runs this in
hython after snapshotting the asset's staged build: a scene of two nodes, a
Sublayer LOP reading the snapshot and ``th::lookdev_studio`` with its
Selection > Primitives on the asset and its batch-render switch on, which is
the scene a studio's own Submit Render To Farm saves. The export job then
exports that studio exactly as it exports a lookdev workfile's
(``farm/workers/export_houdini.py``), so a Farm Submit turntable and a
lookdev one share their camera, lights, backdrop, render vars and the
studio's embedded HDRI and textures, copied beside the exported stage.

The snapshot is sublayered as ``$HIP/<name>``: the export job finds it
beside the workfile in its data dir.
"""

from pathlib import Path

import hou

from tumblepipe.api import path_str
from tumblepipe.pipe import turntable


def build(asset_input_name: str, prim_path: str, hip_path: Path) -> list[str]:
    """Save a turntable workfile at ``hip_path`` and return its AOV names.

    ``asset_input_name`` is the snapshot's file name beside ``hip_path``;
    ``prim_path`` is the asset's root prim, the studio's Selection >
    Primitives. The AOV names are the studio's render vars, as its own
    submit reads them (``LookdevStudio.get_aov_names``); reading them cooks
    the studio, so a turntable that would not cook fails here, on the
    collapse job, with the studio's own error.
    """
    from tumblepipe.pipe.houdini.lops.lookdev_studio import LookdevStudio

    # The previous channel's nodes go; the session itself stays (clearing
    # it would also drop HDA libraries installed into it).
    network = hou.node(turntable.WORKFILE_NETWORK)
    for child in network.children():
        child.destroy()
    asset = network.createNode('sublayer', 'ASSET')
    asset.parm('filepath1').set(f'$HIP/{asset_input_name}')
    studio = network.createNode(turntable.NODE_TYPE, turntable.WORKFILE_NODE)
    studio.setInput(0, asset)
    studio.parm('primpattern').set(prim_path)
    # On for the render, as the studio's submit_to_farm sets it while it
    # saves: a batch render swaps the light rig once per loop.
    studio.parm('batchrender').set(1)
    studio.setDisplayFlag(True)

    hip_path = Path(hip_path)
    hip_path.parent.mkdir(parents=True, exist_ok=True)
    hou.hipFile.save(path_str(hip_path))
    return LookdevStudio(studio).get_aov_names()
