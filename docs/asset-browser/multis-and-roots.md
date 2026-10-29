# Multis and Roots

Two kinds of container sit beside shots and assets in the browser. They
answer different questions:

- A **Multi** is *one workfile for several shots (or several assets)*. Ten
  shots of a sequence join a `light` Multi and a lighter works all ten in
  one hip. A Multi changes where workfiles live and nothing else; it adds
  nothing to USD composition.
- A **Root** is *a scene assembled from the configuration*: a list of
  assets, exported as one USD layer, that shots pick up as their root layer.
  A Root has no workfile.

The concepts are described in
[Configuration → Multis](../configuration.md#multis-multishot-workfiles) and
[Composition → Two ways to build multi-asset environments](../composition.md#two-ways-to-build-multi-asset-environments).
This page is about operating them from the browser. Source:
[`containers.py`](../../python/tumblepipe/asset_browser/containers.py) and
the container branches of
[`catalog.py`](../../python/tumblepipe/asset_browser/catalog.py).

Both draw as **container cards**: a tinted body with a large icon and the
member count, no thumbnail. A single click selects the card; a
**double-click** drills into its members, with the container shown in the
breadcrumb. TumbleTrove's *Pipeline assets* page covers the generic card
behaviour.

## Multis

The [Entity Editor](entity-editor.md#multis) is the easiest place to set
Multis up: a **Multis** column on the Assets and Shots tabs for who belongs
where, and a **Multis** tab with a tick box per covered department. It
checks that no member's department is covered by two Multis and writes
everything on Apply. The browser surfaces below edit the same data one
Multi at a time, written at once.

### Where they are

Each project's **Assets** and **Shots** sections start with a **Multis**
subheader once that side has a Multi; a side with none has no subheader.
Click it for a grid of that context's Multis; click a Multi
underneath for the Multi's own card in list view, with one row per
department it covers. Multis are locked to one context: a shots Multi takes
shots only, an assets Multi assets only.

### Creating a Multi

**New Multi…** (the right-click menu of the Assets or Shots header or of a
Multis subheader, or the **+** card)
asks for a **Name** and a **Context** (`shots` or `assets`). From a Multis
subheader the context is that subheader's and is fixed, shown in the title
as **New Multi in assets**. Under Assets or Shots it starts on that context;
elsewhere it starts on `shots`. Status: *Created Multi: seqA (shots, covers 9 departments)*. A new
Multi **covers every department in its context's pool** and starts with no
members. If it is meant for fewer departments, trim the coverage next.

### Coverage: which departments the Multi owns

A Multi's department list is its *coverage*: for those departments, every
member's workfile *is* the Multi's workfile. Three surfaces edit it, all
writing the same list:

| Where | Label | Dialog |
|---|---|---|
| Multi card or sidebar row right-click, or the detail panel's **Edit…** | **Edit Multi…** | **Edit *name***, a **Departments** multi-select and, on a shots Multi, the **Timeline** ([Shared timeline](#shared-timeline)); **OK** applies. |
| Multi card right-click | **Departments…** | **Departments — *name***: one row per department in the pool with a checkbox; each toggle writes immediately, **Close** dismisses. |

What coverage does to a member, as soon as it is written:

- The member's deck item and list row for that department show **ⓜ** on
  an orange tint instead of a version, with the tooltip *Dept — covered by
  Multi: name. Double-click to open the Multi's workfile*. Opening the row
  opens the Multi's hip, and creating a workfile from it (**New from
  Template**, **New from Current**) writes the next version of the Multi's
  workfile, recorded as the Multi's.
- The member's own workfiles for that department, if any exist, stay on
  disk and stop being what the row opens. Uncover the department and they
  are back.
- The row's right-click menu gains **Remove from *name***, which removes
  the member from the Multi entirely (coverage is per Multi, not per
  member-department).

Departments the Multi does not cover keep the member's own workfiles.

### Members

Add shots or assets to a Multi by:

- dragging cards onto the Multi's row in the sidebar (the drag label reads
  **Add to *name***);
- right-clicking a card → **Add to Multi ▸** → the Multi;
- selecting cards, then right-clicking the Multi's row → **Add selected to
  Multi**.

The status bar reports *Added 3*, or *Added 2 of 3 — 1 skipped (group
accepts shots only)* when the wrong kind of entity was in the batch. Remove
with **Remove selected from Multi** on the row, **Remove from Multi:
*name*** on the member's card, or **Remove from *name*** on a ⓜ row.

Double-click the Multi card to see its members. A member's card also lists
the Multi on the detail panel's Departments tab as an `M` pill.

Renaming a member (in the browser or the Entity Editor) renames it in its
Multis too, keeping its place on a shared timeline, and deleting or retiring
it takes it out of them and off the timeline.

### The Multi's workfiles

The Multi's own rows work like a shot's, with the same right-click menu
([Asset Browser → Deck items](index.md)). A row with a workfile opens it
(**Open Latest**, or an older one from **Open Version ▸**), **Open Folder**
opens `<project>/groups/<context>/<name>/<department>/`, and a row without
one reads *missing* until **New from Template** (or a double-click on the
row) creates `v0001` there, named `<name>_<department>_v0001.hip`. The
template runs its group branch, which lays out one pinned graph per member
in columns
([Configuration → Department templates](../configuration.md#department-templates)).
A Multi's exports are written per member, so **View Latest Export ▸** lists
the members and opens the chosen one's export folder. The detail panel's
**Departments** rows leave it out; use the deck item's menu or the member's
own card.

**New from Current** on a Multi's row needs a Multi's workfile open, for
example to start the Multi's `anim` from its `layout`. With a shot's or
asset's scene open it is refused with *New from Current on a Multi needs a
Multi's workfile open; use New from Template to start one*. That scene's
nodes are pinned to its own entity, and in a Multi workfile they would
resolve to nothing.

Only a workfile created from the Multi's own rows is a Multi workfile. A
multi-shot scene built in a *shot's* department — even a shot named after
the Multi — belongs to that shot, and exporting from it finds no tasks for
the member shots; see
[Troubleshooting](../troubleshooting.md#export-says-no-export-tasks-found-for-the-current-context).

Nodes added to a Multi workfile by hand need their **Entity** set to the
member they work on: `from_context` resolves to nothing there, because the
workfile belongs to the Multi rather than to one member (see
[Entity and Department default to `from_context`](../nodes/index.md#entity-and-department-default-to-from_context)).
The per-member graphs the template lays out are already pinned that way.

Once a Multi workfile is open, **Save** versions the Multi's workfile, and
the Multi's own card marks that department as the open one — in its deck
row and in its detail panel's **Departments** section. Publishing from it
publishes each member's layer under that member's own entity; the Multi
itself never composes anything. **Render** does the same fan-out: the
[Farm Submit dialog](submit-jobs.md#opening-it) opens with the members' Render
cells ticked, never the Multi.

**Open in New Houdini**, on the Multi's row or on a member's ⓜ row, opens
the Multi's newest workfile in a second Houdini (see
[Workfiles](workfiles.md#open-in-new-houdini)).

### Shared timeline

By default a shots Multi keeps every member on its own frames: the
template's graph per member runs over that shot's range, so ten shots that
all start at 1001 overlap in the Multi's hip. That is right for ten
separate graphs, and wrong for **one graph that flows through all of
them**: a simulation that carries on from shot to shot, a crowd that keeps
walking across a cut. For that, set **Edit Multi…** › **Timeline**:

| Timeline | What the Multi's workfile gets |
|---|---|
| **Per shot** (default) | Every member on its own frames, as before |
| **Shared** | The members end to end on one timeline, in member order |
| **Shared, with bookmarks** | The same, plus a playbar bookmark per shot |

On a shared timeline each member has a **start**, the frame its own first
frame lands on. sh010 (1001-1050) and sh020 (1001-1040) become 1001-1050 and
1051-1090: sh020 has an offset of +50. Then, in the Multi's workfile:

- `th::import_shot` moves each shot's layers forward by its offset, so the
  shared graph sees sh020's camera and animation at 1051-1090.
- `th::export_layer` cooks sh020's export at 1051-1090 and moves the written
  samples back to 1001-1040, so what is published is an ordinary sh020
  layer. Nothing downstream knows the shared timeline existed. The
  published `context.json` records the offset (`parameters.time_offset`).
- The playbar spans every shot. With bookmarks, each shot has a bookmark
  named after it (`sq010_sh020`) on its shared frames: **double-click** one
  to zoom the playbar to that shot, double-click again to zoom back out.

Both nodes read the offset through their **Time Offset** parm, which
defaults to **From Multi**. Set it to **None** to keep one node on its
shot's own frames, or **Manual** for an offset of your own
([Import and export nodes](../nodes/import-and-export.md#thimport_shot-lop)).
In any workfile that is not a shared-timeline Multi, From Multi is no
offset, so existing workfiles are unchanged.

What to know:

- **Starts are stored, not recomputed** (`timeline_starts` on the Multi in
  `_config/db/groups.json`). Members are placed when shared timeline is
  switched on and when they join, after the last frame any placed shot
  reaches. If a shot's length changes later, the shots after it keep their
  frames: moving them would move work already made against them. A longer
  shot can then overlap the next one.
- A member that leaves takes its start with it; nobody else moves. Back to
  **Per shot** keeps the starts, so switching shared on again puts every shot
  back where it was.
- Rolls are not counted in the layout: neighbouring shots' rolls overlap, as
  continuous action does. The playbar includes the first shot's pre-roll and
  the last shot's post-roll.
- A member with no frame range authored is not placed and keeps offset 0.
- The bookmarks are Houdini's own, marked temporary: they are not saved in
  the hip, and are remade from the configuration whenever the workfile opens
  or the Timeline changes while it is open. Dragging one does not move a
  shot. Bookmarks of your own are never touched.
- Only `import_shot` and `export_layer` read the offset. Other nodes that
  work on one shot's frames (`import_layer`, playblasts, caches) still use
  the shot's own frames; set them by hand inside a shared-timeline Multi.

### Open Location and Delete

**Open Location** on the card or the sidebar row opens
`<project>/groups/<context>/<name>` in Explorer, when the folder exists.

**Delete Multi** (card, row, or the detail panel's **Delete**) asks *Delete
Multi 'name'? This cannot be undone from the browser.* and removes the Multi
from the project configuration (`_config/db/groups.json`). Its workfiles on
disk are left where they are; the members' ⓜ rows revert to their own
workfiles.

## Roots

### Where they are

The **Roots** section lists every Root in the project with its asset count.
A Root's card shows the count and drills into its member assets on
double-click. Roots are stored at `scenes:/<path>` in
`_config/db/scenes.json`; a nested name like `outdoor/forest` creates the
parent `outdoor` as well, and a child Root inherits its parents' assets at
export time.

Shots do not appear in a Root's member list. A shot *uses* a Root through
its own `scene` property (its **Root**, shown on the shot's Info tab as
`outdoor/forest`, with *(inherited)* when the sequence set it rather than
the shot). Give a whole sequence a Root on its row in the
[Entity Editor](entity-editor.md#sequence-and-category-rows): every shot in
it without a Root of its own then uses that one.

### Creating a Root

**New Root…** (the Roots header's right-click menu, or the **+** card) asks
for a **Name**. Status: *Created Root: forest*. It starts empty.

**Create Root from Items…** on the shelf's **⋯** menu asks the same, then
adds every pipeline asset on the shelf to the new Root. Status: *Created with
N assets*, noting how many shelf items came from other catalogs.

### Members

Add assets the same three ways as a Multi: drag onto the Root's sidebar row,
**Add to Root ▸** on a card, or **Add selected to Root** on the row.
Each new asset joins with one instance on the default channel; an asset
already listed is skipped (*already in Root*).

Dropping a **shot** on a Root does not add it to the list: it sets that
shot's Root to this one. Removing a shot (**Remove selected from Root**, or **Remove from Root: *name*** on the shot's card) clears the
shot's Root only when the shot itself points at this Root; a shot whose
Root is inherited from its sequence still lists the entry but is skipped
(*Skipped 1*). **Clear Root** on the shot's card appears only for a Root
set directly on the shot, and clears it.

**Edit Root…** (card, or the detail panel's **Edit…**) opens **Edit
*name*** with an **Assets** multi-select of every asset in the project,
labelled `CATEGORY/name` and ticked where the Root holds them. Untick to
remove, tick to add; assets the Root already held keep their instance count
and channel. Status: *Updated forest*.

Membership edits write the configuration, then bring the shots up to
date: the Root is exported, a shot whose Root changed gets a new root
layer, and a shot with no staged build yet is built. The status bar says
so, e.g. *Added 2; staged Root forest; updated 1 shot*. Shots without a
Root are not touched. The Entity Editor's Apply and **New from Template**
do the same, so a layout workfile opened next imports the Root's assets.

A Root's assets belong to the Root: departments can move and edit them,
but no export claims them, and an export that imports one again through
its own import node is refused. See
[Composition → A shot's Root assets belong to the Root](../composition.md#a-shots-root-assets-belong-to-the-root).

### The drift dot

A Root card carries a small orange dot when its asset list (asset,
instances, channel) differs from what its latest export recorded in
`context.json`, or when it has members and has never been exported. It
means the shots that use this Root are composing an older asset list.
Root edits made in the browser or the Entity Editor export the Root
themselves, so a dot that stays means that export failed (the Python
Shell says why) or the Root was edited elsewhere (the Database Editor, a
script). **Export Root USD** clears it.

### Root actions

Right-click a Root card, its list row, or its sidebar row:

| Action | What it does |
|---|---|
| **Open Root** | Drills into the Root's members, like a double-click. |
| **Open Location** | Opens `<project>/export/scenes/<path>/_staged` in Explorer, or its parent. A Root that has never been exported has no folder, and nothing opens. |
| **Stage Root & Rebuild Shots…** | **Export Root USD** followed by **Rebuild Assigned Shots…**, behind one confirmation (**Stage Root & Rebuild N shots**, button **Stage & Build**) that lists the shots. Status: *Staged Root 'forest'; rebuilt 3/3 shots*, with *— 1 failed (see Python Shell)* when a rebuild failed. |
| **Export Root USD** | Writes the next `<project>/export/scenes/<path>/_staged/vNNNN/<name>_vNNNN.usda`, a layer that sublayers each member asset's staged file (strongest first) and then the parent Roots' layers, plus a `context.json` recording the asset list. Status: *Exported Root USD: forest*. |
| **Rebuild Assigned Shots…** | For every shot whose Root is this one: regenerates the shot's root layer at `<project>/export/shots/<seq>/<shot>/root/vNNNN/<shot>_root_vNNNN.usda` (the Root's layer plus the root defaults), then builds the shot's staged USD locally over its full frame range. Confirmation **Rebuild N shots** (button **Rebuild N**) lists the shots and warns that each is a local Houdini build. Status: *Rebuilt 3/3 shots*. With no assigned shots: *No shots assigned to forest*. |
| **Edit Root…** | See Members above. |
| **Delete Root** | *Delete Root 'forest'? This cannot be undone from the browser.* Removes the Root from the configuration; exported layers stay on disk. |

These actions are the by-hand fallback for the staging Root edits now do
themselves. The two build actions run in this Houdini session and block it while they
work; the status bar reports the outcome, and per-shot failures are logged
(see [Development → Where the logs are](../development.md#where-the-logs-are)).

### Using a Root in a scene

Drag a Root card into a LOP network for a stock `sublayer` node whose file
path is the Root's `entity:/scenes/<path>` URI; the resolver maps it to the
latest exported Root layer, so the node follows re-exports without editing.
Status: *Sublayered Root: forest*. Roots can only be dropped into LOP
networks.
