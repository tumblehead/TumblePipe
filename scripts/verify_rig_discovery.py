#!/usr/bin/env hython
"""Verify the animatable prim tag and Import Rigs' Discover.

Asset prims carry ``animatable`` in their pipeline customData
(prim_metadata.ANIMATABLE_KEY): the asset's Animatable box, or a rig
published in any channel. Import Rigs' Discover reads it off a LOP stage
and gives a row per animatable asset that has a rig. ``tests/`` pins the
rule without Houdini; this runs the real scripts and nodes against a
throwaway project:

1. The import nodes' metadata scripts, run in a Python Script LOP, stamp
   the tag: a rigged character (box off) and an animatable prop yes, a
   static prop no. import_layer's update overwrites a stale stamp.
2. Discover with Stage set to that LOP: one row for the rigged character,
   Instances = its tagged prims, the animatable prop with no rig named in
   the comment (checked on a stage with no rig left, where Import bypasses),
   a static prop left out; a row the stage still animates
   keeps its version. An untagged prim (imported before the tag) is judged
   by the rule.
3. Discover with Stage empty inside a th::animate reads the stage fed into
   it; outside one it raises a NodeError that says to set Stage.
4. Browse Rigs... (the picker stood in for, picking a rigged asset, an
   unrigged one and one already in a row): the button is beside Discover,
   now labelled Add Rigs from Scene; it opens the picker in multi-select,
   adds one row (latest, one instance), names the unrigged pick in the
   status line, and imports.
5. startup.hide_retired_node_types hides th::import_rig from TAB, not
   th::import_rigs, and createNode still makes it.
6. Rig Sync on Follow Stage in a th::animate: the rows follow the stage.
7. Rig Sync moves keys: kid keyed in the dive's Scene Animate (a base
   entry and one on a layer), the stage raised to two copies, a sync: kid0
   has kid's keys as they were, kid is gone from the rows, th::animate's
   Characters and th::layout_keys rows; one undo puts it all back; a
   second sync sets nothing; with kid0 keyed already nothing moves and kid
   is kept beside it.

Needs a Houdini license, not an existing project:

    HOUDINI_PACKAGE_SKIP=1 HOUDINI_PATH="<repo>;&" PYTHONPATH=<repo>/python \
        TH_PIPELINE_PATH=<repo> hython scripts/verify_rig_discovery.py

Skipping packages also skips Houdini's own APEX scene SOPs, so the rig
import Discover runs stops at instancing; that is printed as a note.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORK = Path(tempfile.mkdtemp(prefix="verify_rig_discovery_"))
PROJECT = WORK / "project"
shutil.copytree(REPO / "scripts" / "project_template" / "_config",
                PROJECT / "_config")
os.environ["TH_PROJECT_PATH"] = str(PROJECT)
os.environ["TH_CONFIG_PATH"] = str(PROJECT / "_config")
os.environ["TH_EXPORT_PATH"] = str(PROJECT / "export")
os.environ["HOUDINI_USER_PREF_DIR"] = str(WORK / "prefs")
os.environ.setdefault("TT_USER_NAME", "verify")

import hou  # noqa: E402

from tumblepipe.api import api, reset_default_client  # noqa: E402

reset_default_client()

import tumblepipe.pipe.houdini.nodes as ns  # noqa: E402
from tumblepipe.pipe.houdini import prim_metadata  # noqa: E402
from tumblepipe.pipe.houdini import import_metadata  # noqa: E402
from tumblepipe.pipe.houdini.sops import import_rigs  # noqa: E402
from tumblepipe.pipe.paths import get_rig_export_path  # noqa: E402
from tumblepipe.pipe.paths.naming import entity_stem  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402

HERO = Uri.parse("entity:/assets/CHAR/hero")       # rigged, box off
BALL = Uri.parse("entity:/assets/PROP/ball")       # box on, no rig
CRATE = Uri.parse("entity:/assets/PROP/crate")     # neither
OLD = Uri.parse("entity:/assets/CHAR/sidekick")    # rigged, prim untagged
PAWN = Uri.parse("entity:/assets/CHAR/pawn")       # rigged, copies with a gap
KID = Uri.parse("entity:/assets/CHAR/kid")         # rigged, added on the stage later
VILLAIN = Uri.parse("entity:/assets/CHAR/villain") # rigged, not on the stage
FAILURES: list[str] = []
APEX_ADD = "apex::sceneaddcharacter"


def check(label: str, ok: bool, detail="") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}  {detail}")
    if not ok:
        FAILURES.append(f"{label}: {detail}")


def publish_rig(asset: Uri) -> None:
    version_dir = Path(get_rig_export_path(asset, "default")) / "v0001"
    version_dir.mkdir(parents=True)
    geometry = hou.Geometry()
    geometry.createPoint()
    geometry.saveToFile(str(version_dir / f"{entity_stem(asset, 'rig', 'v0001')}.bgeo.sc"))
    (version_dir / "context.json").write_text(json.dumps({
        "inputs": [],
        "outputs": [{"uri": str(asset), "department": "rig",
                     "version": "v0001", "parameters": {}}],
    }), encoding="utf-8")


def _run(label: str, execute) -> bool:
    """Discover ends in Import; with packages skipped Houdini's APEX scene
    SOPs are missing and that stops at instancing, after the rows are set."""
    try:
        execute()
    except hou.OperationFailed:
        if hou.nodeType(hou.sopNodeTypeCategory(), APEX_ADD) is None:
            print(f"  note {label}: stopped at instancing, {APEX_ADD} is not "
                  "loaded in this session (packages skipped)")
            return True
        check(label, False, traceback.format_exc())
        return False
    except Exception:
        check(label, False, traceback.format_exc())
        return False
    return True


def shot_stage():
    """/stage: prims for every asset, then the import nodes' metadata
    scripts. hero has two copies; sidekick's prim is tagged the old way."""
    stage_net = hou.node("/stage")
    prims = stage_net.createNode("primitive", "prims")
    paths = ["/CHAR/hero0", "/CHAR/hero1", "/PROP/ball", "/PROP/crate", "/CHAR/sidekick"]
    prims.parm("primpath").set(" ".join(paths))
    prims.parm("primtype").set("UsdGeomXform")
    script = stage_net.createNode("pythonscript", "metadata")
    script.setInput(0, prims)
    lines = [
        "import hou",
        "from tumblepipe.pipe.houdini import prim_metadata",
        "stage = hou.pwd().editableStage()",
        # sidekick: written before the tag existed
        "prim_metadata.set_metadata(stage.GetPrimAtPath('/CHAR/sidekick'), {",
        f"    'uri': '{OLD}', 'instance': 'sidekick', 'channel': 'default', 'inputs': []}})",
    ]
    for prim_path, asset in (("/CHAR/hero0", HERO), ("/CHAR/hero1", HERO),
                             ("/PROP/ball", BALL), ("/PROP/crate", CRATE)):
        # import_asset's script tags the asset's own prim path; point it
        # at each copy
        generated = import_metadata.asset_script(asset)
        generated = generated.replace(
            repr("/" + "/".join(asset.segments[1:])), repr(prim_path))
        lines.append(generated)
    script.parm("python").set("\n".join(lines))
    return script


def check_stamps(lop) -> None:
    print("1. the import scripts stamp animatable")
    stage = lop.stage()
    if stage is None:
        check("metadata LOP cooks", False, lop.errors())
        return

    def tag(path):
        metadata = prim_metadata.get_metadata(stage.GetPrimAtPath(path))
        return None if metadata is None else metadata.get(prim_metadata.ANIMATABLE_KEY)

    check("rigged character (box off) is animatable", tag("/CHAR/hero0") is True, tag("/CHAR/hero0"))
    check("animatable prop is animatable", tag("/PROP/ball") is True, tag("/PROP/ball"))
    check("static prop is not", tag("/PROP/crate") is False, tag("/PROP/crate"))
    check("the old prim carries no tag", tag("/CHAR/sidekick") is None, tag("/CHAR/sidekick"))

    # import_layer refreshes a stale stamp from the project
    update = lop.parent().createNode("pythonscript", "stale_then_update")
    update.setInput(0, lop)
    update.parm("python").set("\n".join([
        "import hou",
        "from tumblepipe.pipe.houdini import prim_metadata",
        "prim = hou.pwd().editableStage().GetPrimAtPath('/PROP/ball')",
        "metadata = prim_metadata.get_metadata(prim)",
        "metadata['animatable'] = False",
        "prim_metadata.set_metadata(prim, metadata)",
        import_metadata.layer_inputs_script(
            Uri.parse("entity:/shots/SQ010/SH010"), "layout", "v0001",
            [{"asset": str(BALL)}]
        ),
    ]))
    ball = prim_metadata.get_metadata(update.stage().GetPrimAtPath("/PROP/ball"))
    check("import_layer overwrites a stale stamp",
          ball is not None and ball.get("animatable") is True, ball)


def check_discover(lop) -> None:
    print("2. Discover with Stage set")
    geo = hou.node("/obj").createNode("geo", "discover_set")
    native = geo.createNode("th::import_rigs::2.0", "rigs")
    # Discover as Manual has it (Follow Stage is section 6)
    native.parm("rig_sync").set("manual")
    node = import_rigs.ImportRigs(native)
    # A row the stage still animates keeps its pinned version
    node.set_rig_imports([(HERO, "default", "v0001", 1), (CRATE, "default", "latest", 1)])
    native.parm("stage_path").set(lop.path())
    if not _run("Discover runs", node.discover):
        return
    rows = [
        (native.parm(f"entity{i}").eval(), native.parm(f"channel{i}").eval(),
         native.parm(f"version{i}").eval(), native.parm(f"instances{i}").eval())
        for i in range(1, native.parm("rig_imports").eval() + 1)
    ]
    check("a row per rigged animatable asset, Instances = its prims",
          rows == [(str(HERO), "default", "v0001", 2), (str(OLD), "default", "latest", 1)],
          rows)

    # The import above stops at instancing here, before its comment; a
    # stage with no rigged asset left bypasses and comments instead
    no_rigs = lop.parent().createNode("prune", "no_characters")
    no_rigs.setInput(0, lop)
    no_rigs.parm("primpattern1").set("/CHAR")
    no_rigs.parm("method").set("deactivate")
    native.parm("stage_path").set(no_rigs.path())
    if not _run("Discover on a stage with no rigs runs", node.discover):
        return
    comment = native.comment()
    check("no rows left", native.parm("rig_imports").eval() == 0,
          native.parm("rig_imports").eval())
    check("the animatable prop with no rig is named",
          "Animatable, no rig published: ball" in comment, repr(comment))
    check("the static prop is not", "crate" not in comment, repr(comment))


def check_stage_default(lop) -> None:
    print("3. Discover with Stage empty")
    geo = hou.node("/obj").createNode("geo", "discover_outside")
    native = geo.createNode("th::import_rigs::2.0", "rigs")
    try:
        import_rigs.ImportRigs(native).discover()
        check("outside th::animate it raises", False, "no error")
    except ns.NodeError as exc:
        check("outside th::animate it raises, naming Stage", "Set Stage" in str(exc), str(exc))

    animate = lop.parent().createNode("th::animate::2.0", "animate")
    animate.setInput(0, lop)
    dive = animate.node("anim_dive")
    if dive is None:
        check("th::animate has an anim_dive", False, [c.name() for c in animate.children()])
        return
    native = dive.createNode("th::import_rigs::2.0", "rigs")
    node = import_rigs.ImportRigs(native)
    if not _run("Discover inside th::animate runs", node.discover):
        return
    entities = [native.parm(f"entity{i}").eval()
                for i in range(1, native.parm("rig_imports").eval() + 1)]
    check("it reads the stage fed into th::animate",
          entities == [str(HERO), str(OLD)], entities)


class _HouNoUi:
    """``hou`` with the UI calls a button makes answered headless:
    hython has no main window and no status bar."""

    def __init__(self, statuses):
        self.qt = type("qt", (), {"mainWindow": staticmethod(lambda: None)})
        self.ui = type("ui", (), {"setStatusMessage": staticmethod(
            lambda text, **kwargs: statuses.append(text))})

    def __getattr__(self, name):
        return getattr(hou, name)


def check_browse_rigs() -> None:
    print("4. Browse Rigs... adds the picked rigs, then imports")
    import types

    geo = hou.node("/obj").createNode("geo", "browse_rigs")
    native = geo.createNode("th::import_rigs::2.0", "rigs")
    # Browse Rigs... as Manual has it (Follow Stage is section 6)
    native.parm("rig_sync").set("manual")
    discover = native.parmTemplateGroup().find("discover")
    browse = native.parmTemplateGroup().find("browse_rigs")
    check("Discover is labelled Add Rigs from Scene, parm name kept",
          discover is not None and discover.label() == "Add Rigs from Scene",
          discover.label() if discover else None)
    check("Browse Rigs... sits beside it",
          browse is not None and browse.label() == "Browse Rigs..."
          and discover.joinsWithNext(), browse.label() if browse else None)
    import_rigs.ImportRigs(native).set_rig_imports([(OLD, "default", "v0001", 1)])

    picks, seen, statuses = [str(HERO), str(CRATE), str(OLD)], [], []

    class Picker:
        def __init__(self, *args, **kwargs):
            seen.append(kwargs)

        def exec_(self):
            return True

        def get_selected_uris(self):
            return list(picks)

    fake = types.ModuleType("tumblepipe.pipe.houdini.ui.widgets")
    fake.EntitySelectorDialog = Picker
    saved_module = sys.modules.get(fake.__name__)
    sys.modules[fake.__name__] = fake
    import_rigs.hou = _HouNoUi(statuses)
    try:
        _run("Browse Rigs... runs", lambda: import_rigs.browse({"node": native}))
    finally:
        import_rigs.hou = hou
        if saved_module is None:
            sys.modules.pop(fake.__name__, None)
        else:
            sys.modules[fake.__name__] = saved_module
    rows = [
        (native.parm(f"entity{i}").eval(), native.parm(f"version{i}").eval(),
         native.parm(f"instances{i}").eval())
        for i in range(1, native.parm("rig_imports").eval() + 1)
    ]
    check("the picker opened in multi-select", seen and seen[0].get("multi_select") is True, seen)
    check("a row for the new rigged pick, the listed and unrigged ones not",
          rows == [(str(OLD), "v0001", 1), (str(HERO), "latest", 1)], rows)
    check("the status line counts and names the unrigged pick",
          statuses == ["rigs: Added 1 rig, skipped 1 already in the list. "
                       "No rig published: crate."], statuses)
    # Import ran: it rebuilt the dive network from the rows (with APEX
    # missing it stops at the first rig's instancing, after making it).
    dive = native.node("dive")
    imports = sorted(c.name() for c in dive.children() if c.type().name().startswith("th::import_rig"))
    check("Import ran on the rows",
          bool(imports) and set(imports) <= {"CHAR_hero_import", "CHAR_sidekick_import"}, imports)


def _rows(native):
    return [
        (native.parm(f"entity{i}").eval(), native.parm(f"version{i}").eval(),
         native.parm(f"instances{i}").eval(), native.parm(f"names{i}").eval(),
         native.parm(f"skip{i}").eval())
        for i in range(1, native.parm("rig_imports").eval() + 1)
    ]


def _copies_lop(parent, name, upstream, prims):
    """A Python Script LOP after ``upstream`` adding ``prims``
    (``(path, asset)``) stamped as import_asset stamps them."""
    lop = parent.createNode("pythonscript", name)
    lop.setInput(0, upstream)
    lines = ["import hou", "from pxr import UsdGeom",
             "stage = hou.pwd().editableStage()"]
    for prim_path, asset in prims:
        lines.append(f"UsdGeom.Xform.Define(stage, {prim_path!r})")
        generated = import_metadata.asset_script(asset)
        lines.append(generated.replace(
            repr("/" + "/".join(asset.segments[1:])), repr(prim_path)))
    lop.parm("python").set("\n".join(lines))
    return lop


def stage_asset_build(asset: Uri) -> Path:
    """A staged build of ``asset`` holding its prim, as th::import_asset
    loads it (the resolver is stood in for: this checkout's tumbleResolver
    is not built here)."""
    from pxr import Usd, UsdGeom
    from tumblepipe.pipe.paths.staged import get_staged_file_path

    staged = Path(get_staged_file_path(asset, "v0001"))
    staged.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(staged))
    UsdGeom.Xform.Define(stage, "/" + "/".join(asset.segments[1:-1]))
    UsdGeom.Xform.Define(stage, "/" + "/".join(asset.segments[1:]))
    stage.GetRootLayer().Save()
    (staged.parent / "context.json").write_text(
        json.dumps({"parameters": {"assets": []}}), encoding="utf-8")
    return staged


def check_follow_stage(lop) -> None:
    print("6. Rig Sync: Follow Stage in a th::animate")
    from tumblepipe import resolver
    from tumblepipe.pipe.houdini import animate_sync

    stage_net = lop.parent()
    # The stage as import_shot would leave it, plus Pawn: two copies with a
    # gap (pawn0, pawn2), as a layout edit leaves them
    source = _copies_lop(stage_net, "with_pawns", lop,
                         [("/CHAR/pawn0", PAWN), ("/CHAR/pawn2", PAWN)])
    animate = stage_net.createNode("th::animate::2.0", "animate_follow")
    animate.setInput(0, source)
    rigs = animate_sync.rigs_in(animate)
    check("a th::animate placed by hand starts with a th::import_rigs",
          len(rigs) == 1, [r.path() for r in rigs])
    if not rigs:
        return
    native = rigs[0]
    check("Rig Sync defaults to Follow Stage",
          native.parm("rig_sync").eval() == "follow", native.parm("rig_sync").eval())
    check("Add Assets for Rigs defaults on", animate.parm("add_assets_for_rigs").eval() == 1)

    # import_shot / import_assets end in this call
    _run("sync after an import", lambda: animate_sync.sync_downstream(source))
    rows = _rows(native)
    check("the rows follow the stage: one per rigged asset, named after its copies",
          rows == [(str(HERO), "latest", 2, "hero0 hero1", 0),
                   (str(PAWN), "latest", 2, "pawn0 pawn2", 0),
                   (str(OLD), "latest", 1, "sidekick", 0)], rows)
    rig_names = sorted(
        c.parm("names").eval() for c in native.node("dive").children()
        if c.type().name().startswith("th::import_rig::"))
    check("each rig import names its characters after the stage copies (gaps kept)",
          rig_names == ["hero0 hero1", "pawn0 pawn2", "sidekick"], rig_names)
    check("the comment names the animatable asset with no rig",
          "Animatable, no rig published: ball" in native.comment(), repr(native.comment()))
    check("sync_version is set", native.parm("sync_version").eval() == 1)

    # A pinned version and Skip survive; a sync with nothing moved sets nothing
    native.parm("version1").set("v0001")
    native.parm("skip3").set(1)
    # Parm and userData writes are what dirty a workfile (QA ledger 51:
    # setting the value a parm holds dirties it too)
    written = []
    watched = [native] + list(native.node("dive").children())
    events = (hou.nodeEventType.ParmTupleChanged, hou.nodeEventType.CustomDataChanged)

    def record(**kwargs):
        written.append((kwargs["node"].name(), str(kwargs["event_type"]),
                        getattr(kwargs.get("parm_tuple"), "name", lambda: "")()))

    for watched_node in watched:
        watched_node.addEventCallback(events, record)
    try:
        animate_sync.sync_downstream(source)
    finally:
        for watched_node in watched:
            watched_node.removeEventCallback(events, record)
    check("a sync with nothing moved sets no parm and no user data", not written, written)

    # An asset added on the stage: a row appears, pins kept
    added = _copies_lop(stage_net, "with_kid", source, [("/CHAR/kid", KID)])
    animate.setInput(0, added)
    _run("sync after the stage gained an asset", lambda: animate_sync.sync_downstream(added))
    rows = _rows(native)
    check("a row appears for the new asset; the pin and Skip stay",
          rows == [(str(HERO), "v0001", 2, "hero0 hero1", 0),
                   (str(PAWN), "latest", 2, "pawn0 pawn2", 0),
                   (str(OLD), "latest", 1, "sidekick", 1),
                   (str(KID), "latest", 1, "kid", 0)], rows)

    # Drift: the stage moves on with nothing synced, the cook only warns
    check("no drift warning while in sync",
          animate_sync.drift_message(animate.node("anim_dive"),
                                     animate.node("IN_stage_start").stage()) == "")
    animate.setInput(0, source)
    drift = animate_sync.drift_message(animate.node("anim_dive"),
                                       animate.node("IN_stage_start").stage())
    check("a stage changed since the sync warns at cook, rows untouched",
          "changed since th::import_rigs last synced" in drift and len(_rows(native)) == 4,
          drift)
    animate.setInput(0, added)

    # Add Characters for an asset not on the stage: the managed import
    staged = stage_asset_build(VILLAIN)
    saved = resolver.try_resolve_entity_uri

    def resolve(uri):
        if "villain" in str(uri):
            return str(staged)
        return saved(uri)

    resolver.try_resolve_entity_uri = resolve
    try:
        status = []
        _run("Add Characters runs",
             lambda: status.append(import_rigs.add_characters_to(native, [str(VILLAIN), str(HERO)])))
    finally:
        resolver.try_resolve_entity_uri = saved
    managed = animate.input(0)
    check("a managed th::import_assets sits just above th::animate",
          managed is not None and managed.type().name().startswith("th::import_assets")
          and managed.userData(animate_sync.MANAGED_KEY) == animate.name()
          and managed.input(0) == added,
          managed.path() if managed else None)
    if managed is not None:
        entities = [managed.parm(f"entity{i}").eval()
                    for i in range(1, managed.parm("asset_imports").eval() + 1)]
        check("it imports the picked asset off the stage, not the one on it",
              entities == [str(VILLAIN)], entities)
    rows = _rows(native)
    check("the rig row follows the asset onto the stage",
          (str(VILLAIN), "latest", 1, "villain", 0) in rows, rows)
    check("the status line says what was added",
          status and "Added to the stage: villain." in status[0]
          and "Already in: hero." in status[0], status)

    # Instances on a managed asset goes to the managed import's row
    index = [r[0] for r in rows].index(str(VILLAIN)) + 1 if str(VILLAIN) in [r[0] for r in rows] else 0
    if index:
        resolver.try_resolve_entity_uri = resolve
        try:
            native.parm(f"instances{index}").set(3)
            _run("Instances edit routes", lambda: animate_sync.route_instances(native, index))
        finally:
            resolver.try_resolve_entity_uri = saved
        check("an Instances edit on a managed asset sets its import's copies",
              managed is not None and managed.parm("instances1").eval() == 3,
              managed.parm("instances1").eval() if managed else None)
        rows = _rows(native)
        check("and the row follows the new copies",
              (str(VILLAIN), "latest", 3, "villain0 villain1 villain2", 0) in rows, rows)

    # Manual: an import never touches the rows
    native.parm("rig_sync").set("manual")
    before = _rows(native)
    animate.setInput(0, source)
    animate_sync.sync_downstream(source)
    check("Manual: a sync leaves the rows alone", _rows(native) == before, _rows(native))


CLIP = "/animation/default.clip"


def _keys_geometry(entries):
    """A Scene Animate's ``animation`` as Animate keeps it: per
    ``(character, layer)`` (layer '' for the base), channel -> (frames,
    values), each entry with its ``rig_path``."""
    geo = hou.Geometry()
    for (character, layer), channels in entries.items():
        entry = hou.Geometry()
        name = entry.addAttrib(hou.attribType.Prim, "name", "")
        entry.addAttrib(hou.attribType.Global, "rig_path", "")
        entry.setGlobalAttribValue("rig_path", f"/{character}.char/Base.rig")
        for channel_name, (frames, values) in channels.items():
            channel = entry.createChannelPrim()
            channel.setAttribValue(name, channel_name)
            for frame in frames:
                channel.insertKey(frame)
            channel.setKeyValues(values)
        folder = f"{CLIP}/{layer}" if layer else CLIP
        geo.packToFolder(f"{folder}/_{character}.char_Base", entry)
    return geo


def _entries(node):
    """``{path: (rig_path, {channel: (frames, values, slopes)})}`` of the
    key entries Scene Animate ``node`` keeps."""
    stored = node.parm("animation").evalAsGeometry()
    found = {}
    if stored is None:
        return found
    for path in stored.extractPackedPaths(f"{CLIP}/**"):
        if not path.endswith(".char_Base"):
            continue
        entry = stored.unpackFromFolder(path)
        channels = {}
        for prim in entry.prims():
            if hasattr(prim, "keyFrames"):
                channels[prim.attribValue("name")] = (
                    tuple(prim.keyFrames()), tuple(prim.keyValues()), tuple(prim.keySlopes()))
        found[path] = (entry.attribValue("rig_path"), channels)
    return found


def check_move_keys(lop) -> None:
    print("7. Rig Sync: a keyed character animated alone, its asset now two copies")
    from tumblepipe.pipe.houdini import animate_sync
    from tumblepipe.pipe.houdini.lops import animate_invoke
    from tumblepipe.pipe.houdini.sops import layout_keys_rows

    stage_net = lop.parent()
    one = _copies_lop(stage_net, "kid_alone", lop, [("/CHAR/kid", KID)])
    animate = stage_net.createNode("th::animate::2.0", "animate_move")
    animate.setInput(0, one)
    dive = animate.node("anim_dive")
    native = animate_sync.rigs_in(animate)[0]
    _run("sync on one copy", lambda: animate_sync.sync_downstream(one))
    row = [r for r in _rows(native) if r[0] == str(KID)]
    check("one copy: the character is kid", [r[3] for r in row] == ["kid"], row)
    scene_animate = next(c for c in dive.children()
                         if c.type().name().startswith("apex::sceneanimate"))
    # Keyed in Scene Animate: a base entry and one on a layer
    scene_animate.parm("animation").set(_keys_geometry({
        ("kid", ""): {"Main_tx": ((1, 12, 24), (0.0, 1.5, 4.0)), "Main_ry": ((1,), (30.0,))},
        ("kid", "copied"): {"Main_ty": ((1, 24), (0.0, 2.0))},
    }))
    before = _entries(scene_animate)
    # th::animate's Characters and a th::layout_keys row name kid
    count = animate.parm(animate_invoke.MULTIPARM).eval()
    if "kid" not in [animate.evalParm(f"character{i}") for i in range(1, count + 1)]:
        animate.parm(animate_invoke.MULTIPARM).set(count + 1)
        animate.parm(f"character{count + 1}").set("kid")
    layout = layout_keys_rows.create(dive, "layout_keys").native()
    layout_keys_rows.LayoutKeys(layout).set_rows([{"on": True, "character": "kid",
                                                   "target": "kid", "version": "latest"}])

    two = _copies_lop(stage_net, "kid_twice", lop, [("/CHAR/kid0", KID), ("/CHAR/kid1", KID)])
    animate.setInput(0, two)
    moved = {path.replace("_kid.char_Base", "_kid0.char_Base"): (
        "/kid0.char/Base.rig", channels) for path, (_rig, channels) in before.items()}
    # The sync alone first (the import after it is undo steps of its own)
    node = import_rigs.ImportRigs(native)
    result = node.sync()
    check("kid0 carries kid's keys, base and layer, as they were (rig_path renamed)",
          _entries(scene_animate) == moved, sorted(_entries(scene_animate)))
    check("the sync's notes say the keys moved", "kid -> kid0" in "\n".join(result.notes),
          result.notes)
    if hou.undos.areEnabled():
        hou.undos.performUndo()
        back = [r[3] for r in _rows(native) if r[0] == str(KID)]
        check("one undo puts kid's keys and its row back together",
              _entries(scene_animate) == before and back == ["kid"],
              (sorted(_entries(scene_animate)), back))
        hou.undos.performRedo()
        check("and redo moves them again", _entries(scene_animate) == moved,
              sorted(_entries(scene_animate)))
    else:
        print("  note undo: undos are off in this hython; the undo step is not checked")
    _run("sync on two copies", lambda: animate_sync.sync_downstream(two))
    after = _entries(scene_animate)
    check("after the import's sync too, kid0 has kid's keys and nothing of kid is left",
          after == moved, sorted(after))
    row = [r for r in _rows(native) if r[0] == str(KID)]
    check("the row follows the two copies, kid gone",
          [r[3] for r in row] == ["kid0 kid1"], row)
    names = [animate.evalParm(f"character{i}")
             for i in range(1, animate.parm(animate_invoke.MULTIPARM).eval() + 1)]
    check("th::animate's Characters row is kid0, no kid", "kid0" in names and "kid" not in names,
          names)
    layout_rows = layout_keys_rows.LayoutKeys(layout).get_rows()
    check("th::layout_keys' row is kid0",
          [(r["character"], r["target"]) for r in layout_rows] == [("kid0", "kid0")],
          layout_rows)

    # Once moved, a sync moves and sets nothing
    written = []
    watched = [native, scene_animate, animate, layout]

    def record(**kwargs):
        written.append((kwargs["node"].name(), str(kwargs["event_type"])))

    events = (hou.nodeEventType.ParmTupleChanged, hou.nodeEventType.CustomDataChanged)
    for node in watched:
        node.addEventCallback(events, record)
    try:
        animate_sync.sync_downstream(two)
    finally:
        for node in watched:
            node.removeEventCallback(events, record)
    check("a second sync sets no parm and no user data", not written, written)

    # X0 with keys of its own: kid is kept beside it, nothing overwritten
    animate.setInput(0, one)
    scene_animate.parm("animation").set(_keys_geometry({
        ("kid", ""): {"Main_tx": ((1, 24), (0.0, 4.0))},
    }))
    _run("sync back on one copy", lambda: animate_sync.sync_downstream(one))
    scene_animate.parm("animation").set(_keys_geometry({
        ("kid", ""): {"Main_tx": ((1, 24), (0.0, 4.0))},
        ("kid0", ""): {"Main_tz": ((1, 24), (0.0, 7.0))},
    }))
    before = _entries(scene_animate)
    animate.setInput(0, two)
    _run("sync with kid0 keyed", lambda: animate_sync.sync_downstream(two))
    check("kid0 keyed already: nothing moved, nothing overwritten",
          _entries(scene_animate) == before, sorted(_entries(scene_animate)))
    row = [r for r in _rows(native) if r[0] == str(KID)]
    check("kid is kept beside kid0 and kid1", [r[3] for r in row] == ["kid0 kid1 kid"], row)
    check("the comment says why", "kid kept beside kid0" in native.comment(),
          repr(native.comment()))


def check_import_rig_hidden() -> None:
    print("5. th::import_rig is hidden from TAB, and still made by name")
    from tumblepipe import startup

    startup.hide_retired_node_types()
    rig_type = hou.nodeType(hou.sopNodeTypeCategory(), "th::import_rig::1.0")
    rigs_type = hou.nodeType(hou.sopNodeTypeCategory(), "th::import_rigs::2.0")
    check("th::import_rig is hidden", rig_type is not None and rig_type.hidden(),
          rig_type.hidden() if rig_type else None)
    check("th::import_rigs is not", rigs_type is not None and not rigs_type.hidden())
    geo = hou.node("/obj").createNode("geo", "hidden_rig")
    try:
        made = geo.createNode("th::import_rig::1.0", "rig")
        check("createNode still makes th::import_rig", made is not None)
    except hou.OperationFailed as exc:
        check("createNode still makes th::import_rig", False, str(exc))


def main() -> int:
    print(f"project: {PROJECT}")
    project = Path(api.storage.resolve(Uri.parse("project:/")))
    if WORK not in project.parents and project != PROJECT:
        print(f"refusing to run: project resolves to {project}, not into {WORK}")
        return 1
    for uri, properties in (
        (Uri.parse("entity:/assets/CHAR"), {}),
        (Uri.parse("entity:/assets/PROP"), {}),
        (HERO, {"name": "hero"}),
        (BALL, {"name": "ball", "animatable": True}),
        (CRATE, {"name": "crate"}),
        (OLD, {"name": "sidekick"}),
        (PAWN, {"name": "pawn"}),
        (KID, {"name": "kid"}),
        (VILLAIN, {"name": "villain"}),
    ):
        if api.config.get_properties(uri) is None:
            api.config.add_entity(uri, properties)
        elif properties:
            api.config.set_own_properties(uri, {**(api.config.get_own_properties(uri) or {}), **properties})
    for asset in (HERO, OLD, PAWN, KID, VILLAIN):
        publish_rig(asset)

    lop = shot_stage()
    check_stamps(lop)
    check_discover(lop)
    check_stage_default(lop)
    check_browse_rigs()
    check_follow_stage(lop)
    check_move_keys(lop)
    check_import_rig_hidden()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        for failure in FAILURES:
            print(f"  - {failure}")
        print(f"project kept for inspection: {WORK}")
        return 1
    print("all checks passed")
    import logging
    logging.shutdown()  # pipeline.log holds a handle under WORK
    shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
