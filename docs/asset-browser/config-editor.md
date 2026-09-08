# The config database editor

The **Database Editor** edits a project's configuration databases — the JSON
files under `_config/db/` that define entities, their schemas, departments,
groups and project-wide settings — as a tree of entities on the left and the
selected entity's properties on the right. Source:
[`config_editor/`](../../python/tumblepipe/config_editor/). The
convention framework these files feed is described in
[Configuration](../configuration.md#the-convention-framework).

## Opening it

- Right-click a shot or asset card → **Open in Database Editor…**, or use
  the **Open in Database Editor…** action at the top of its detail panel.
  Both open the editor with that entity selected.
- Multi and Root cards have no database row and do not offer the action.

There is one editor window per session: opening it again raises the
existing one. The window is non-modal, titled **Database Editor**, and gains
a `*` suffix while it holds unsaved changes.

## Layout

| Pane | Contents |
|---|---|
| Left — the **URI** tree | One top-level node per *purpose* (database), expanding into the entities it holds, nested as in the file. |
| Right — the JSON pane | The selected entity's properties as an editable tree with **Structure**, **Value** and **Type** columns. Disabled until something is selected. |
| Bottom-right buttons | **Discard Changes** and **Save Changes**, enabled only while the pane is dirty. |

Switching entity or closing the window with unsaved changes asks
**Unsaved Changes** (*Save* / *Discard* / *Cancel*).

## The databases

Each purpose is one file, `_config/db/<purpose>.json`, holding a tree of
`{"properties": …, "children": …}` nodes. Selecting a purpose's root row
edits the root properties, which every entity below inherits. The project
template ships these:

| Purpose | URI form | Holds |
|---|---|---|
| `entity` | `entity:/shots/<sequence>/<shot>`, `entity:/assets/<category>/<asset>` | The shots and assets themselves and their properties (frame range, render and farm settings, channels, department assignment). The root carries project-wide defaults such as `farm.pools` / `farm.default_pool`; `entity:/shots` carries the render resolution. |
| `schemas` | `schemas:/entity/shots/sequence/shot`, `schemas:/entity/assets/category/asset`, … | The **schema** for each position in the other databases: the fields an entity at that depth has, with their defaults (`frame_start`, `fps`, `render.*`, `farm.*`, `variants`, `departments`, …). Also schemas for `departments`, `groups` and `config` entries. |
| `departments` | `departments:/shots/<name>`, `departments:/assets/<name>`, `departments:/render/<name>` | The department pools, in pipeline order, with `independent` / `publishable` / `renderable` flags. Normally edited through the [pool editor](settings.md#the-department-pool-editor). |
| `groups` | `groups:/shots/<name>`, `groups:/assets/<name>` | Multis — each with `members` and the `departments` it covers. |
| `scenes` | `scenes:/<name>` | Roots (config-driven scenes) and their asset lists. |
| `config` | `config:/project`, `config:/discord/…`, `config:/submission/…` | Project-wide settings: fps, the Discord token and its `users` / `channels` / `departments` maps, submission column presets. |
| `procedurals` | `procedurals:/assets/…`, `procedurals:/shots/…` | Procedural definitions (empty in the template). |

Right-clicking empty space in the tree offers **Add Purpose** — a new,
empty database that becomes `<purpose>.json` on save. A purpose row's own
menu offers **Add Entity** and **Remove Purpose** (confirmed). Purpose
labels cannot be renamed.

## Entities

### Adding

Right-click an entity (or a purpose) → **Add Entity**. On any row below a
purpose root this opens the **Add Entities** batch dialog:

- **Parent** shows the URI you are adding under; **Entity Type** is the
  child schema (a combo when the position allows several; changing it
  clears the rows after a **Change Entity Type** confirmation). With no
  schema at that position the dialog cannot create anything.
- **+ Add Row** / **- Remove Selected** manage a table with a **Name \***
  column, one column per schema field, and a **Status** column. Cells whose
  value fails the schema are highlighted; the status tooltip lists the
  errors.
- The status line reads `Enter entity names to create`, `N entities ready
  to create`, or `N entities ready, M with errors`.
- **Create** adds every valid row — schema defaults filled in for fields you
  left blank — under the parent, then reports **Some Entities Failed** if any
  row was rejected. Rows with errors are left out.

On a purpose root itself (`entity:/`) the plain **Add Entity** prompt asks
for a label instead.

### Renaming

Edit the row's label in place (double-click). **Enter** or clicking
anywhere else commits — exactly one change, persisted at once; **Escape**
cancels; a label already used by a sibling is rejected. Renaming moves the
entity's whole subtree to the new key.

### Removing

Right-click → **Remove Entity**, or select rows and press **Delete**. The
confirmation names the entity — and how many children go with it — as
**Remove Entity** / **Remove Entities**. Removal is written to disk
immediately.

### Reordering

Drag an entity onto a sibling to move it above or below it. Order is the
order in the file, which is what the browser and menus list.

## Properties

The JSON pane shows the selected node's own properties merged with what it
inherits from its ancestors and its schema:

| Colour | Meaning |
|---|---|
| Dark grey | **Inherited** — from a parent node or the schema default. Read-only; editing the value turns it into a local override. |
| Bright, bold | **Override** — a local value that differs from what is inherited. |
| Normal | **Local** — a field only this node defines. |

A changed field is prefixed `*`. Editing:

- Double-click a value to edit it; **Enter**, **Tab** or clicking anywhere
  else (another row, another widget, or the pane's empty background) commits,
  **Escape** cancels. Integer and float fields only accept numbers; an
  incomplete entry (empty, or a lone `-`) keeps the old value. Booleans edit
  as a checkbox.
- Double-click a key to rename it; the same commit rules apply, and a key
  already present on the same object is rejected.
- Right-click a field for **Change Type** (null, boolean, integer, float,
  string, array, object), **Remove Field**, and **Revert Changes** (back to
  the value the pane opened with — an added field is removed, an override
  goes back to inherited). Objects offer **Add Field**; arrays **Add Item**
  / **Remove Item** and **Move** (Up / Down / Top / Bottom); inherited fields
  have no menu.
- **Delete** on a selected field removes a local field, or reverts an
  override to its inherited value. Inherited fields cannot be deleted.

Nothing reaches disk until **Save Changes**. **Discard Changes** (confirmed:
*Discard Changes — Are you sure you want to discard all changes to
"<uri>"?*) restores the pane.

## Saving, and schema migration

**Save Changes** writes the whole purpose file back through the config
store, so other sessions pick it up on their next read. If the file changed
on disk since the editor loaded it and the change conflicts with yours, a
**Merge Conflict** dialog asks: *Save* keeps your version (discarding the
external edit), *Discard* reloads from disk and drops yours. Any other
write error reports **Save Error** — `Failed to save: …`.

Saving a **schema** (`schemas:/…`) whose fields you added or removed first
opens **Schema Migration** — *Schema changes detected for: <schema uri>*:

| Group | Meaning |
|---|---|
| **Fields to ADD (with defaults)** | Each new field, its default, and how many existing entities of that schema will receive it. |
| **Fields to REMOVE** | Each dropped field and how many entities have their own value for it, which would be deleted. |

**Apply Migration** saves the schema and updates every affected entity;
tick **Skip migration (save schema only, don't update entities)** to
change the schema alone; **Cancel** aborts the save. Entities inherit
schema defaults anyway, so a skipped addition still *reads* as the default
— the migration only matters for writing the field explicitly, and for
removals.

## What it writes

Only `_config/db/<purpose>.json` files — one rewrite of the whole file per
save, add, remove, rename or reorder. It never touches workfiles, exports or
the `_config/*.py` conventions, and a project's layout version
(`_config/version.json`) is untouched. Because the browser reads the same
files coherently, an edit shows in the Asset Browser on its next refresh.
