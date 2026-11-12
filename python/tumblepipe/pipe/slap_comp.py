"""Copernicus slap comps for TumblePipe renders (#81): the preset, and the
program a farm render applies.

Not the render chain's ``slapcomp`` job (``farm/workers/slapcomp.py``, the
oiiotool stack of a shot's channels): this is Houdini's slap comp, a COP
block run on each rendered frame.

**The preset.** Houdini's stock Slap Comp Block (a Slap Comp Import, a Block
Begin and a Block End) names its layer ``C``, Karma's colour AOV. TumblePipe's
render settings name the colour AOV ``beauty``, so the stock block finds
nothing in the Solaris viewport. :func:`preset_nodes` is the same three nodes
with every slap comp AOV named ``beauty``; ``pipe/houdini/cops/slap_comp_preset.py``
builds them (Tab menu *TumblePipe Slap Comp* in a COP network).

**The farm.** husk takes a slap comp as a compiled program: ``--slap-comp
<file>``, a geometry file holding the block as an APEX graph (what the Block
to Geometry COP outputs; Houdini's USD Render ROP writes the same thing,
``<usd>_slapcomp_<n>.bgeo.sc``, for a slap comp whose Source is a COP
network). husk runs it on each frame, and an output overwrites the AOV of
the same name. Farm renders do not open the workfile, so the program is
published per entity: the preset's Block End carries a **Publish to Farm**
button that compiles the block into the next version of
``export:/<entity>/_slapcomp/`` (:func:`publish`). Every farm render of the
entity then bundles the newest published program into its job and hands it
to husk (:func:`farm_program`, ``farm/pipelines/render.py``). Publishing with
**Use on Farm Renders** off writes a version without one, which turns it
off for the renders after.

husk applies a slap comp to whatever image it renders, so a tiled render
would run it on each tile: a render with a slap comp must render whole
frames (Tiles 1), and :func:`tiles_problem` says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from tumblepipe.api import api
from tumblepipe.util.io import VERSION_MARKER, load_json_if_exists, store_json
from tumblepipe.util.uri import Uri
from tumblepipe.pipe.paths.naming import entity_stem
from tumblepipe.pipe.paths.version import (
    get_latest_version_path,
    get_next_version_path,
)

#: The colour AOV TumblePipe's render settings name (Karma's own is ``C``).
BEAUTY = 'beauty'

#: Karma's colour AOV, what the stock Slap Comp Block names.
KARMA_COLOR = 'C'

#: The node names the preset gives its three nodes.
IMPORT_NAME = 'slapcomp_import'
BEGIN_NAME = 'slapcomp_begin'
END_NAME = 'slapcomp_end'

#: ``export:/<entity>/<SLAP_COMP_FOLDER>/<version>/`` holds the programs.
SLAP_COMP_FOLDER = '_slapcomp'

#: The compiled program's extension: geometry, as Block to Geometry gives it.
PROGRAM_SUFFIX = '.bgeo.sc'

#: Where a render job bundles its program, relative to the job's data dir.
BUNDLE_DIR = 'slapcomp'


###############################################################################
# The preset
###############################################################################
@dataclass(frozen=True)
class NodeSpec:
    """One node of the preset: its type, name and place, its parm values and
    expressions, the multiparm holding its slap comp AOVs (``count_parm``
    rows of ``row_parm``, one per AOV) and its inputs (``(input index,
    source node name, source output index)``)."""
    type_name: str
    role: str           # IMPORT_NAME, BEGIN_NAME or END_NAME
    name: str           # the role and the preset's suffix
    position: tuple[float, float]
    count_parm: str
    row_parm: str
    aovs: tuple[str, ...]
    parms: dict = field(default_factory=dict)
    expressions: dict = field(default_factory=dict)
    inputs: tuple[tuple[int, str, int], ...] = ()


def preset_nodes(aovs: Sequence[str] = (BEAUTY,), suffix: str = '') -> list[NodeSpec]:
    """The preset's Slap Comp Import, Block Begin and Block End, every slap
    comp AOV named from ``aovs`` (``beauty`` by default), in creation order,
    each named after its role plus ``suffix`` (:func:`free_suffix`).

    Each AOV is one row on all three nodes, wired import -> begin -> end,
    the shape of Houdini's stock Slap Comp Block recipe
    (``sidefx::recipe::cop::slapcompblock``) with its ``C``/``depth`` rows
    replaced. The Block End is registered as a slap comp block, so the
    viewport's slap comp menu lists it.
    """
    aovs = tuple(aovs)
    if not aovs:
        raise ValueError('A slap comp needs at least one AOV')
    if len(set(aovs)) != len(aovs):
        raise ValueError(f'Slap comp AOVs repeat: {list(aovs)}')
    indices = range(len(aovs))
    import_name, begin_name, end_name = (
        f'{role}{suffix}' for role in (IMPORT_NAME, BEGIN_NAME, END_NAME)
    )
    return [
        NodeSpec(
            type_name='slapcompimport',
            role=IMPORT_NAME,
            name=import_name,
            position=(0.0, 0.0),
            count_parm='aovs',
            row_parm='aov#',
            aovs=aovs,
            # Layers come in where the Block End places them.
            expressions={'slapcompcameraspace': f'ch("../{end_name}/slapcompcameraspace")'},
        ),
        NodeSpec(
            type_name='block_begin',
            role=BEGIN_NAME,
            name=begin_name,
            position=(2.0, 0.0),
            count_parm='ports',
            row_parm='name#',
            aovs=aovs,
            parms={'blockpath': f'../{end_name}'},
            inputs=tuple((index, import_name, index) for index in indices),
        ),
        NodeSpec(
            type_name='block_end',
            role=END_NAME,
            name=end_name,
            position=(7.0, 0.0),
            count_parm='ports',
            row_parm='name#',
            aovs=aovs,
            parms={'slapcomp': 1, 'slapcompcameraspace': 0},
            inputs=tuple((index, begin_name, index) for index in indices),
        ),
    ]


def free_suffix(taken) -> str:
    """The suffix that makes none of the preset's three names one of
    ``taken``: none when they are free, else the smallest number from 2."""
    roles = (IMPORT_NAME, BEGIN_NAME, END_NAME)
    taken = set(taken)
    if not any(role in taken for role in roles):
        return ''
    number = 2
    while any(f'{role}{number}' in taken for role in roles):
        number += 1
    return str(number)


def row_name(pattern: str, index: int) -> str:
    """Multiparm row ``index`` (from 0) of ``pattern`` (``name#`` -> ``name1``)."""
    return pattern.replace('#', str(index + 1))


###############################################################################
# Published programs
###############################################################################
@dataclass(frozen=True)
class Published:
    """One finished version of an entity's slap comp: its folder and the
    program in it, None when it was published with Use on Farm Renders off."""
    version_path: Path
    program_path: Optional[Path]

    @property
    def version_name(self) -> str:
        return self.version_path.name


def slap_comp_dir(entity_uri: Uri) -> Path:
    """``export:/<entity>/_slapcomp``: the folder holding the versions."""
    return api.storage.resolve(
        Uri.parse('export:/') / entity_uri.segments / SLAP_COMP_FOLDER
    )


def program_name(entity_uri: Uri, version_name: str) -> str:
    """``<entity>_slapcomp_<version>.bgeo.sc``."""
    return entity_stem(entity_uri, 'slapcomp', version_name) + PROGRAM_SUFFIX


def publish(
    entity_uri: Uri,
    write_program: Optional[Callable[[Path], None]],
    info: Optional[dict] = None,
    ) -> Published:
    """Write the entity's next slap comp version and return it.

    ``write_program(path)`` writes the compiled program to ``path``; None
    publishes a version without one (Use on Farm Renders off). The
    ``context.json`` goes in last, so a failed compile leaves an unfinished
    folder the farm reads past (``get_latest_version_path``).
    """
    version_path = get_next_version_path(slap_comp_dir(entity_uri))
    version_path.mkdir(parents=True, exist_ok=False)
    program_path = None
    if write_program is not None:
        program_path = version_path / program_name(entity_uri, version_path.name)
        write_program(program_path)
        if not program_path.is_file():
            raise RuntimeError(f'The slap comp program was not written: {program_path}')
    store_json(version_path / VERSION_MARKER, dict(
        info or {},
        entity=str(entity_uri),
        program=None if program_path is None else program_path.name,
    ))
    return Published(version_path, program_path)


def latest(entity_uri: Uri) -> Optional[Published]:
    """The entity's newest finished slap comp version, None when it never
    published one."""
    version_path = get_latest_version_path(slap_comp_dir(entity_uri))
    if version_path is None:
        return None
    context = load_json_if_exists(version_path / VERSION_MARKER)
    if context is None:
        # No version is finished: get_latest_version_path fell back to the
        # newest folder, a publish that failed or is still writing.
        return None
    name = context.get('program')
    program_path = None if not name else version_path / name
    return Published(version_path, program_path)


def farm_program(entity_uri: Uri) -> Optional[Published]:
    """The published slap comp a farm render of ``entity_uri`` applies: the
    newest finished version, when it holds a program. None renders without."""
    published = latest(entity_uri)
    if published is None or published.program_path is None:
        return None
    if not published.program_path.is_file():
        raise FileNotFoundError(
            f'{entity_uri} slap comp {published.version_name} names a program '
            f'that is not on disk: {published.program_path}'
        )
    return published


def bundle_path(published: Published) -> Path:
    """Where a render job bundles ``published``'s program, relative to its
    data dir."""
    return Path(BUNDLE_DIR) / published.program_path.name


def tiles_problem(entity_uri: Uri, tile_count: int) -> Optional[str]:
    """Why ``entity_uri`` cannot render in ``tile_count`` tiles, None when it can.

    husk runs a slap comp on each tile it renders, not on the stitched frame,
    so a vignette or a blur would come out once per tile.
    """
    if tile_count == 1:
        return None
    published = farm_program(entity_uri)
    if published is None:
        return None
    return (
        f'{entity_uri} has a slap comp published for farm renders '
        f'({published.version_name}), and husk would run it on each of the '
        f'{tile_count} tiles: set Tiles to 1, or publish the slap comp with '
        f'Use on Farm Renders off.'
    )


def husk_args(program_paths: Sequence[Path]) -> list[str]:
    """husk's ``--slap-comp`` flags, one per program, run in order."""
    args = []
    for program_path in program_paths:
        args += ['--slap-comp', str(Path(program_path).as_posix())]
    return args
