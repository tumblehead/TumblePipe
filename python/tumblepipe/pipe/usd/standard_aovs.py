"""The standard AOVs, as data: what each ``th::render_vars`` toggle authors.

``th::render_vars`` is where a shot's AOVs are ticked; each toggle is a
Render Var LOP inside the HDA. A project can also name some of them for
every shot (``render.aovs``, ``config.render_config``), which the shot's
root layer authors with no node to cook (``pipe.usd.render_overrides``).
Both must make the same RenderVar, so this table mirrors the HDA: one
:class:`Aov` per toggle, by AOV name, in the order the node lists them.

The HDA stays the definition. ``tests/test_project_aovs.py`` reads its
Render Var LOPs (every property they set, and each Compression menu's
default) and holds this table equal to them, and
``scripts/verify_project_render_vars.py`` cooks the node under hython and
compares the prims. A toggle added to the node without a row here fails
the first.

Pure: no ``pxr``, no ``hou``.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Karma's pixel filters, as the Render Var LOP writes them.
BOX_FILTER = '["ubox",{}]'
MSE_FILTER = '["stats",{"mode":"mse"}]'
ZMIN_FILTER = '["minmax",{"mode":"zmin"}]'

#: The schemas the Render Var LOP applies: Karma's only to a var that sets
#: one of Karma's properties (measured on the cooked node, hython 22.0.429).
KARMA_SCHEMA = 'KarmaRenderVarAPI'
HUSK_SCHEMA = 'HuskRenderVarAPI'
#: The compressions with a DWA level: the LOP authors the level for no other.
DWA_COMPRESSIONS = ('dwaa', 'dwab')

_AOV = 'driver:parameters:aov:'

#: How each property a var sets is declared in USDA, in the order a var
#: lists them.
PROPERTY_TYPES = {
    'dataType': 'uniform token',
    f'{_AOV}clearValue': 'custom int',
    f'{_AOV}format': 'custom token',
    f'{_AOV}husk:channel_lower_rgb': 'bool',
    f'{_AOV}husk:channel_prefix': 'string',
    f'{_AOV}husk:OpenEXR:compression': 'string',
    f'{_AOV}husk:OpenEXR:dwa_compression': 'int',
    f'{_AOV}karma:cryptomatte': 'bool',
    f'{_AOV}karma:cryptomatterank': 'int',
    f'{_AOV}karma:defaultvalue': 'float',
    f'{_AOV}karma:dither': 'float',
    f'{_AOV}karma:filter': 'string',
    f'{_AOV}multiSampled': 'custom bool',
    f'{_AOV}name': 'custom string',
    'sourceName': 'uniform string',
    'sourceType': 'uniform token',
}


@dataclass(frozen=True)
class Aov:
    """One standard AOV, as its ``th::render_vars`` toggle authors it.

    ``name`` is the RenderVar's prim name and its plane
    (``driver:parameters:aov:name``), the name the farm splits by.
    ``label`` is the toggle's label on the node. ``compression`` is the
    default of the toggle's Compression menu (``pipe.aovs``: lossless for
    data, DWAB for colour). ``karma`` False is a var that sets none of
    Karma's dither, default value and cryptomatte, nor a DWA level (which
    the others set only under a DWA compression)."""
    name: str
    label: str
    data_type: str
    source_name: str
    source_type: str
    format: str
    multi_sampled: bool
    compression: str
    lower_rgb: bool = True
    filter: str | None = BOX_FILTER
    karma: bool = True
    cryptomatte: bool = False
    channel_prefix: str | None = None

    def properties(self) -> dict[str, object]:
        """Every property the var sets, by USD name."""
        found: dict[str, object] = {
            'dataType': self.data_type,
            'sourceName': self.source_name,
            'sourceType': self.source_type,
            f'{_AOV}name': self.name,
            f'{_AOV}format': self.format,
            f'{_AOV}multiSampled': self.multi_sampled,
            f'{_AOV}clearValue': 0,
            f'{_AOV}husk:OpenEXR:compression': self.compression,
        }
        if self.filter is not None:
            found[f'{_AOV}karma:filter'] = self.filter
        if self.karma:
            found[f'{_AOV}karma:dither'] = 1.0
            found[f'{_AOV}karma:defaultvalue'] = 0.0
            found[f'{_AOV}karma:cryptomatte'] = self.cryptomatte
            if self.compression in DWA_COMPRESSIONS:
                found[f'{_AOV}husk:OpenEXR:dwa_compression'] = 45
        if self.cryptomatte:
            found[f'{_AOV}karma:cryptomatterank'] = 6
        if self.channel_prefix is not None:
            found[f'{_AOV}husk:channel_prefix'] = self.channel_prefix
        if self.lower_rgb:
            found[f'{_AOV}husk:channel_lower_rgb'] = True
        return {name: found[name] for name in PROPERTY_TYPES if name in found}

    def api_schemas(self) -> tuple[str, ...]:
        """The schemas the var's prim applies."""
        if self.karma or self.filter is not None:
            return (KARMA_SCHEMA, HUSK_SCHEMA)
        return (HUSK_SCHEMA,)


def _lpe(name: str, source: str, label: str | None = None, fmt: str = 'color3h',
         prefix: str | None = None) -> Aov:
    """A colour split of the beauty: an LPE, multisampled, DWAB."""
    return Aov(name, label or name, 'color3f', source, 'lpe', fmt, True, 'dwab',
               channel_prefix=prefix)


_AOVS = (
    Aov('beauty', 'beauty', 'color4f', 'C.*[LO]', 'lpe', 'color4f', True, 'dwab'),
    Aov('beauty_mse', 'beauty variance', 'color3f', 'C.*[LO]', 'lpe', 'color3f', False, 'dwab',
        filter=MSE_FILTER, karma=False),
    Aov('samples', 'samples', 'color3f', 'ray:pixelsamples', 'raw', 'float', False, 'zip',
        lower_rgb=False, filter=None, karma=False),
    Aov('albedo', 'albedo', 'color3f', 'export_basecolor', 'raw', 'color3h', False, 'dwab'),
    Aov('albedo_mse', 'albedo variance', 'color3f', 'export_basecolor', 'raw', 'color3f', False,
        'zip', lower_rgb=False, filter=None, karma=False),
    Aov('normal', 'normal', 'normal3f', 'ray:hitN', 'raw', 'half3', False, 'zip'),
    Aov('normal_mse', 'normal variance', 'normal3f', 'ray:hitN', 'raw', 'float3', False, 'zip',
        lower_rgb=False, filter=None, karma=False),
    Aov('uv', 'uv', 'float3', 'st', 'primvar', 'half3', False, 'zip', lower_rgb=False),
    Aov('position', 'position', 'point3f', 'ray:hitP', 'raw', 'half3', False, 'zip',
        lower_rgb=False),
    Aov('depth', 'depth', 'float', 'ray:hitPz', 'raw', 'half', False, 'zip', lower_rgb=False,
        filter=ZMIN_FILTER),
    _lpe('diffuse', 'C<RD>.*L'),
    _lpe('specular', 'C<RG>.*L', fmt='color3f'),
    _lpe('emission', 'C.*O', prefix='emission'),
    _lpe('volume', 'CV.*L', fmt='color3f', prefix='volume'),
    _lpe('diffuse_direct', 'C<RD>L', 'diffuse direct'),
    _lpe('diffuse_indirect', 'C<RD>.+L', 'diffuse indirect'),
    _lpe('specular_direct', 'C<RG>L', 'specular direct'),
    _lpe('specular_indirect', 'C<RG>.+L', 'specular indirect'),
    _lpe('transmission', 'C<TG>.*L'),
    _lpe('sss', 'C<TD>.*L'),
    _lpe('coat', "C<...'coat'>.*L"),
    _lpe('unshadowed', 'unoccluded;C.*[LO]'),
    _lpe('shadow', 'shadow;C.*[LO]'),
    Aov('ao', 'ao', 'color3f', 'AO', 'raw', 'color3h', True, 'zip'),
    Aov('motionvectors', 'motion vectors', 'vector3f', 'ray:motionvector', 'raw', 'half3', True,
        'zip'),
    Aov('velocity', 'velocity', 'vector3f', 'ray:velocity', 'raw', 'half3', True, 'zip'),
    Aov('cryptoprimitives', 'cryptoprimitives', 'float3', '__name', 'primvar', 'float3', False,
        'zip', lower_rgb=False, cryptomatte=True),
    Aov('cryptomaterials', 'cryptomaterials', 'float3', '__materialname', 'primvar', 'float3',
        False, 'zip', lower_rgb=False, cryptomatte=True),
)

#: The standard AOVs by name, in the order ``th::render_vars`` lists them.
AOVS: dict[str, Aov] = {aov.name: aov for aov in _AOVS}


def _string(text: str) -> str:
    """``text`` as a USDA string: double quoted unless it holds one."""
    if '"' not in text and '\\' not in text:
        return f'"{text}"'
    if "'" not in text and '\\' not in text:
        return f"'{text}'"
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _value(value) -> str:
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, int):
        return str(value)
    return _string(value)


def property_lines(aov: Aov) -> list[str]:
    """The USDA property lines of ``aov``'s RenderVar prim."""
    return [
        f'{PROPERTY_TYPES[name]} {name} = {_value(value)}'
        for name, value in aov.properties().items()
    ]


def metadata_line(aov: Aov) -> str:
    """The RenderVar prim's metadata: the schemas the Render Var LOP applies."""
    return 'prepend apiSchemas = [' + ', '.join(f'"{name}"' for name in aov.api_schemas()) + ']'
