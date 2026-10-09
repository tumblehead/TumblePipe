# Radial menus and shortcuts

TumblePipe's quick access to its nodes and actions is a set of radial menus
that open over a network editor. They are registered at Houdini startup by
`python/tumblepipe/startup.py` (called from `python3.11libs/pythonrc.py`),
and they all belong to the **radial** — the Qt radial that ships as its own
package, `tumbleradial`.

## Requirement: the `tumbleradial` package

Nothing on this page exists unless `tumbleradial` is installed alongside
TumblePipe (it is a separate package in TumbleTrove). Without it, startup
logs this warning and registers nothing:

```
tumbleradial is not installed, so TumblePipe's radial menus (pipeline submenus, recipes, asset favorites, cop/vop network menus) will not register
```

Install the package, restart Houdini, and the menus appear. The COP and
VOP network menus need **Radial 0.4.0 or later**; an older Radial registers
the other menus and logs `tumbleradial predates bind() (Radial 0.4.0), so
TumblePipe's COP and VOP network menus will not register`. There is no
other fallback — the Houdini-native `radialmenu/` system that used to carry
these menus was retired in the radial's favour.

The shipped menu definitions live in the package's `radial_menus/`
directory, which startup registers with the radial. `tumblepipe_pipeline.json`
is the one static file; the recipes and favourites menus below are
regenerated into the same directory on every launch.

## Alt+T: the TumblePipe pipeline radial

Key **Alt+T**, context `network`, label **TumblePipe**
(`radial_menus/tumblepipe_pipeline.json`). The ring holds six slots:

| Slot | What it does |
|---|---|
| **Project info** | shows the project dialog described below; closes the radial |
| **Asset** | submenu of the import/export nodes |
| **Render** | submenu of the render-setup nodes |
| **Refresh cache** | the cache action described below |
| `network.parms`, `network.spreadsheet` | two of the radial's own network actions, borrowed onto this ring |

The two submenus each place a node of the named type in the current
network:

**Asset**

| Entry | Node |
|---|---|
| Import Layer | `th::import_layer::1.0` |
| Import Assets | `th::import_assets::2.0` |
| Export Layer | `th::export_layer::1.0` |

**Render**

| Entry | Node |
|---|---|
| Mattes | `th::mattes::1.0` |
| LPE Tags | `th::lpe_tags::1.0` |
| Render Settings | `th::render_settings::1.0` |
| Render Vars | `th::render_vars::1.1` |

All of these are LOPs, so the radial is meant for a `/stage` network. The
nodes themselves are documented under [Nodes](nodes/index.md) — the import
and export nodes in [Import and export](nodes/import-and-export.md), the
render nodes in [Lighting and rendering](nodes/lighting-and-rendering.md).

## Alt+R: recipes

Key **Alt+R**, context `network`, label **Recipes**. This menu is not a
shipped file: at startup TumblePipe reads the definitions in
`otls/Recipes.hda` and writes `radial_menus/tumblepipe_recipes.json` with one
slot per recipe, in the HDA's order, up to the ring's maximum of **9**. Each
slot's label is the recipe's own label and its icon the recipe's icon; picking
one places that recipe in the network editor and closes the radial.

The package currently ships four recipes: *th configure forest gobo*, *th
configure grass*, *th configure lop import* and *th configure material
override*.

Every recipe gets an action (`tumblepipe.recipe.<name>`), including any past
the ninth, because the department menus place recipes through the same
actions.

A radial ring needs at least two filled slots, so when fewer than two
recipes exist the menu is not written (and a stale file from an earlier
session is deleted) — Alt+R then does nothing. (These HDA recipes are a
different thing from the Asset Browser's project recipes, the saved node
clusters under `<project>/recipes/` — see
[Recipes](asset-browser/recipes.md).) See [Tools](nodes/tools.md).

## Alt+F: asset favourites

Key **Alt+F**, context `network`, label **Asset Favorites**. Also generated
at startup: TumblePipe collects the assets you have marked as favourites in
the Asset Browser (the `__favorites__` collection of every catalog), takes
the first **9**, and writes `radial_menus/tumblepipe_asset_favorites.json`
with one star-icon slot per asset, labelled with the asset's name.

Firing a slot **drops the asset into the network under the cursor** — the
same path a drag from the browser takes, so placement, auto-connect and
position follow the cursor. If the cursor is not over a network editor the
slot falls back to the asset's first non-download action. The radial stays
open afterwards, so several favourites can be dropped in one go. Failures
go to the status bar: `asset '<id>' unavailable: …` when the catalog cannot
resolve it, `no drop action for <name>` when nothing applies.

Two limits: the list is read **once, at startup**, so a favourite added
mid-session appears after the next launch; and with fewer than two
favourites no menu is written and Alt+F does nothing.

## The two general actions

Both are TumblePipe actions the pipeline radial's ring uses; they are
registered whether or not any menu shows them.

**Project info** (`tumblepipe.show_project_info`, available in every
context) opens a dialog titled **TumblePipe** with

```
TumblePipe project info

Project name : <name>
User         : <user>
Pipeline     : <path>
Project path : <path>
Edit path    : <path>
```

A value that is empty reads `(unset)`; one that cannot be read at all reads
`(error: …)` — for example `Edit path` reports an error when `TH_EDIT_PATH`
is not in the environment, which is the normal case, since the package
manifest does not set it. This is the quickest way to confirm which project
and user a session is running as; see
[Environment variables](configuration.md#environment-variables).

**Refresh cache** (`tumblepipe.refresh_global_cache`, network context) calls
`api.refresh_global_cache()`, which drops the pipeline's in-memory config
cache so the next read comes from the `db/*.json` files on disk. Config reads
are already coherent (an edited database file is picked up on the next
read), so this is an explicit "discard what you have" for the rare case
where you want to force it. A failure shows a warning dialog:
`refresh_global_cache failed: …`.

## COP and VOP network context menus

Two further menus are built in Python rather than JSON and bound at the
radial's `network.cop` and `network.vop` contexts, the context IDs the
radial reports for a Copernicus and a VOP network. They open on the same
key as the radial's own menus: Radial's **Open menus with** setting
(**TumbleTrove ▸ Settings… ▸ Radial**, Space by default). Over a COP or VOP
network that key opens these menus in place of Radial's network menu.
Change the setting and they move with it; clear it and they are unbound
along with Radial's own. They are bound once the Houdini UI is up, so a
headless `hython` session has none. See
[Project structure → radial_menus](project_structure.md#radial_menus).

- **COP** — submenus **Convert** (Mono/UV/RGB/RGBA), **Pattern**, **Filter**,
  **Composite** (over, blend, multiply, add, under, subtract, divide), plus
  **File** and **Render**; the convert, composite and render entries run
  TumblePipe's `coputils` helpers on the selected nodes. See [Comp](nodes/comp.md).
- **VOP** — submenus **Image**, **Adjust**, **Generators** and **Utility**,
  each placing MaterialX / Karma VOP nodes. See [Tools](nodes/tools.md).

## Shift+Space: the department menu

TumblePipe ships one radial per department, the **stock** department menus,
in `stock/radial_menus/<department>.json`. Each one carries
`"when": {"department": ["<department>"]}`, so it applies only in that
department's workfiles. These menus bring back the per-department radials TumblePipe had
up to v1.20, before its menus moved from Houdini's native radial menus to
Radial.

| Department | Network | Ring |
|---|---|---|
| model | SOP | File, Color, Blast, Cache, Match Size, Transform, Group |
| blendshape | SOP | File, Sculpt, Edit, Mesh Blender, Blast |
| lookdev | LOP | Material, Material Assigner, Lookdev Studio, COP Material Library, Material Library, Assign Material, Export Layer |
| rig | SOP | Import Model, Skeleton, Capture, Pack Folder, Rig Doctor, Export Rig |
| layout | LOP | Import Assets, Edit, Transform, Camera, Prune, Export Layer |
| environment | LOP | Import Assets, Grass (recipe), Instancer, Edit, Prune, Export Layer |
| animation | SOP | Import Rigs, Scene Animate, Scene Invoke, Playblast, Cache |
| crowd | SOP | Agent, Crowd Source, Agent Layer, File, Cache |
| effects | SOP | File, Pyro Solver, RBD Solver, Vellum Solver, Cache |
| cfx | SOP | File, Vellum Constraints, Vellum Solver, Vellum Drape, Cache |
| light | LOP | Light Linker, Lights (Rect, Distant, Dome), Light Gobo (recipe), Light Mixer, Light Blocker, Fog |
| render | LOP | Render Settings, Render Vars, LPE Tags, Mattes, Render Debug |
| composite | COP | Build Comp, LOP Import, File, Depth Cull, Gradient Map, A/B Slider |

**Shift+Space** opens the menu for the workfile's department. Radial owns
that key. The department comes from TumbleTrove: at startup TumblePipe
registers as its workfile-context provider (`tumblepipe.workfile_context`),
which reads the `context.json` beside the open .hip. TumbleTrove re-reads it
when you open, save as or start a new scene.

The stock menus are the lowest of five layers. A menu from a higher layer
replaces the stock one for its department:

1. yours
2. the project's, saved by a lead into the project config
   (`_config/radial_menus/`)
3. the organisation's
4. a package's, such as TumbleRig's rig radial
5. TumblePipe's stock menus

The stock menus are not in `radial_menus/`, because TumbleTrove reads a
package's `radial_menus/` folder as that package's own layer.

This needs a **TumbleTrove** that has the workfile context
(tumbletrove/asset-browser#24) and a **Radial** that has department menus
(tumbletrove/radial#1). With older versions TumblePipe registers nothing
and logs nothing, and Shift+Space does nothing.

## Keyboard shortcuts

TumblePipe registers no Houdini hotkeys of its own. The only keys it claims
are the three radial keys above (**Alt+T**, **Alt+R**, **Alt+F**), and only
while `tumbleradial` is installed. **Shift+Space** belongs to Radial;
TumblePipe only supplies the menus it opens. The COP and VOP menus claim no key; they
use whichever key Radial's own menus open with.
