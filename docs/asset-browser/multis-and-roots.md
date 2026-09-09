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

### Where they are

Each project's **Assets** and **Shots** sections start with a **Multis**
subheader. Click it for a grid of that context's Multis; click a Multi
underneath for the Multi's own card in list view, with one row per
department it covers. Multis are locked to one context: a shots Multi takes
shots only, an assets Multi assets only.

### Creating a Multi

**New Multi…** (the Multis subheader's right-click menu, or the **+** card)
asks for a **Name** and a **Context** (`shots` or `assets`; `shots` is the
default). Status: *Created Multi: seqA (shots, covers 9 departments)*. A new
Multi **covers every department in its context's pool** and starts with no
members. If it is meant for fewer departments, trim the coverage next.

### Coverage: which departments the Multi owns

A Multi's department list is its *coverage*: for those departments, every
member's workfile *is* the Multi's workfile. Three surfaces edit it, all
writing the same list:

| Where | Label | Dialog |
|---|---|---|
| Multi card right-click, or the detail panel's **Edit…** | **Edit Multi…** | **Edit *name***, a **Departments** multi-select; **OK** applies. |
| Multi row in the sidebar | **Edit Departments…** | the same dialog |
| Multi card right-click | **Departments…** | **Departments — *name***: one row per department in the pool with a checkbox; each toggle writes immediately, **Close** dismisses. |

What coverage does to a member, as soon as it is written:

- The member's deck item and list row for that department show **ⓜ** on
  an orange tint instead of a version, with the tooltip *Dept — covered by
  Multi: name. Double-click to open the Multi's workfile*. Opening the row
  opens the Multi's hip.
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
- selecting cards, then right-clicking the Multi's row → **Add selected
  assets to Multi**.

The status bar reports *Added 3*, or *Added 2 of 3 — 1 skipped (group
accepts shots only)* when the wrong kind of entity was in the batch. Remove
with **Remove selected assets from Multi** on the row, **Remove from Multi:
*name*** on the member's card, or **Remove from *name*** on a ⓜ row.

Double-click the Multi card to see its members. A member's card also lists
the Multi on the detail panel's Departments tab as an `M` pill.

### The Multi's workfiles

The Multi's own rows work like a shot's. A row with a workfile opens it
(**Open Latest**), **Open Location** opens
`<project>/groups/<context>/<name>/<department>/`, and a row without one
reads *missing* until **New: Template** creates `v0001` there, named
`<name>_<department>_v0001.hip`. The template runs its group branch, which
lays out one pinned graph per member in columns
([Configuration → Department templates](../configuration.md#department-templates)).
The Multi's rows do not offer **New: Current**.

Once a Multi workfile is open, **Save** versions the Multi's workfile, and
the Multi's own card marks that department as the open one — in its deck
row and in its detail panel's **Departments** section. Publishing from it
publishes each member's layer under that member's own entity; the Multi
itself never composes anything.

The browser's members-list open is Multi-aware, but **Open in New Houdini**
on a member's row is not: it opens the newest file in the member's own
department folder (see [Workfiles](workfiles.md#open-in-new-houdini)).

### Open Location and Delete

**Open Location** on the card opens `<project>/groups/<context>/<name>` in
Explorer, when the folder exists.

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
the shot).

### Creating a Root

**New Root…** (the Roots header's right-click menu, or the **+** card) asks
for a **Name**. Status: *Created Root: forest*. It starts empty.

### Members

Add assets the same three ways as a Multi: drag onto the Root's sidebar row,
**Add to Root ▸** on a card, or **Add selected assets to Root** on the row.
Each new asset joins with one instance on the default channel; an asset
already listed is skipped (*already in Root*).

Dropping a **shot** on a Root does not add it to the list: it sets that
shot's Root to this one. Removing a shot (**Remove selected assets from
Root**, or **Remove from Root: *name*** on the shot's card) clears the
shot's Root only when the shot itself points at this Root; a shot whose
Root is inherited from its sequence still lists the entry but is skipped
(*Skipped 1*). **Clear Root** on the shot's card appears only for a Root
set directly on the shot, and clears it.

**Edit Root…** (card, or the detail panel's **Edit…**) opens **Edit
*name*** with an **Assets** multi-select of every asset in the project,
labelled `CATEGORY/name` and ticked where the Root holds them. Untick to
remove, tick to add; assets the Root already held keep their instance count
and channel. Status: *Updated forest*.

Membership edits write the configuration immediately and nothing else: no
USD is produced until you export.

### The drift dot

A Root card carries a small orange dot when its asset list (asset,
instances, channel) differs from what its latest export recorded in
`context.json`, or when it has members and has never been exported. It
means the shots that use this Root are composing an older asset list.
**Export Root USD** clears it.

### Root actions

Right-click a Root card:

| Action | What it does |
|---|---|
| **Open Root** | Drills into the Root's members, like a double-click. |
| **Open Location** | Opens `<project>/export/scenes/<path>/_staged` in Explorer, or its parent. A Root that has never been exported has no folder, and nothing opens. |
| **Stage Root & Rebuild Shots…** | **Export Root USD** followed by **Rebuild Assigned Shots…**, behind one confirmation (**Stage Root & Rebuild N shots**, button **Stage & Build**) that lists the shots. Status: *Staged Root 'forest'; rebuilt 3/3 shots*, with *— 1 failed (see Python Shell)* when a rebuild failed. |
| **Export Root USD** | Writes the next `<project>/export/scenes/<path>/_staged/vNNNN/<name>_vNNNN.usda`, a layer that sublayers each member asset's staged file (strongest first) and then the parent Roots' layers, plus a `context.json` recording the asset list. Status: *Exported Root USD: forest*. |
| **Rebuild Assigned Shots…** | For every shot whose Root is this one: regenerates the shot's root layer at `<project>/export/shots/<seq>/<shot>/root/vNNNN/<shot>_root_vNNNN.usda` (the Root's layer plus the root defaults), then builds the shot's staged USD locally over its full frame range. Confirmation **Rebuild N shots** (button **Rebuild N**) lists the shots and warns that each is a local Houdini build. Status: *Rebuilt 3/3 shots*. With no assigned shots: *No shots assigned to forest*. |
| **Edit Root…** | See Members above. |
| **Delete Root** | *Delete Root 'forest'? This cannot be undone from the browser.* Removes the Root from the configuration; exported layers stay on disk. |

The two build actions run in this Houdini session and block it while they
work; the status bar reports the outcome, and per-shot failures are logged
(see [Development → Where the logs are](../development.md#where-the-logs-are)).

### Using a Root in a scene

Drag a Root card into a LOP network for a stock `sublayer` node whose file
path is the Root's `entity:/scenes/<path>` URI; the resolver maps it to the
latest exported Root layer, so the node follows re-exports without editing.
Status: *Sublayered Root: forest*. Roots can only be dropped into LOP
networks.
