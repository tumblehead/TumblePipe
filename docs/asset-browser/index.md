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
[`python3.13libs/uiready.py`](../../python3.13libs/uiready.py) switches
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
| **Name**, **Project Path** (with **Browse…**), **Config Path** | The selected project's identity; Config Path is optional. Locked for the project TumbleTrove Desktop launched. |
| **Libraries** (**Add…** / **Remove**) | The selected project's library folders, after its `TH_LIBRARY_PATH` ones (greyed out). See [Libraries](libraries.md). |
| **Project defaults ▸ Department pool** | The selected project's department pool, shared by everyone on it; owners and admins edit, everyone else reads. See [The department pool editor](settings.md#the-department-pool-editor). |
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

Beside the projects, once for the whole catalog:

| Section | Rows underneath | Notes |
|---|---|---|
| ***Library name*** | **Recipes** (a row per context) and **Texture Sets**, per library the projects list | See [Libraries](libraries.md). |
| **Nodes** | one row per TAB submenu that ships a node: **Pipeline**, **Model**, **Lookdev**, **Lighting**, **Rendering**, **Comp**, **Utils**, **Debug** | See [Finding and creating nodes](#finding-and-creating-nodes). |

### Finding and creating nodes

The **Nodes** section lists every `th::` node this package ships, one card per
node, filed by its TAB submenu (`_TumblePipe/pipeline` is the **Pipeline**
row). It is how you find `import_shot` or `export_layer` without knowing
where they sit in the TAB menu: search matches the node's name, type name,
context and row. Nodes come with the package, not the project, so the section
shows the same nodes in every project and a project filter never hides them.

A card shows the node itself, drawn as the network editor draws it (colour,
input and output dots, icon). Nodes whose icon is one of TumblePipe's
illustrations show the illustration instead. A name shipped in two contexts
gets two cards, e.g. **Cache (LOP)** and **Cache (SOP)**.

To create one, drag the card onto a network editor, or pick **Create Node**
on the card's right-click menu or the detail panel. See
[Dragging into Houdini](#dragging-into-houdini) for where it lands. **Copy Node
Type** puts the type name (`th::import_shot::1.0`) on the clipboard.

Listed: the operators in [`hpm.toml`](../../hpm.toml) `[[operators]]` that are
loaded in this session and not hidden from the TAB menu. The five `Data`
config HDAs (`th_configure_*`) are left out; they are not placed in a network.

The sidebar has no Todos section; an entity's tasks live on its card's
**Tasks…** menu item and on the detail panel's **Tasks** tab.

Right-click menus on sidebar rows. Each starts with the creation options the
**+** card offers when that row is selected (see
[Creating entities](#creating-entities)); the table lists what follows them.

| Row | Creation options | Then |
|---|---|---|
| **Assets** header, a category | **New Asset...**, **Entity Editor...**, **New Category...**, **New Multi...**, **New Root...** | a category adds **Delete category '*category*'…** |
| **Shots** header, a sequence | **New Shot...**, **Entity Editor...**, **New Sequence...**, **Import Shots from CSV...**, **New Multi...**, **New Root...**; a sequence leads with **Play in Cinedex** (all its shots in the editor) | a sequence adds **Delete sequence '*sequence*'…** |
| **Multis** subheader, a Multi | **New Multi...** | a Multi adds the Multi card's menu, plus **Add selected to Multi** and **Remove selected from Multi** |
| **Roots** header | **New Root...** | |
| a Root | **New Asset...**, **New Shot...**, **Entity Editor...**, **New Recipe...**, **New Multi...**, **New Root...** | the Root card's menu, plus **Add selected to Root** and **Remove selected from Root** |
| **Recipes** header, a context row | **New Recipe...** | |
| **Nodes** header, a submenu row | none | |

A Multi or Root row's menu is its card's menu, from **Open** to **Delete**,
Root export actions included (see
[Multis and Roots](multis-and-roots.md#root-actions)).

The add/remove entries act on the grid's current selection and are greyed
out without one. Multi and Root rows also accept dragged cards.

## Cards

An asset card is named after the asset; when the view mixes categories (the
Assets root, a Root's members, the *All* view) the name is prefixed with its
category, `CHAR/Baby`, so same-named assets in different categories stay
apart. Inside one category the bare name is shown. Shot cards work the same
way: `SH0030` inside its sequence, `Seq_0010/SH0030` where sequences mix
(the Shots root, a Root's members, the *All* view).

The card whose workfile is currently open in Houdini is sorted first and
drawn with a highlight border.

**Thumbnail.** Read from a `thumbnail.png` sidecar in the entity's folder
(`<project>/assets/<category>/<name>/thumbnail.png` or
`<project>/shots/<sequence>/<shot>/thumbnail.png`,
[`thumbnails.py`](../../python/tumblepipe/asset_browser/thumbnails.py)).
Without one the card shows the placeholder icon. Two right-click items write
it:

- **Select Thumbnail…** opens a **Select Thumbnail** file dialog
  (`png jpg jpeg tif tiff bmp exr`) and saves the picked image as PNG.
- **Capture Thumbnail** flipbooks the current frame of the active Scene
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

**Other Houdini sessions on this machine.** A card follows what your other
open sessions do, without a Refresh. Save a workfile version or finish an
export in one Houdini, switch to another, and its browser has rebuilt that
card within about a second. A config edit made in another session (an
entity created, renamed or deleted, a Multi changed) refreshes the whole
browser the same way. Each session writes a line to a small journal on
local disk (`%LOCALAPPDATA%\tumblepipe\events\changes.jsonl` on Windows,
`~/Library/Application Support/tumblepipe/events/` on macOS,
`~/.local/state/tumblepipe/events/` on Linux,
[`util/events.py`](../../python/tumblepipe/util/events.py)), and every open
browser reads it twice a second. Changes made on *other machines* (another
artist, a farm export) are not in it: press **Refresh** to see those.

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
| a no-entry cursor when dragged | the department is published from SOPs (rig), so there is no USD layer to drop into a LOP network (TumbleTrove 0.50+) |
| *(off)* after the label, greyed | the department is [turned off on this shot](entity-editor.md#departments-turned-off-on-a-shot): its workfiles still open, nothing new is made for it |

Labels are the department's `short` from the pool when it has one,
otherwise `Anim`, `Blend`, `Comp`, `Enviro` or the title-cased name.

Double-click an item to open that department's latest workfile. On an item
with no workfile yet (it reads *missing*), a double-click creates the first
version from the department template instead. Right-click one for:

| Item has versions | Item has none |
|---|---|
| **Open Latest (vNNNN)** | **New from Template** |
| **Open Version ▸** (older versions, newest first) | **New from Current** |
| **Open Folder** | |
| **View Latest Export** | **Open Folder** |
| **Open in New Houdini** | |
| **Reload Scene** (only on the open department) | |
| **Sync with Multi** (only on a Multi's open department) | |
| **Remove from *Multi*** (only on a ⓜ item) | |
| **New from Template** | |
| **New from Current** | |
| **Disable department on this shot** / **Enable department** (shots) | **Disable department on this shot** / **Enable department** (shots) |

The item an artist almost always wants comes first: creating the first
version on an empty item, opening the latest on one that has versions. What
each does is on the [Workfiles](workfiles.md) page. A Multi's own deck items
offer the same menu, except that **View Latest Export ▸** lists the Multi's
members, each opening that member's export (see
[Multis and Roots](multis-and-roots.md#the-multis-workfiles)), and the open
one adds **Sync with Multi** (see
[Keeping the workfile and the Multi in step](multis-and-roots.md#keeping-the-workfile-and-the-multi-in-step)).

**Disable department on this shot** turns the department off on that shot
(the same switch as the Entity Editor's **Departments off** column, see
[Departments turned off on a shot](entity-editor.md#departments-turned-off-on-a-shot)):
its layers leave the shot's staged build at the next build, Farm Submit and
the publish leave it alone, and **New from Template** / **New from Current**
on it are refused with a message. Nothing is deleted; **Enable department**
turns it back on.

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

Sort options: **Name A-Z**, **Name Z-A**, **Latest Update**, **Oldest
Update**.

The **Edits** section has columns of its own, since an edit has no category
or departments (on a TumbleTrove newer than 0.59.0, which asks a catalog for
each section's columns; an older one shows the columns above everywhere):

| Column | Edit row |
|---|---|
| **Name** | edit name |
| **Published** | latest published version |
| **Working** | newest working version |
| **Master** | ✓ for the project's master edit |
| **User** | who saved or published it last |
| **Edited** | when, as `2h ago` |

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
  A department turned off on the shot reads *(off)*, greyed, and its play
  button cannot create a first version.
- **Tasks**: the todo list with an **Add task…** field and a clear menu
  (**Clear completed** / **Clear all**).
- A bottom action bar: **Import to Scene** (assets only; creates a
  `th::import_asset` in `/stage`), **Open Folder** (the entity's folder, as
  on the card), **Open Export Folder**, **Open in Entity Editor…**, **Open
  in Database Editor…**, **Edit…** and **Delete**.

A Multi card gets **Info** and **Departments** (its coverage rows), a Root
card **Info** alone, and both an action bar of **Edit…** and **Delete**.

## Quick actions

The toolbar's quick-action buttons come from the catalog. The label beside
them is the open `.hip`'s filename.

| Button | Tooltip | Does |
|---|---|---|
| **Open Timeline** | Play the selected shot's sequence in Cinedex, or the sequence you are viewing; with neither, the project's master edit, else every shot | Starts [Cinedex](play-shots.md) from the toolbar. It is the bar's wide accent-coloured button, first in the row (it shrinks to an icon before anything goes into the overflow menu), and is greyed out with the reason in its hover when Cinedex cannot open (not installed, no OCIO config); this needs TumbleTrove 0.60.0, an older one draws it as a plain square button that explains the refusal on the status line when clicked. It plays a whole sequence: the sequence of the shot whose card the right pane shows, else the sequence the view is narrowed to (its sidebar row), every shot of it in name order, exactly as that sequence's **Play in Cinedex** does. With no shot selected and no sequence in view (or an asset selected) it opens the project's master edit, else an untitled edit of every shot. The project is the selected card's, else the view's, else the open scene's, else the only registered one. To play just one shot, or a hand-picked set, right-click the card(s) and choose **Play in Cinedex** / **Play 3 Shots in Cinedex**. The status line says *Opened Cinedex on sequence ...* or *... on the project's timeline*; a sequence with no shots says so there and opens nothing; a failure (no Cinedex installed, no OCIO config, which project is unknown) shows there too, in red, and the button's hover says when Cinedex is not installed. |
| **Save** | Save current scene | Saves the open scene as the **next version** of its own department. Asks for a note first. See [Workfiles → Saving](workfiles.md#saving-a-new-version). |
| **Publish** | Publish exports | Opens the **Publish** process dialog for the open scene's entity, with every export task listed and a choice of where to run them: in this session, on this machine in the background, or on the render farm. |
| **Render** | Farm Submit, with the current scene's Render ticked: publish, playblast and render any shots or assets | Opens the [Farm Submit](submit-jobs.md) dialog with the scene's shot or asset's **Render** cell ticked (for a Multi workfile, its member shots'), and the preview cut pinned to the open workfile's department: tick publish, playblast and render cells for any number of shots or assets and submit them in the background. With no workfile open it opens with nothing ticked. |
| **Entity Editor** | The project's assets and shots as spreadsheets: add, edit and retire them | Opens the [Entity Editor](entity-editor.md) of the open workfile's project, on its shot or asset's tab and group with its row selected, or on **Multis** in a Multi's workfile (on **Shots** with no workfile open). |
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

An asset's or shot's card (and its row in the list view) opens the same menu
whatever you clicked, in the same order. TumbleTrove's own entries come
first: favourites, collections, **Add to Multi ▸**, **Add to Root ▸**, then
**Open Folder**, which opens the entity's folder (where its
`description.txt` and `thumbnail.png` live). The catalog's entries follow
([`get_card_menu_items`](../../python/tumblepipe/asset_browser/catalog.py)),
in sections with a line between each (TumbleTrove 0.57+; older builds show
them as one list), and TumbleTrove ends with **Refresh Thumbnail** and
**Delete asset…** / **Delete shot…**:

| Section | Item | What it does |
|---|---|---|
| Play | **Play in Cinedex** / **Play N Shots in Cinedex** (shots) | Plays the shot, or the selected shots back to back, in [Cinedex](play-shots.md). Always the first catalog entry, with **Submit Jobs…** right after it. To open a workfile, click its department tile on the card. |
| Farm | **Submit Jobs…** / **Submit Jobs for N selected…** | Opens the [Farm Submit](submit-jobs.md) dialog with this entity's **Render** cell ticked, or every selected card's of the same kind (shots with shots, assets with assets). |
| | **Generate Master…** | Merges each department's latest workfile into `<entity folder>/master/<name>_master.hip`, one network box per department, after a **Generate Master Scene** confirmation. Opens that scene. |
| | **Duplicate…** (assets) | Asks for a name (default `<name>Copy`, letters and digits only) and creates that asset in the same category with the source's own properties. In a background hython it then copies each department's latest finished workfile to the new asset as its `v0001`, with the new asset's `context.json`. Nodes pinned to the source entity switch to `from_context`, and the source's prim path typed into string parms (`/CHAR/Pengo/mtl/`) becomes the copy's (`/CHAR/PengoCopy/mtl/`). A department whose workfile belongs to a Multi is not copied. Nothing is published: publish the copy from its own workfiles. Your open scene is not touched. Use this instead of copying an asset's folder by hand, which leaves the copy pointing at the original. |
| Editors | **Open in Entity Editor…** | Opens the [Entity Editor](entity-editor.md) on the entity's tab and category or sequence, with it selected. The toolbar's **Entity Editor** button opens it on the open scene's entity. |
| | **Open in Database Editor…** | Opens the **Database Editor** window on this entity. |
| The entity | **Edit Description…** | Edits the `description.txt` sidecar in the entity's folder (dialog **Edit Description**). Shown on the Info tab. |
| | **Tasks…** | Opens TumbleTrove's **Tasks — *name*** dialog for the entity's todo list. |
| | **Departments…** | Which departments this entity uses (**Departments — *name***, tick boxes, **Use all (inherit)**, **Apply**); see [Configuration → Per-entity assignment](../configuration.md#per-entity-assignment). |
| | **Select Thumbnail…** / **Capture Thumbnail** | See [Cards](#cards). |
| Leaving | **Clear Root** | Shots only, and only when a Root is set directly on the shot (not inherited from the sequence). Clears it. |
| | **Remove from Multi: *name*** / **Remove from Root: *name*** | One entry per container the entity belongs to. |

A section with nothing in it is left out. A Multi's or Root's card has the
container menu instead (**Open Multi**, **Open Folder**, …; see
[Multis and Roots](multis-and-roots.md#play-open-folder-add-entity-and-delete)).
A recipe card has its own entries — **Edit Description…**, **Set Icon…**,
**Share to Library** / **Copy to Project**, **Delete Recipe** — see
[Recipes](recipes.md#card-menu-and-detail-actions), and TumbleTrove's
**Open Folder** opens the recipe's folder. A texture set card offers
**Share to Library** / **Copy to Project** — see
[Libraries](libraries.md#sharing-and-copying). An edit's card: see
[Play shots](play-shots.md).

## Creating entities

The dashed **+** card, the list view's **+** row, a right-click on empty
space in either view, and the sidebar menus above all offer the same list for
the same place
([`get_creation_options`](../../python/tumblepipe/asset_browser/catalog.py))
and lead to the same small forms (`get_creation_fields`). Under Assets (or a
category) the list is **New Asset...**, **Entity Editor...**, **New
Category...**, **New Multi...** and **New Root...**; under Shots (or a sequence) it is **New
Shot...**, **Entity Editor...**, **New Sequence...**, **Import Shots from
CSV...** ([the Entity Editor's page](entity-editor.md#apply) describes it),
**New Multi...** and **New Root...**, led on one sequence by **Play
Sequence** ([the editor](play-shots.md)). **New Multi...** chosen on a
category or sequence row offers to start the Multi with that whole category
or sequence. A
Multis subheader offers **New Multi...** alone, the Roots header **New
Root...** alone, the Recipes section **New Recipe...** alone, the Edits
section **New Edit...** alone, and anywhere unscoped offers **New Asset...**,
**New Shot...**, **Entity Editor...**, **New Recipe...**, **New Edit...**,
**New Multi...** and **New Root...** (a project's Edits section is there only
once it has an edit, so its first one is made from here or in the editor). Dragging selected nodes from a network editor
onto the grid also opens **New Recipe**. A
**Project** dropdown is added to every form when more than one project is
registered.

| Form | Fields | Notes |
|---|---|---|
| **New Asset** | **Name**; **Category** (dropdown of existing categories) | Started from a category row, the category is fixed. Refuses *Asset 'X' already exists in 'Y'.* |
| **New Shot** | **Name**; **Sequence** (dropdown); **Frame Start** `1001`; **Frame End** `1100` | Started from a sequence row, the sequence is fixed. |
| **New category** | **Category** (free text) | Creates an empty category. Refuses a name already used by a sequence. |
| **Entity Editor** | a window, not a form | Every asset and shot of the project as spreadsheets: add shots, assets, sequences and categories, retime, choose a shot's assets, set up Multis, rename, retire. See [The Entity Editor](entity-editor.md). |
| **New sequence** | **Sequence** (free text) | Creates an empty sequence. Refuses a name already used by a category. |
| **New Multi** | a window, not a form | The [Entity Editor](entity-editor.md#multis)'s Multis tab, asking for a **Name**, a shots Multi's **Timeline** and the departments, in the context it was chosen from (`shots` elsewhere). See [Multis and Roots](multis-and-roots.md#creating-a-multi). |
| **New Root** | **Name** | See [Multis and Roots](multis-and-roots.md#creating-a-root). |
| **New Recipe** | **Name**; **Description**; **Tags** | Saves the nodes selected in the network editor. See [Recipes](recipes.md#saving-a-recipe). |
| **New Edit** | **Name** | An edit with nothing saved yet: **Open in Editor** on its card opens it empty, for its first **Save**. Refuses a name the project has, in any case (*There is already an edit 'X'.*). See [Playing shots in the editor](play-shots.md). |

Names are written as typed; the new asset or shot is selected in the grid
once it appears. A form says what is wrong with a value under its field as
you type (a name with other characters, one already taken, a category named
like a sequence) and keeps **OK** off until it is right; a create that is
refused all the same reopens the form with what you typed (both need a
TumbleTrove newer than 0.59.0; on an older one the refusal is a message and
the form closes). A new asset, shot, category or sequence is named with
letters and digits only (`SH050`, not `SH_050`): names are joined with `_`
across the pipeline, so `PROP/old_tree` and `PROP_old/tree` would read the
same. Every way of creating or renaming one (these forms, **Duplicate…**,
the [Entity Editor](entity-editor.md), the [Database Editor](config-editor.md),
CSV import, scripts) refuses anything else with *Asset name 'X' may only
use letters and digits.* Nor can a name be one of the device names Windows
keeps in every folder, in any case: `CON`, `PRN`, `AUX`, `NUL`, `COM0` to
`COM9` and `LPT0` to `LPT9` (*Shot name 'Aux' is a device name Windows
reserves*), since no Windows machine can make or open a folder of that
name. `console` or `COM10` is fine. Department and channel names follow the
same device rule. A category or sequence named before this rule
(`Seq_0010`) keeps working and takes new, well-named entities. A category and a sequence can never share a name, so every
form that would create one (including a **New Shot** or **New Asset** whose
sequence or category does not exist yet) refuses with *'X' is already a
sequence; a category cannot share its name.* (or the reverse). A card's id
does not say whether its middle part is a category or a sequence, so the
browser works that out from the name; with both kinds sharing one, every
shot in the sequence opened as an asset that does not exist. A new shot stores its frame range on the shot entity; a new
asset stores only its name. Neither creates a workfile — that is the
department row's **New from Template**.

To add many assets or shots at once, or change many frame ranges, rolls,
fps, Roots or the assets shots import, use the
[Entity Editor](entity-editor.md).

## Editing and deleting entities

**Edit…** (detail panel action bar) opens **Edit *name***. For a shot it
shows **Project**, **Sequence** and **Name** read-only and lets you change
**Frame Start** and **Frame End**; only values you actually changed are
written, as the shot's own override, so an untouched field keeps following
the sequence. For an asset every field is read-only. Edit refuses an entity
that is not in the project configuration and writes nothing; an error
dialog reads *Editing 'name' failed* and names `EntityNotRegistered`. Up to TumblePipe 1.47.1 such an edit silently added
the entity.

**Delete** removes the entity from the project configuration after a
**Delete entity** confirmation (*Delete 'name'? This cannot be undone from
the browser.*). Its folders — workfiles, exports, renders, playblasts —
move to `_deleted/<date-time>/` under each storage root (for example
`<project>/_deleted/20261007-111122/assets/CHAR/GREEN`), so a new entity of
the same name starts empty and the old work can still be recovered by hand.
If a folder can't be moved (a file open in another program), nothing is
moved and nothing is deleted. **Delete category '…'…**
and **Delete sequence '…'…** refuse while the bucket still holds entities
(*Category 'X' still contains 3 assets.*) and confirm when it is empty.

An edit card's menu ends with **Rename Edit…** and **Delete Edit…**.
**Rename Edit…** asks for the new name (letters and digits, not one the
project has in any case); the master edit stays the master edit under its
new name. An edit that has saved or published versions on disk is refused,
as an asset with files is: its files are named after it, so the renamed edit
would come up empty. Open it in the editor and **Save As** under the new
name, then delete the old one. **Delete Edit…** refuses the master edit
(**Clear Master Edit**, or set another, first) and otherwise asks first; its
saved and published versions and rendered movies move to
`_deleted/<date-time>/` under their storage roots (`edits:/`, `export:/`,
`render:/`), as an entity's folders do, so they can still be recovered.
The Edits section, its count and the grid follow at once.

An edit card's thumbnail is the thumbnail of the shot its first clip plays
(the earliest shot clip on any video track of its newest saved, else latest
published, version); an edit with no shot clip, or whose shot has no
thumbnail, shows the film icon. **Refresh Thumbnail** picks up a changed cut.

### An edit on Houdini's timeline

**Show on Houdini Timeline** (an edit card's menu, inside Houdini, once the
edit has a saved or published version) and the TumblePipe shelf's **Import
Edit to Timeline** (which asks for the edit, the master edit first) lay the
edit's cut on the playbar, to check its ranges against the shots. Nothing is
written to the project.

- The global and playback range span the edit: its first frame sits at the
  edit's start frame (`frame_start`, inherited from the project: 1001 unless
  configured) and its last is the end of its last clip, video or audio.
- Each video clip gets a playbar bookmark spanning its cut-in to cut-out on
  the edit's timeline, named after its shot (`SQ010/SH030`) or, for a media
  clip, its library item. Slugs and gaps get none; clips on stacked tracks
  (a dissolve) overlap, and so do their bookmarks. A bookmark's comment
  names the track, the edit frames and the clip's source frames.
  Double-click a bookmark to zoom the playbar to that clip.
- It reads the edit's newest version: its newest saved working version,
  else its latest published one; the message names which.
- An edit cut at another fps than the scene's is converted to the scene's
  rate (frames scaled and rounded to the nearest frame, every clip at least
  one frame long), with a warning naming both rates.
- Running it again, for this or another edit, replaces the bookmarks it
  made; your own bookmarks and a Multi's shot bookmarks are left alone.
  Like a Multi's, the bookmarks are temporary: Houdini does not save them
  in the hip, so run it again after reopening the scene.

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
| department deck items (the deck popup; list-view rows do not drag) | a LOP network | one `th::import_layer` per department, wired and flagged (TumbleTrove's default path). A department published from SOPs (rig: a `.bgeo.sc`, no USD layer) does not drag (TumbleTrove 0.50+); on older TumbleTrove it is skipped on drop with a warning. Bring a rig in with `th::import_rig` in a SOP network |
| a node card (**Nodes** section) | a network of the node's context | the node at the cursor; dropped on an existing node, it is wired below it and takes its display flag. A SOP node dropped at object level gets a new Geometry node to live in. Another context is refused |
| several node cards | a network of their context | one node per card, side by side, in one undo step; if any card's context does not fit, none is created |

A single asset or shot card dropped into a LOP or SOP network also
pins the entity's `thumbnail.png` above the new node in the network
editor, saved with the hip. On `th::import_asset`, `th::import_shot` and
`th::import_model` the image follows a rename and goes away with the
node. `th::import_assets` has neither callback, so the image a *Combined
X into Y* drop pins stays behind when that node is renamed or deleted.
A multi-card drop adds no thumbnail.

The network editor shows a copy of the thumbnail at most 256 px across,
`network_thumbnail.png`, written beside `thumbnail.png` the first time it
is needed and again whenever the thumbnail is newer. Houdini reloads every
image in a network each time one is added or removed, on the main thread.
At full size (2048 px, on the project share) that froze Houdini for a few
seconds after each drop, longer for each asset already in the network.
Older scenes still name the full-size image; the next drop, delete or
rename in that network switches them over.

Any other pane: *Pipeline assets can only be imported into LOP or SOP
networks.* While you hover a network editor a ghost of the node to be
created is drawn under the cursor. Dropping an entity from one project into
a scene from another is refused by TumbleTrove, since the resolver serves
one project at a time.
