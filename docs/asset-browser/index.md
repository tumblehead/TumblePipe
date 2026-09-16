# The Asset Browser and the pipeline

TumblePipe does not ship a browser of its own. It registers a **catalog** in
TumbleTrove's Asset Browser, and that catalog is where you open workfiles,
save versions, publish, submit renders, and create shots and assets. This
page covers what TumblePipe adds to the panel. The generic browser (cards,
decks, grid and list view, search, favourites, the shelf, drag and drop
mechanics) is described in the TumbleTrove documentation under *Asset
Browser*; the *Pipeline assets* page there is the one closest to this.

Two more pages go deeper:

- [Workfiles and versions](workfiles.md) — opening, creating and saving
  department workfiles, and what the version numbers mean.
- [Multis and Roots](multis-and-roots.md) — shared multi-shot workfiles and
  config-driven scenes.

Source: [`catalog.py`](../../python/tumblepipe/asset_browser/catalog.py) is
the hub; the other modules in that folder are named where they matter.

## Opening the browser

The `TumblePipe` desktop (`desktop/TumblePipe.desk`) carries the Asset
Browser as a Python Panel pane tab, and
[`python3.11libs/uiready.py`](../../python3.11libs/uiready.py) switches
Houdini to that desktop once the UI is ready. If you use another desktop,
open it the TumbleTrove way: the **TumbleTrove** menu in Houdini's menu bar,
or a pane tab's type menu → **Python Panel** → **Asset Browser**.

The catalog appears on the rail as **TumblePipe**. When Houdini was launched
into a project (`TH_PROJECT_PATH` set — see
[Configuration → Environment variables](../configuration.md#environment-variables))
the browser opens scoped to that project, and the project's name is shown
under the catalog name in the sidebar.

Everything the catalog does starts on a worker thread and only touches the
Houdini scene on the main thread, so a slow network share makes the grid
late, not Houdini frozen.

## Projects

The catalog can browse several projects at once. They are registered in
`projects.json` under `$HOUDINI_USER_PREF_DIR/asset_browser/` (or
`~/.config/asset_browser/` when that variable is unset). On first launch the
list is seeded from the launch project's environment.

Click the **gear** in the sidebar header to open the catalog's settings page,
titled **Projects**
([`settings_widget.py`](../../python/tumblepipe/asset_browser/settings_widget.py)):

| Control | What it does |
|---|---|
| **Add** / **Remove** | Add or drop a project from the list. |
| **Name**, **Project Path** (with **Browse…**), **Config Path** | The selected project's identity; Config Path is optional. |
| **Departments…** | Opens the **Departments — *project*** pool editor; see [Configuration → Departments](../configuration.md#departments). |
| **Autosave (version up) on scene change** | Off by default. See [Workfiles → Switching scenes with unsaved changes](workfiles.md#switching-scenes-with-unsaved-changes). |
| **Auto-import latest on workfile open** | On by default. See [Composition → Picking up new versions on open](../composition.md#picking-up-new-versions-on-open). |
| **Ask for a version note on save** | On by default. See [Workfiles → The version note](workfiles.md#the-version-note). |
| **Apply Project Changes** | Writes the project list and reconnects. |

The three toggles are saved to `pipeline_prefs.json` next to `projects.json`.

With more than one project registered, each project becomes a folder in the
sidebar and the creation dialogs gain a **Project** dropdown. With exactly
one, its sections sit at the top level.

Opening a workfile that belongs to a different project than the one your
current scene lives in does not load it into this session: it launches a new
Houdini on it (see [Workfiles → Open in New Houdini](workfiles.md#open-in-new-houdini)).
The first time an action switches the active project the status bar says
*Switched pipeline context to X. Some operations may require a Houdini
restart.*

## The sidebar

Per project:

| Section | Rows underneath | Notes |
|---|---|---|
| **Assets** | a **Multis** subheader (only once an assets Multi exists), then one row per category | |
| **Shots** | a **Multis** subheader (only once a shots Multi exists), then one row per sequence | Sequences open in list view by default. |
| **Roots** | one row per Root | |
| **Recipes** | one row per Houdini context (**SOP**, **LOP**, …) that holds a saved recipe | See [Recipes](recipes.md). |

Count pills show how many assets, shots or recipes a row holds. Empty
categories and sequences are listed too, so a bucket can exist before its
first entity, and the Recipes section is listed before its first recipe.

The sidebar has no Todos section; an entity's tasks live on its card's
**Tasks…** menu item and on the detail panel's **Tasks** tab.

Right-click menus on sidebar rows. Each starts with the creation options the
**+** card offers when that row is selected (see
[Creating entities](#creating-entities)); the table lists what follows them.

| Row | Creation options | Then |
|---|---|---|
| **Assets** header, a category | **New Asset...**, **New Category...**, **New Multi...**, **New Root...** | a category adds **Delete category '*category*'…** |
| **Shots** header, a sequence | **New Shot...**, **New Sequence...**, **New Multi...**, **New Root...** | a sequence adds **Delete sequence '*sequence*'…** |
| **Multis** subheader, a Multi | **New Multi...** | a Multi adds the Multi card's menu, plus **Add selected to Multi** and **Remove selected from Multi** |
| **Roots** header | **New Root...** | |
| a Root | **New Asset...**, **New Shot...**, **New Recipe...**, **New Multi...**, **New Root...** | the Root card's menu, plus **Add selected to Root** and **Remove selected from Root** |
| **Recipes** header, a context row | **New Recipe...** | |

A Multi or Root row's menu is its card's menu, from **Open** to **Delete**,
Root export actions included (see
[Multis and Roots](multis-and-roots.md#root-actions)).

The add/remove entries act on the grid's current selection and are greyed
out without one. Multi and Root rows also accept dragged cards.

## Cards

An asset card is named after the asset; when the view mixes categories (the
Assets root, a Root's members, the *All* view) the name is prefixed with its
category, `CHAR/Baby`, so same-named assets in different categories stay
apart. Inside one category the bare name is shown. A shot card is always
`sequence_shot`.

The card whose workfile is currently open in Houdini is sorted first and
drawn with a highlight border.

**Thumbnail.** Read from a `thumbnail.png` sidecar in the entity's folder
(`<project>/assets/<category>/<name>/thumbnail.png` or
`<project>/shots/<sequence>/<shot>/thumbnail.png`,
[`thumbnails.py`](../../python/tumblepipe/asset_browser/thumbnails.py)).
Without one the card shows the placeholder icon. Two right-click items write
it:

- **Select thumbnail…** opens a **Select Thumbnail** file dialog
  (`png jpg jpeg tif tiff bmp exr`) and saves the picked image as PNG.
- **Capture thumbnail** flipbooks the current frame of the active Scene
  Viewer at 512×512 into the sidecar. With no Scene Viewer open the status
  bar says *Capture: no active Scene Viewer.*

The same sidecar is attached as a network image next to any import node the
browser creates for that entity.

**Hover popup** (the info control on a card): the name; a line with the type
(*Asset*, *Shot*, *Multi*, *Root*), project and category; any custom tag
pills; and, for assets and shots, a **Departments** icon grid — the entity's
departments in pipeline order, bright where a workfile exists and dark where
none does. Pin the popup and hover an icon for that department's version,
user, last saved and last published times. Multi and Root cards list their
member count instead.

**Multi and Root cards** have no thumbnail. The Root card carries a small
orange dot when its asset list has changed since its last export; see
[Multis and Roots](multis-and-roots.md#the-drift-dot).

## Department rows and the deck

Behind each asset or shot card is a **deck** with one item per department
([`get_deck_items`](../../python/tumblepipe/asset_browser/catalog.py)).
Open it with the card's chevron or a double-click; in list view the same
items are the rows under the entity. The items are the departments the
entity is scoped to plus any department that already has a workfile (a row
that has work but is not assigned carries a tooltip saying so), in pool
order.

| Item state | Meaning |
|---|---|
| a version label (`v0012`) | the latest workfile in that department |
| bright | the department of the scene you have open |
| dark, no version | no workfile yet |
| **ⓜ** with an orange tint | the department is covered by a Multi; the tooltip names it |

Labels are the department's `short` from the pool when it has one,
otherwise `Anim`, `Blend`, `Comp`, `Enviro` or the title-cased name.

Double-click an item to open that department's latest workfile. On an item
with no workfile yet (it reads *missing*), a double-click creates the first
version from the department template instead. Right-click one for:

| Item has versions | Item has none |
|---|---|
| **Open Latest (vNNNN)** | **New from Template** |
| **Open Folder** | **New from Current** |
| **View Latest Export** | |
| **Open in New Houdini** | **Open Folder** |
| **Reload Scene** (only on the open department) | |
| **Remove from *Multi*** (only on a ⓜ item) | |
| **New from Template** | |
| **New from Current** | |

The item an artist almost always wants comes first: creating the first
version on an empty item, opening the latest on one that has versions. What
each does is on the [Workfiles](workfiles.md) page. A Multi's own deck items
offer **New from Template** and **Open Folder**, plus **Open Latest** once a
version exists.

### List view columns

| Column | Entity row | Department row |
|---|---|---|
| **Name** | entity name | department |
| **Version** | empty | latest version, or ⓜ |
| **Note** | the note saved with the newest version across departments | the note on that department's latest version |
| **Category** | category | |
| **Depts** | number of departments with work | |
| **User** | who saved the newest version | who saved that version |
| **Edited** | when, as `2h ago` | when |

The **Note** column only appears on TumbleTrove 0.24 or newer.

Sort options: **Name A-Z**, **Name Z-A**, **Latest Update**, **Oldest
Update**.

## The detail panel

The right pane describes the card you clicked, and re-reads every time you
click another one. It used to be a *session panel* pinned to the open
`.hip` instead, which meant browsing five assets while lilGuy was open
showed lilGuy five times.

Nothing about the open scene was lost with it: the toolbar label names the
open `.hip`, its card is marked in the grid, and the open department is the
bright row in whichever entity's **Departments** section you are looking at
— a Multi's included.

For a pipeline card the panel shows
([`detail.py`](../../python/tumblepipe/asset_browser/detail.py)):

- **Info**: the entity URI (with a copy button), the export folder path, a
  small table (project, category and channels for an asset; project,
  sequence, frame start/end/total, FPS and the assigned **Root** for a shot)
  and the description.
- **Departments**: pills for every Multi (`M`) and Root (`R`) the entity
  belongs to, a **Departments…** button, and one row per department with a
  version dropdown and a play button that opens the chosen version (or
  creates `v0001` from the template when there is none). Right-click a row
  for the same menu as a deck item, with **Open vNNNN** for the version the
  dropdown shows.
- **Tasks**: the todo list with an **Add task…** field and a clear menu
  (**Clear completed** / **Clear all**).
- A bottom action bar: **Import to Scene** (assets only; creates a
  `th::import_asset` in `/stage`), **Open Export Folder**, **Open in
  Database Editor…**, **Edit…** and **Delete**.

A Multi card gets **Info** and **Departments** (its coverage rows), a Root
card **Info** alone, and both an action bar of **Edit…** and **Delete**.

## Quick actions

The toolbar's quick-action buttons come from the catalog. The label beside
them is the open `.hip`'s filename.

| Button | Tooltip | Does |
|---|---|---|
| **Save** | Save current scene | Saves the open scene as the **next version** of its own department. Asks for a note first. See [Workfiles → Saving](workfiles.md#saving-a-new-version). |
| **Publish** | Publish exports | Opens the **Publish** process dialog for the open scene's entity, with every export task listed and a local/farm choice. |
| **Render** | Submit render jobs for the current scene's entity | Opens the **Submit Jobs** dialog for the scene's shot or asset (for a Multi workfile, its member shots), with the render department seeded from the open workfile's department. |
| **Update** | Re-import latest published versions into the current scene (no scene reload) | Re-executes every `th::import_*` node. Status: *Imports updated to latest published versions (N node(s)).* If any node failed a warning dialog says how many. See [Composition → Picking up new versions mid-session](../composition.md#picking-up-new-versions-mid-session). |
| **Reload** | Reload current scene | Reloads the open `.hip` from disk (after the unsaved-changes prompt), then re-applies the timeline and the import refresh. |

Hover **Save** for *Last saved* and **Publish** for *Last published* ages.
Right-click **Save** for **Emergency Save (off-thread)**: a save that runs
inline instead of waiting for Houdini's event loop, for when Houdini is
sitting in its crash-report dialog. It never prompts for a note.

Save, Publish and Render each refuse with a dialog when the open scene has no
pipeline context (an untitled or off-pipeline hip): *Save: the current scene
has no pipeline context, so there is no version to save it as. Open or
create the scene through the pipeline first.*, and the equivalent for
Publish and Render.

## Right-click on a card

Besides TumbleTrove's own entries (favourites, collections, **Add to
Multi ▸**, **Add to Root ▸**, **Delete asset…** / **Delete shot…**), the
catalog contributes
([`get_card_menu_items`](../../python/tumblepipe/asset_browser/catalog.py)):

| Item | What it does |
|---|---|
| **Submit Jobs…** / **Submit Jobs for N selected…** | Opens the **Submit Jobs** dialog for this entity, or for every selected card of the same kind (shots with shots, assets with assets). |
| **Generate Master…** | Merges each department's latest workfile into `<entity folder>/master/<name>_master.hip`, one network box per department, after a **Generate Master Scene** confirmation. Opens that scene. |
| **Edit description…** | Edits the `description.txt` sidecar in the entity's folder (dialog **Edit Description**). Shown on the Info tab. |
| **Tasks…** | Opens TumbleTrove's **Tasks — *name*** dialog for the entity's todo list. |
| **Select thumbnail…** / **Capture thumbnail** | See [Cards](#cards). |
| **Departments…** | Which departments this entity uses (**Departments — *name***, tick boxes, **Use all (inherit)**, **Apply**); see [Configuration → Per-entity assignment](../configuration.md#per-entity-assignment). On a Multi it opens the coverage editor instead. |
| **Open in Database Editor…** | Opens the **Database Editor** window on this entity. |
| **Clear Root** | Shots only, and only when a Root is set directly on the shot (not inherited from the sequence). Clears it. |
| **Remove from Multi: *name*** / **Remove from Root: *name*** | One entry per container the entity belongs to. |

A recipe card has its own menu — **Edit Description…**, **Set Icon…**, **Open
Recipe Folder**, **Delete Recipe** — see [Recipes](recipes.md#card-menu-and-detail-actions).

## Creating entities

The dashed **+** card, the list view's **+** row, a right-click on empty
space in either view, and the sidebar menus above all offer the same list for
the same place
([`get_creation_options`](../../python/tumblepipe/asset_browser/catalog.py))
and lead to the same small forms (`get_creation_fields`). Under Assets (or a
category) the list is **New Asset...**, **New Category...**, **New
Multi...** and **New Root...**; under Shots (or a sequence) it is **New
Shot...**, **New Sequence...**, **New Multi...** and **New Root...**. A
Multis subheader offers **New Multi...** alone, the Roots header **New
Root...** alone, the Recipes section **New Recipe...** alone, and anywhere
unscoped offers **New Asset...**, **New Shot...**, **New Recipe...**, **New
Multi...** and **New Root...**. Dragging selected nodes from a network editor
onto the grid also opens **New Recipe** (TumbleTrove 0.35 or later). A
**Project** dropdown is added to every form when more than one project is
registered.

| Form | Fields | Notes |
|---|---|---|
| **New Asset** | **Name**; **Category** (dropdown of existing categories) | Started from a category row, the category is fixed. Refuses *Asset 'X' already exists in 'Y'.* |
| **New Shot** | **Name**; **Sequence** (dropdown); **Frame Start** `1001`; **Frame End** `1100` | Started from a sequence row, the sequence is fixed. |
| **New category** | **Category** (free text) | Creates an empty category. |
| **New sequence** | **Sequence** (free text) | Creates an empty sequence. |
| **New Multi** | **Name**; **Context** (`shots` or `assets`, default `shots`) | See [Multis and Roots](multis-and-roots.md#creating-a-multi). |
| **New Root** | **Name** | See [Multis and Roots](multis-and-roots.md#creating-a-root). |
| **New Recipe** | **Name**; **Description**; **Tags** | Saves the nodes selected in the network editor. See [Recipes](recipes.md#saving-a-recipe). |

Names are written as typed; the new asset or shot is selected in the grid
once it appears. A new shot stores its frame range on the shot entity; a new
asset stores only its name. Neither creates a workfile — that is the
department row's **New from Template**.

## Editing and deleting entities

**Edit…** (detail panel action bar) opens **Edit *name***. For a shot it
shows **Project**, **Sequence** and **Name** read-only and lets you change
**Frame Start** and **Frame End**; only values you actually changed are
written, as the shot's own override, so an untouched field keeps following
the sequence. For an asset every field is read-only. Edit refuses an entity
that is not in the project configuration and writes nothing; the status bar
reads *Edit failed for 'name' — see Python Shell*, and the shell shows
`EntityNotRegistered`. Up to TumblePipe 1.47.1 such an edit silently added
the entity.

**Delete** removes the entity from the project configuration after a
**Delete entity** confirmation (*Delete 'name'? This cannot be undone from
the browser.*). It does not delete files on disk. **Delete category '…'…**
and **Delete sequence '…'…** refuse while the bucket still holds entities
(*Category 'X' still contains 3 assets.*) and confirm when it is empty.

## Dragging into Houdini

Drop targets and what gets built
([`drops.py`](../../python/tumblepipe/asset_browser/drops.py)):

| You drag | Onto | Result |
|---|---|---|
| an asset card | a LOP network | a `th::import_asset` node named after the asset, executed, display flag set (LOP nodes have no render flag) |
| a shot card | a LOP network | a `th::import_shot` node named `sequence_shot` |
| an asset card | an existing `th::import_assets` node | the asset is appended to that node (*Added X to Y*) |
| an asset card | an existing `th::import_asset` node | the node is replaced by a `th::import_assets` holding both assets, keeping its name, wiring and options (*Combined X into Y*) |
| several asset cards | a LOP network | one `th::import_assets` with an entry per asset, and nothing else; shots in the selection are skipped. If the build fails you get *Failed to import N assets (see console)*, not a `th::import_asset` per card |
| an asset card | a SOP network | a `th::import_model` with its **department** set to `model` (`blendshape` for assets tagged so) |
| a shot card | a SOP network | refused: *Shots can only be imported into LOP networks* |
| a Root card | a LOP network | a stock `sublayer` node whose file path is the Root's `entity:/scenes/…` URI, so it follows the Root's latest export |
| a recipe card | any network editor | the recipe's nodes, wired and placed at the cursor; another context than the one it was saved in asks **Load Anyway** first. See [Recipes](recipes.md#using-a-recipe) |
| department deck items or list rows | a LOP network | one `th::import_layer` per department, wired and flagged (TumbleTrove's default path) |

A single asset or shot card dropped into a LOP or SOP network also
pins the entity's `thumbnail.png` above the new node in the network
editor, saved with the hip. On `th::import_asset`, `th::import_shot` and
`th::import_model` the image follows a rename and goes away with the
node. `th::import_assets` has neither callback, so the image a *Combined
X into Y* drop pins stays behind when that node is renamed or deleted.
A multi-card drop adds no thumbnail.

Any other pane: *Pipeline assets can only be imported into LOP or SOP
networks.* While you hover a network editor a ghost of the node to be
created is drawn under the cursor. Dropping an entity from one project into
a scene from another is refused by TumbleTrove, since the resolver serves
one project at a time.
