"""Audit: the comp template end to end, from staged renders to farm frames.

    hython scripts/verify_comp.py

The live test of designs/comp-template.md (§2.1) made repeatable. In a
throwaway project it stages synthetic renders with hoiiotool (constant
colours, light groups that add up to the beauty, a one-channel matte, ACEScg
tags), builds a comp workfile from the composite template, freezes it as
Submit does, and runs the composite worker's hython half in a subprocess on
the config the job passes. Then it checks:

- the imports resolve one department each (``best``: denoise, else render),
  and the cable carries exactly the comp's AOVs but the beauty (``rgba``);
- an import switched to another channel that resolves to the same
  department and version reads the new channel (Remy, 2026-11-10: a copied
  import kept the old channel's frames until the department was toggled);
- each light mix gets its import's ``rgba`` on its beauty input;
- Channel ``*`` reads every channel in one node: ``rgba`` is the channels
  over each other, the cable every channel's beauty and AOVs; a channel
  not rendered yet is left out and named; the freeze pins each channel, and
  the worker renders the comp from those pins;
- AOV Outputs keeps a Cable Unpack with one typed output per cable field,
  and removes it when turned off;
- th::import_scene hands COPs the staged shot's geometry and camera, with
  the output types a Rasterize Geometry takes;
- each channel's light mix at defaults equals its beauty (never beauty plus
  its groups), a muted group drops out, exposure scales by 2^e, and the alpha
  is the beauty's A;
- the merged frame is the ``over`` of the channels, last channel on top;
- the frames carry the ACEScg chromaticities, and the receipts exist;
- the version's context.json records what it read (``reads``);
- a re-comp, as Farm Submit's Comp column sends it (imports ``{}``, the
  export found by layer), writes the next version;
- the freeze pins what resolved, and a channel no import reads is reported;
- a Multi's workfile (two shots in one comp network) submits one composite
  version per shot, each with only its own imports;
- the worker's output has no "Render failed".

Shot SH010 stores its channels without ``default`` (``["chars"]``), SH020
with it (``["default", "chars"]``): Pale India has both.

Needs Houdini with the comp HDAs compiled (``hotl -l`` of
otls/cop_th.{import_render,light_mix,export_comp}.1.0, or compile-hdas) and
this checkout's python/ on PYTHONPATH. Do not set HOUDINI_USER_PREF_DIR to a
scratch dir named with __HVER__: COP cooks then fail ("invalid string
position"). Nothing outside a temp dir is written.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

TEMP_ROOT = Path(tempfile.mkdtemp(prefix='verify-comp-'))
PROJECT = TEMP_ROOT / 'proj'
shutil.copytree(REPO / 'scripts' / 'project_template' / '_config', PROJECT / '_config')
os.environ['TH_PROJECT_PATH'] = str(PROJECT)
os.environ['TH_CONFIG_PATH'] = str(PROJECT / '_config')
os.environ['TH_PIPELINE_PATH'] = str(REPO)
os.environ['TH_EVENTS_DIR'] = str(TEMP_ROOT / 'events')
os.environ.setdefault('TH_USER_NAME', 'verify')
os.environ['OCIO'] = str(PROJECT / '_config' / 'ocio' / 'tumblehead.ocio')
for name in list(sys.modules):
    if name == 'tumblepipe' or name.startswith('tumblepipe.'):
        del sys.modules[name]

import hou  # noqa: E402

from tumblepipe.api import api, path_str  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402
from tumblepipe.pipe import comp  # noqa: E402
from tumblepipe.pipe.paths import Context  # noqa: E402
from tumblepipe.pipe.houdini import entity_node  # noqa: E402
from tumblepipe.pipe.houdini.cops import export_comp, import_render, import_scene, light_mix  # noqa: E402

HFS_BIN = Path(os.environ['HFS']) / 'bin'
OIIO = HFS_BIN / ('hoiiotool.exe' if os.name == 'nt' else 'hoiiotool')
HYTHON = HFS_BIN / ('hython.exe' if os.name == 'nt' else 'hython')
FIRST, LAST = 1001, 1003
CHROMA = ['--attrib:type=float[8]', 'chromaticities',
          '0.713,0.293,0.165,0.830,0.128,0.044,0.32168,0.33767']

# Constant, premultiplied colours. chars: two light groups summing to 80% of
# the beauty, so Rest is the other 20%.
DEFAULT_BEAUTY = (0.1, 0.2, 0.5, 1.0)
CHARS_BEAUTY = (0.4, 0.2, 0.1, 0.5)
CHARS_GROUPS = {'beauty_key': 0.6, 'beauty_rim': 0.2}

_failures = []


def check(label, ok, detail=''):
    print(f"{'ok   ' if ok else 'FAIL '} {label}")
    if not ok:
        if detail: print(f'        {detail}')
        _failures.append(label)


def close(label, got, want, tolerance=2e-3):
    ok = got is not None and len(got) == len(want) and all(
        abs(g - w) < tolerance for g, w in zip(got, want)
    )
    check(label, ok, f'got {got}, want {want}')


def oiiotool(*args):
    result = subprocess.run([str(OIIO), *map(str, args)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def average(path):
    for line in oiiotool('--stats', path).splitlines():
        if 'Stats Avg' in line:
            return [float(v) for v in line.split(':', 1)[1].split('(')[0].split()]
    return None


###############################################################################
# The project and its renders
###############################################################################
def add_shot(name, channels):
    uri = Uri.parse(f'entity:/shots/SQ010/{name}')
    api.config.add_entity(uri, {
        'frame_start': FIRST, 'frame_end': LAST, 'roll_start': 0,
        'roll_end': 0, 'fps': 24, 'channels': channels,
    })
    return uri


def stage(shot_uri, department, channel, version, raw, aovs):
    """One render version: ``aovs`` maps an AOV to its constant colour."""
    root = api.storage.resolve(
        Uri.parse('render:/render') / shot_uri.segments / department / channel / version
    )
    stem = '_'.join(shot_uri.segments[1:])
    for aov, colour in aovs.items():
        names = ['R', 'G', 'B', 'A'][:len(colour)] if len(colour) > 1 else ['Z']
        names = [f'{aov}.{n}' if raw or len(colour) == 1 else n for n in names]
        (root / aov).mkdir(parents=True, exist_ok=True)
        for frame in range(FIRST, LAST + 1):
            out = root / aov / f'{stem}_{channel}_{aov}_{version}.{frame}.exr'
            oiiotool('--pattern', 'constant:color=' + ','.join(map(str, colour)),
                     '32x16', len(colour), '--chnames', ','.join(names),
                     *CHROMA, '-d', 'float', '-o', out)
    (root / 'context.json').write_text(json.dumps(dict(
        entity=str(shot_uri), department='light', channel=channel,
        render_department_name=department, version_name=version,
        first_frame=FIRST, last_frame=LAST, step_size=1,
    )))


def stage_shot(shot_uri):
    stage(shot_uri, 'render', 'default', 'v0001', True, {'beauty': DEFAULT_BEAUTY})
    stage(shot_uri, 'denoise', 'default', 'v0001', False, {'beauty': DEFAULT_BEAUTY})
    chars = {'beauty': CHARS_BEAUTY, 'objid_hero': (1.0,), 'depth': (5.0, 5.0, 5.0)}
    for group, share in CHARS_GROUPS.items():
        chars[group] = tuple(c * share for c in CHARS_BEAUTY[:3])
    chars['velocity'] = (0.0, 0.0, 0.0)   # not a comp AOV: must stay out
    stage(shot_uri, 'render', 'chars', 'v0001', True, chars)


###############################################################################
# Rendering a node's output
###############################################################################
_renders = 0


def render_average(native, output=0):
    global _renders
    _renders += 1
    copnet = native.parent()
    rop = copnet.createNode('rop_image', f'verify_rop{_renders}')
    rop.setInput(0, native, output)
    out = TEMP_ROOT / 'probe' / f'probe{_renders}.$F4.exr'
    rop.parm('trange').set('normal')
    rop.parm('f1').set(FIRST)
    rop.parm('f2').set(FIRST)
    rop.parm('mkpath').set(1)
    rop.parm('copoutput').set(path_str(out))
    rop.parm('execute').pressButton()
    rop.destroy()
    frame = Path(str(out).replace('$F4', str(FIRST)))
    return average(frame) if frame.exists() else None


###############################################################################
# The checks
###############################################################################
def load_template():
    path = REPO / 'scripts' / 'project_template' / '_config' / 'templates' / 'shots' / 'composite' / 'template.py'
    namespace = {}
    exec(compile(path.read_text(), str(path), 'exec'), namespace)
    return namespace


def use_context(shot_uri):
    """Answer 'from_context' as a composite workfile of ``shot_uri`` would."""
    entity_node.scene_context = lambda: Context(shot_uri, 'composite')


def check_single(template, shot_uri, label):
    print(f'--- {label}: {shot_uri}')
    hou.hipFile.clear(suppress_save_prompt=True)
    hou.playbar.setFrameRange(FIRST, LAST)
    hou.setFrame(FIRST)
    use_context(shot_uri)
    template['create'](hou.node('/stage'), shot_uri, 'composite')
    copnet = hou.node('/stage/comp')
    check(f'{label}: the template makes /stage/comp', copnet is not None)
    imports = {node.get_channel_name(): node for node in import_render.find_imports()}
    if os.environ.get('VERIFY_DEBUG'):
        import traceback
        try: imports['chars'].execute()
        except Exception: traceback.print_exc()
    check(f'{label}: one import per channel, in channel order',
          [n.get_channel_name() for n in import_render.find_imports()] == ['default', 'chars'],
          str(list(imports)))
    check(f'{label}: imports stay on from_context',
          all(n.parm('entity').eval() == 'from_context' for n in imports.values()))
    check(f'{label}: default resolves to denoise v0001',
          imports['default'].get_resolved() == ('denoise', 'v0001'),
          str(imports['default'].get_resolved()))
    check(f'{label}: chars resolves to render v0001 (no denoise)',
          imports['chars'].get_resolved() == ('render', 'v0001'),
          imports['chars'].parm('version_label').eval())
    check(f'{label}: the chars cable carries exactly the comp AOVs, no beauty',
          imports['chars'].get_fields() == ['beauty_key', 'beauty_rim', 'depth', 'objid_hero'],
          str(imports['chars'].get_fields()))
    close(f'{label}: import rgba is the beauty', render_average(imports['chars'].native(), 0), CHARS_BEAUTY)

    mixes = {m.native().name(): m for m in light_mix.find_mixes()}
    chars_mix = mixes['light_mix_chars']
    check(f'{label}: Sync Rows gave chars a row per light group',
          chars_mix.rows() == ['beauty_key', 'beauty_rim'], str(chars_mix.rows()))
    check(f'{label}: default has no rows', mixes['light_mix_default'].rows() == [])
    for channel in ('default', 'chars'):
        beauty_input = mixes[f'light_mix_{channel}'].native().input(light_mix.BEAUTY_INPUT)
        check(f'{label}: the {channel} mix takes its import rgba on its beauty input',
              beauty_input is not None and beauty_input.path() == imports[channel].path(),
              str(beauty_input and beauty_input.path()))
    close(f'{label}: chars mix at defaults equals its beauty',
          render_average(chars_mix.native()), CHARS_BEAUTY)
    close(f'{label}: default mix (no groups) equals its beauty',
          render_average(mixes['light_mix_default'].native()), DEFAULT_BEAUTY)
    native = chars_mix.native()
    native.parm('mute1').set(1)
    want = tuple(c * (1 - CHARS_GROUPS['beauty_key']) for c in CHARS_BEAUTY[:3]) + (CHARS_BEAUTY[3],)
    close(f'{label}: a muted group drops out exactly (alpha kept)', render_average(native), want)
    native.parm('mute1').set(0)
    native.parm('exposure2').set(1.0)
    want = tuple(c * (1 + CHARS_GROUPS['beauty_rim']) for c in CHARS_BEAUTY[:3]) + (CHARS_BEAUTY[3],)
    close(f'{label}: exposure 1 doubles its group', render_average(native), want)
    native.parm('exposure2').set(0.0)
    chars_mix.sync_rows()
    check(f'{label}: syncing again changes no rows', chars_mix.rows() == ['beauty_key', 'beauty_rim'])

    export = export_comp.find_exports()[0]
    check(f'{label}: export_comp has no warning while every channel is read',
          export.refresh_warning() == '', export.parm('warning').eval())

    # A channel added to the shot later
    own = api.config.get_own_properties(shot_uri) or {}
    channels = list(own.get('channels') or [])
    api.config.set_own_properties(shot_uri, dict(own, channels=channels + ['fx']))
    check(f'{label}: a channel added later warns on export_comp',
          export.refresh_warning() == comp.unread_warning(['fx']), export.parm('warning').eval())
    submits = export_comp.plan_submits(export)
    check(f'{label}: Submit lists the unread channel', submits[0].unread == ['fx'], str(submits[0].unread))
    api.config.set_own_properties(shot_uri, dict(own, channels=channels))
    export.refresh_warning()

    submits = export_comp.plan_submits(export)
    check(f'{label}: one composite version for one shot', len(submits) == 1)
    plan = submits[0].plan
    check(f'{label}: the freeze pins each import to what it resolved', plan == {
        imports['default'].path(): dict(channel='default', department='denoise', version='v0001'),
        imports['chars'].path(): dict(channel='chars', department='render', version='v0001'),
    }, json.dumps(plan))
    check(f'{label}: the freeze leaves the workfile on latest',
          all(n.get_version_setting() == 'latest' for n in imports.values()))
    return export, submits[0]


def check_channel_switch(shot_uri, label):
    """A copy of one channel's import switched to another channel that
    resolves to the same department and version rebuilds its File COPs."""
    print(f'--- {label}: switching a copied import to another channel')
    copnet = hou.node('/stage/comp')
    source = import_render.ImportRender(copnet.node('import_default'))
    source.parm('department').set('render')
    source.execute()
    check(f'{label}: default on render resolves render v0001',
          source.get_resolved() == ('render', 'v0001'), str(source.get_resolved()))
    copy = import_render.ImportRender(hou.copyNodesTo([source.native()], copnet)[0])
    copy.set_channel_name('chars')
    copy.execute()
    check(f'{label}: the copy switched to chars reads chars (same render v0001)',
          copy.get_fields() == ['beauty_key', 'beauty_rim', 'depth', 'objid_hero'],
          str(copy.get_fields()))
    close(f'{label}: the copy outputs the chars beauty', render_average(copy.native(), 0), CHARS_BEAUTY)
    copy.native().destroy()
    source.parm('department').set(comp.BEST)
    source.execute()


def check_all_channels(shot_uri, label):
    """Channel ``*``: every channel in one node; returns its export and the
    submit, for the worker. The comp is the all-channels import's rgba into
    its own export_comp, in a fresh workfile."""
    print(f'--- {label}: one import for all channels')
    hou.hipFile.clear(suppress_save_prompt=True)
    hou.playbar.setFrameRange(FIRST, LAST)
    hou.setFrame(FIRST)
    use_context(shot_uri)
    copnet = hou.node('/stage').createNode('copnet', 'comp')
    node = import_render.create(copnet, 'import_all')
    node.set_channel_name(comp.ALL_CHANNELS)
    node.execute()
    check(f'{label}: the Channel menu offers All channels',
          node.channel_menu()[-2:] == [comp.ALL_CHANNELS, import_render.ALL_CHANNELS_LABEL],
          str(node.channel_menu()))
    check(f'{label}: all channels read default and chars',
          node.read_channels() == ['default', 'chars'], str(node.read_channels()))
    want_fields = ['default', 'chars', 'chars_beauty_key', 'chars_beauty_rim',
                   'chars_depth', 'chars_objid_hero']
    check(f'{label}: the cable carries each channel beauty and AOVs',
          node.get_fields() == want_fields, str(node.get_fields()))
    a = CHARS_BEAUTY[3]
    over = tuple(f + b * (1 - a) for f, b in zip(CHARS_BEAUTY, DEFAULT_BEAUTY))
    close(f'{label}: all-channels rgba is chars over default', render_average(node.native(), 0), over)

    mix = light_mix.create(copnet, 'mix_all')
    mix.native().setInput(0, node.native(), import_render.AOVS_OUTPUT)
    try:
        mix.sync_rows()
        refused = False
    except Exception as error:
        refused = 'reads all channels' in str(error)
    check(f'{label}: a light mix on an all-channels import is refused with a reason', refused)
    mix.native().destroy()

    # AOV Outputs
    node.parm(import_render.UNPACK_PARM).set(1)
    node.execute()  # as the AOV Outputs callback does
    unpack = node.find_unpack()
    check(f'{label}: AOV Outputs adds a Cable Unpack beside the node', unpack is not None)
    if unpack is not None:
        check(f'{label}: the unpack has one output per cable field',
              list(unpack.outputLabels()) == want_fields, str(unpack.outputLabels()))
        check(f'{label}: the unpack outputs are typed (beauty RGBA, depth RGB, matte Mono)',
              list(unpack.outputDataTypes()) == ['RGBA', 'RGBA', 'RGB', 'RGB', 'RGB', 'Mono'],
              str(unpack.outputDataTypes()))
        close(f'{label}: the unpack chars output is the chars beauty',
              render_average(unpack, 1), CHARS_BEAUTY)
    node.parm(import_render.UNPACK_PARM).set(0)
    node.execute()  # as the AOV Outputs callback does
    check(f'{label}: AOV Outputs off removes the unread unpack', node.find_unpack() is None)

    export = export_comp.create(copnet, 'export_comp')
    export.native().setInput(0, node.native(), import_render.RGBA_OUTPUT)
    check(f'{label}: no unread warning while all channels are read',
          export.refresh_warning() == '', export.parm('warning').eval())

    # A channel not rendered yet is left out and named
    own = api.config.get_own_properties(shot_uri) or {}
    channels = list(own.get('channels') or [])
    api.config.set_own_properties(shot_uri, dict(own, channels=channels + ['fx']))
    node.execute()
    check(f'{label}: an unrendered channel is left out',
          node.read_channels() == ['default', 'chars'], str(node.read_channels()))
    check(f'{label}: the Reads label names it',
          'not rendered: fx' in node.parm('version_label').eval(), node.parm('version_label').eval())
    check(f'{label}: export_comp warns it is unread',
          export.refresh_warning() == comp.unread_warning(['fx']), export.parm('warning').eval())
    api.config.set_own_properties(shot_uri, dict(own, channels=channels))
    node.execute()
    export.refresh_warning()

    submits = export_comp.plan_submits(export)
    plan = submits[0].plan
    check(f'{label}: the freeze pins each channel of the all-channels import', plan == {
        comp.plan_key(node.path(), 'default', True): dict(channel='default', department='denoise', version='v0001'),
        comp.plan_key(node.path(), 'chars', True): dict(channel='chars', department='render', version='v0001'),
    }, json.dumps(plan))
    return export, submits[0], over


def check_scene(shot_uri):
    """th::import_scene on a staged shot: a cube and the render camera."""
    print('--- th::import_scene: the staged shot as geometry and camera')
    from pxr import Usd, UsdGeom
    from tumblepipe.pipe.paths.staged import get_staged_file_path
    staged = get_staged_file_path(shot_uri, 'v0001')
    staged.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(staged))
    UsdGeom.Xform.Define(stage, '/props')
    UsdGeom.Cube.Define(stage, '/props/box').CreateSizeAttr(2.0)
    camera = UsdGeom.Camera.Define(stage, '/cameras/render_camera')
    UsdGeom.Xformable(camera).AddTranslateOp().Set((0.0, 0.0, 10.0))
    stage.GetRootLayer().Save()
    (staged.parent / 'context.json').write_text(json.dumps(dict(
        entity=str(shot_uri), version_name='v0001', channel='default',
    )))
    hou.hipFile.clear(suppress_save_prompt=True)
    use_context(shot_uri)
    copnet = hou.node('/stage').createNode('copnet', 'comp')
    node = import_scene.create(copnet, 'import_scene')
    native = node.native()
    check('import_scene: outputs geometry and camera',
          list(native.outputLabels()) == ['geometry', 'camera'], str(native.outputLabels()))
    check('import_scene: typed Geometry and Metadata (Rasterize Geometry inputs)',
          list(native.outputDataTypes()) == ['Geometry', 'Metadata'], str(native.outputDataTypes()))
    try:
        node.execute()
        executed = True
    except Exception as error:
        executed = False
        print(f'        {error}')
    check('import_scene: Import runs the embedded import_shot', executed)
    inner = native.node(import_scene.IMPORT_NODE)
    check('import_scene: the embedded import_shot loads payloads',
          inner is not None and inner.parm('loadpayloads').eval() == 1)
    # A real staged file holds its departments as entity:/ sublayers, which
    # need the pipeline's USD resolver; this one holds its prims inline, so
    # the embedded Sublayer LOP is pointed at it. import_shot's own
    # resolution is covered by its tests; this checks everything after it.
    sublayer = inner.node('import')
    sublayer.parm('num_files').set(1)
    sublayer.parm('filepath1').set(path_str(staged))
    geometry = native.node('geometry')
    sop = geometry.node('output') if geometry is not None else None
    sop_geo = sop.geometry() if sop is not None else None
    check('import_scene: the box arrives as polygons',
          sop_geo is not None and len(sop_geo.prims()) == 6
          and all(p.type() == hou.primType.Polygon for p in sop_geo.prims()),
          str(sop_geo and [p.type() for p in sop_geo.prims()][:8]))
    lopcam = native.node('objnet/camera')
    check('import_scene: the camera is the shot render camera',
          lopcam is not None and abs(lopcam.worldTransform().extractTranslates()[2] - 10.0) < 1e-4,
          str(lopcam and lopcam.worldTransform().extractTranslates()))
    raster = copnet.createNode('rasterizegeo', 'raster')
    raster.setInput(0, native, 1)
    raster.setInput(1, native, 0)
    try:
        raster.cook(force=True)
        errors = raster.errors()
    except hou.OperationFailed as error:
        errors = [str(error)]
    check('import_scene: Rasterize Geometry cooks on its two outputs', not errors, str(errors))


def run_worker(shot_uri, submit, label, recomp=False, want_merged=None):
    """The hython half on the config the job would pass. A re-comp (Farm
    Submit's Comp column) freezes nothing and names only the comp layer."""
    hip = TEMP_ROOT / f'{label}.hip'
    hou.hipFile.save(path_str(hip))
    wanted = [dict(node_path='', layer=comp.MERGED_LAYER)] if recomp else submit.exports
    version = comp.next_composite_version(shot_uri, [e['layer'] for e in wanted])
    exports = [
        dict(node_path=e['node_path'], layer=e['layer'],
             output_path=path_str(comp.layer_output_path(shot_uri, e['layer'], version)))
        for e in wanted
    ]
    receipt = api.storage.resolve(
        Uri.parse('render:/render') / shot_uri.segments / 'composite' / comp.MERGED_FOLDER / version
    ) / 'receipt.####.json'
    config = dict(
        entity=dict(uri=str(shot_uri), department='composite'),
        first_frame=FIRST, last_frame=LAST, frames=list(range(FIRST, LAST + 1)),
        receipt_path=path_str(receipt), input_path=path_str(hip),
        exports=exports, imports={} if recomp else submit.plan,
    )
    config_path = TEMP_ROOT / f'{label}.json'
    config_path.write_text(json.dumps(config))
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join([str(REPO / 'python'), env.get('PYTHONPATH', '')])
    # An installed tumblepipe package would set TH_* to its own project at
    # startup, over these; the worker needs no package but the HDAs.
    env['HOUDINI_PACKAGE_SKIP'] = '1'
    env['HOUDINI_OTLSCAN_PATH'] = os.pathsep.join([str(REPO / 'otls'), '&'])
    result = subprocess.run(
        [str(HYTHON), str(REPO / 'python' / 'tumblepipe' / 'farm' / 'workers' / 'composite_houdini.py'),
         str(config_path)],
        capture_output=True, text=True, env=env, timeout=600,
    )
    output = result.stdout + result.stderr
    check(f'{label}: the worker exits 0', result.returncode == 0, output[-2000:])
    check(f'{label}: the worker prints no "Render failed"', 'Render failed' not in output,
          output[-2000:])
    frame = Path(exports[0]['output_path'].replace('####', str(FIRST)))
    check(f'{label}: the merged comp is written to composite/slapcomp/{version}',
          frame.exists() and comp.MERGED_FOLDER in frame.parts, str(frame))
    if frame.exists():
        # chars over default
        a = CHARS_BEAUTY[3]
        want = want_merged or tuple(f + b * (1 - a) for f, b in zip(CHARS_BEAUTY, DEFAULT_BEAUTY))
        close(f'{label}: the merged frame is chars over default', average(frame), want)
        info = oiiotool('--info', '-v', frame)
        check(f'{label}: the merged frame carries ACEScg chromaticities',
              'chromaticities' in info and '0.713' in info, info[-600:])
    check(f'{label}: every receipt is written', all(
        Path(str(receipt).replace('####', str(f))).exists() for f in range(FIRST, LAST + 1)
    ))
    context_path = frame.parent / 'context.json'
    reads = json.loads(context_path.read_text()).get('reads') if context_path.exists() else None
    check(f'{label}: the version records what it read',
          reads == comp.provenance(submit.plan), json.dumps(reads))
    return version


def check_multi(template, shots):
    print('--- Multi: two shots in one comp network')
    hou.hipFile.clear(suppress_save_prompt=True)
    entity_node.scene_context = lambda: None
    copnet = hou.node('/stage').createNode('copnet', 'comp')
    x = 0.0
    for shot_uri in shots:
        member = '_'.join(shot_uri.segments[1:])
        x += template['_build'](copnet, shot_uri, suffix=f'_{member}', x=x, pin=True) + comp.MEMBER_GAP
    check('Multi: no import left on from_context',
          all(n.parm('entity').eval() != 'from_context' for n in import_render.find_imports()))
    exports = export_comp.find_exports()
    submits = export_comp.plan_submits(exports[0])
    check('Multi: one composite version per member', sorted(str(s.shot_uri) for s in submits)
          == sorted(map(str, shots)), str([str(s.shot_uri) for s in submits]))
    for submit in submits:
        member = '_'.join(submit.shot_uri.segments[1:])
        check(f'Multi: {member} freezes only its own imports',
              len(submit.plan) == 2 and all(path.endswith(member) for path in submit.plan),
              json.dumps(submit.plan))
        config = export_comp.job_config(submit, 'composite', 'workfile.hip')
        check(f'Multi: {member} job is for its own shot', config['entity']['uri'] == str(submit.shot_uri))


def main():
    sh010 = add_shot('SH010', ['chars'])
    sh020 = add_shot('SH020', ['default', 'chars'])
    for shot_uri in (sh010, sh020):
        stage_shot(shot_uri)
    template = load_template()
    for shot_uri, label in ((sh010, 'SH010 ["chars"]'), (sh020, 'SH020 ["default","chars"]')):
        _, submit = check_single(template, shot_uri, label)
        check_channel_switch(shot_uri, label)
        version = run_worker(shot_uri, submit, label.split()[0])
        check(f'{label}: first composite version is v0001', version == 'v0001', version)
        version = run_worker(shot_uri, submit, label.split()[0] + '_recomp', recomp=True)
        check(f'{label}: a re-comp writes the next version', version == 'v0002', version)
    check_multi(template, [sh010, sh020])
    sh030 = add_shot('SH030', ['default', 'chars'])
    stage_shot(sh030)
    _, submit, over = check_all_channels(sh030, 'SH030 all channels')
    version = run_worker(sh030, submit, 'SH030_all', want_merged=over)
    check('SH030 all channels: the worker comps from the per-channel pins', version == 'v0001', version)
    check_scene(sh030)
    print()
    print(f'{len(_failures)} failure(s)' if _failures else 'all checks passed')
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    return 1 if _failures else 0


if __name__ == '__main__':
    code = main()
    hou.exit(code)
