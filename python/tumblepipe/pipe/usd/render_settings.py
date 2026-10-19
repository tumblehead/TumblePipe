"""Which RenderSettings prim and camera a stage renders through, and the
USDA sections the farm collapse authors against them.

Every lookup here asks the composed stage rather than assuming a path: the
project decides where its RenderSettings lives, and an answer assumed instead
of read is how overrides and playblasts landed on prims that do not exist.
Zero or several candidates is a raise, never a guess.
"""


class RenderSettingsError(RuntimeError):
    """The stage's one UsdRender.Settings prim could not be identified."""


def find_render_settings_prim_path(stage) -> str:
    """The prim path of *stage*'s one UsdRender.Settings prim.

    husk decides what to render by looking at the stage, so this asks the
    stage the same question instead of assuming an answer. Assuming one is
    what broke: the pipeline hardcoded ``/Render/rendersettings`` while 6 of
    13 live projects declare
    ``renderSettingsPrimPath = "/scene/Render/rendersettings"`` in their
    ``root_default_prims.usda``, so on those projects every submit-dialog
    render override composed onto a prim that does not exist and was
    discarded without a word.

    Reading ``renderSettingsPrimPath`` back off the composed stack would be
    wrong in a quieter way, which is why this does not: Houdini's USD export
    stamps that metadatum into department export layers (45 of 66 live layers
    carry one), and those layers are *stronger* than root_default_prims.usda —
    so the composed value is whatever the topmost exported department happened
    to bake in, not what the project declares. A project declaration, if one
    is ever needed to break a tie, has to be read from the project config
    directly. None is needed here, because a tie is a raise.

    Zero or many is always a raise, never a guess: with no Settings prim there
    is nothing to override, and with several there is no way to know which one
    husk will land on.

    The traversal uses ``Usd.PrimRange.Stage(stage, PrimAllPrimsPredicate)``
    rather than ``stage.Traverse()``: the default predicate skips *inactive*
    prims, and a deactivated RenderSettings that husk would still be handed
    must show up here as something to reconcile, not vanish into a "no
    settings" answer.

    The predicate does NOT substitute for loading, which is a separate axis:
    an unloaded payload's contents are not composed onto the stage at all, so
    no predicate can reach them. Callers that open the stage themselves must
    open it ``LoadAll``. When nothing is found and the stage does have
    unloaded payloads, the error says so — "there is no settings prim" and "I
    could not see one" are different problems with different fixes.
    """
    from pxr import Usd, UsdRender

    found = [
        prim.GetPath().pathString
        for prim in Usd.PrimRange.Stage(stage, Usd.PrimAllPrimsPredicate)
        if prim.IsA(UsdRender.Settings)
    ]
    if len(found) == 1:
        return found[0]
    if not found:
        unloaded = sorted(
            path.pathString for path in set(stage.FindLoadable())
            - set(stage.GetLoadSet())
        )
        detail = (
            ' The stage has unloaded payloads, which no traversal can see '
            f'into, so the settings may be inside one: {", ".join(unloaded)}. '
            'Open the stage LoadAll.'
            if unloaded else
            " Check that the project's config:/usd/root_default_prims.usda "
            'is sublayered into the build.'
        )
        raise RenderSettingsError(
            'the stage carries no UsdRender.Settings prim, so nothing on it '
            'says what to render -- and a render override has nothing to '
            'apply to.' + detail
        )
    raise RenderSettingsError(
        'the stage carries more than one UsdRender.Settings prim, so which '
        'one husk renders is not knowable from here: '
        f'{", ".join(sorted(found))}. Leave exactly one on the stage.'
    )


def find_render_camera_prim_path(stage, settings_prim_path: str) -> str:
    """The camera prim *stage* renders through, per its RenderSettings.

    The ``camera`` relationship is the only authority: a shot stage carries
    the project template's placeholder camera as well as whatever the layout
    department exported, and "the first camera found" picks between them by
    traversal order — which is how a playblast ends up shot from the origin
    with a 0.5mm lens.

    Raises RenderSettingsError when the relationship is missing, has no
    target, or targets a prim that is not on the stage: a playblast rendered
    through a camera nobody chose is the silent-wrong-result this prevents.
    """
    from pxr import Usd, UsdGeom

    settings_prim = stage.GetPrimAtPath(settings_prim_path)
    if not settings_prim or not settings_prim.IsValid():
        raise RenderSettingsError(
            f'no prim at the render settings path {settings_prim_path}'
        )
    relationship = settings_prim.GetRelationship('camera')
    targets = list(relationship.GetTargets()) if relationship else []
    if not targets:
        raise RenderSettingsError(
            f"{settings_prim_path} has no 'camera' relationship target, so "
            'the stage does not say which camera to render through.'
        )
    camera_path = targets[0].pathString
    camera_prim = stage.GetPrimAtPath(camera_path)
    if not camera_prim or not camera_prim.IsValid():
        cameras = sorted(
            prim.GetPath().pathString
            for prim in Usd.PrimRange.Stage(stage, Usd.PrimAllPrimsPredicate)
            if prim.IsA(UsdGeom.Camera)
        )
        detail = (
            f' Cameras on the stage: {", ".join(cameras)}.'
            if cameras else ' The stage carries no camera at all.'
        )
        raise RenderSettingsError(
            f'{settings_prim_path} renders through {camera_path}, which is '
            'not on the stage.' + detail
        )
    return camera_path


# Where a collapsed playblast stage carries the RenderSettings husk renders
# it through. Its own prim, not the project's: see
# generate_playblast_settings_section.
PLAYBLAST_SETTINGS_PRIM_PATH = '/Render/tumblepipe_playblast'
_PLAYBLAST_SCOPE_NAME = 'TumblePipePlayblast'


def generate_playblast_settings_section(camera_prim_path: str) -> str:
    """USDA text defining the RenderSettings a farm playblast renders through.

    A playblast does not reuse the project's RenderSettings, whose
    RenderProduct orders Karma's LPE RenderVars (``beauty`` with
    ``sourceName = "C.*[LO]"``, ``sourceType = "lpe"``) for a lit final
    render. The playblast is a preview JPEG, one colour buffer. It was also
    load-bearing while playblasts rendered with Hydra Storm (since removed),
    which cannot produce LPE buffers at all, so husk logged

        Render delegate doesn't provide buffer for: beauty
        All AOVs bypassed or missing. Nothing to write to <path>

    and writes no image at all — which reached the worker as "produced no
    frames", where it was reported as a missing GPU on the render node.
    Verified against husk 22.0.429.

    So the playblast gets its own settings prim: one plain ``color`` RenderVar
    every renderer provides, and the camera the project's own settings name (passed
    in, never guessed — see find_render_camera_prim_path). Resolution is left
    to the worker's ``--res``. ``driver:parameters:aov:name`` is not optional:
    without it husk refuses the RenderVar outright ("Missing
    driver:parameters:aov:husk:name token").
    """
    scope_path = f'/Render/{_PLAYBLAST_SCOPE_NAME}'
    settings_name = PLAYBLAST_SETTINGS_PRIM_PATH.rsplit('/', 1)[-1]
    return '\n'.join([
        '',
        '# Playblast render settings, authored by the submitter: one raw',
        "# colour var instead of the project's Karma LPE AOVs.",
        'def Scope "Render"',
        '{',
        f'    def Scope "{_PLAYBLAST_SCOPE_NAME}"',
        '    {',
        '        def RenderVar "color"',
        '        {',
        '            uniform token dataType = "color4f"',
        '            uniform string sourceName = "color"',
        '            uniform token sourceType = "raw"',
        '            custom string driver:parameters:aov:name = "color"',
        '        }',
        '',
        '        def RenderProduct "product"',
        '        {',
        f'            rel orderedVars = [ <{scope_path}/color> ]',
        '            token productName = "playblast"',
        '            uniform token productType = "raster"',
        '        }',
        '    }',
        '',
        f'    def RenderSettings "{settings_name}"',
        '    {',
        f'        rel camera = <{camera_prim_path}>',
        f'        rel products = <{scope_path}/product>',
        '        uniform token[] includedPurposes = ["default", "render"]',
        '        uniform token aspectRatioConformPolicy = "expandAperture"',
        '    }',
        '}',
        '',
    ])


def generate_render_overrides_section(
    overrides: dict,
    settings_prim_path: str,
) -> str:
    """
    Generate USDA text for render setting overrides.

    Args:
        overrides: Dict mapping Karma attribute names to values
                   e.g. {'karma:global:pathtracedsamples': 128}
        settings_prim_path: Absolute prim path of the stage's RenderSettings,
                   from find_render_settings_prim_path. One nested `over` per
                   path component — an `over` chain that does not match the
                   real prim composes onto nothing and drops every override.

    Returns:
        USDA text defining render settings overrides, or empty string if no overrides
    """
    if not overrides:
        return ''

    names = [name for name in settings_prim_path.split('/') if name]
    if not names:
        raise RenderSettingsError(
            f'render settings prim path is not a usable prim path: '
            f'{settings_prim_path!r}'
        )

    lines = ['']
    for depth, name in enumerate(names):
        indent = '    ' * depth
        lines.append(f'{indent}over "{name}"')
        lines.append(indent + '{')

    body_indent = '    ' * len(names)
    for attr_name, value in overrides.items():
        if isinstance(value, bool):
            val_str = 'true' if value else 'false'
            lines.append(f'{body_indent}bool {attr_name} = {val_str}')
        elif isinstance(value, int):
            lines.append(f'{body_indent}int {attr_name} = {value}')
        elif isinstance(value, float):
            lines.append(f'{body_indent}float {attr_name} = {value}')

    for depth in reversed(range(len(names))):
        lines.append('    ' * depth + '}')

    return '\n'.join(lines)
