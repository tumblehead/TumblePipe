# The Entity Editor

The **Entity Editor** shows a project's assets and shots as two
spreadsheets, an **Assets** tab and a **Shots** tab, one row per entity,
and their [Multis](multis-and-roots.md#multis) on a third, **Multis** tab.
Add assets and shots, change frame ranges, rolls, fps, Animatable and
Roots, choose which assets each shot imports, set up Multis, rename and
retire, then **Apply**. The [Database Editor](config-editor.md) can change the same data
one raw property at a time; this is the friendly way. Source:
[`entity_editor.py`](../../python/tumblepipe/asset_browser/entity_editor.py)
(window) and [`entity_sheet.py`](../../python/tumblepipe/config/entity_sheet.py)
(model, checks and writes).

## Opening it

- The **Entity Editor** button on the toolbar, beside **Render**: opens
  the open workfile's project on its shot or asset, selected, or on the
  **Multis** tab with its Multi selected in a Multi's workfile (on Shots
  with no workfile open).
- **Entity Editor...** in the creation list of the **Assets** and **Shots**
  headers, a category or sequence row, the **+** card and the empty-space
  menus. From the Assets side it opens on the Assets tab, from the Shots
  side on the Shots tab; from a category or sequence, on that one.
- Right-click an asset or shot card → **Open in Entity Editor…**: opens on
  its tab and group with it selected. On a Multi card it opens the Multis
  tab with that Multi selected, as do the Multi's **Edit Multi…** and
  **Departments…**.
- **New Multi…** in the browser opens the Multis tab and asks for a new
  Multi there; see [Multis](#multis).

There is one editor per project. Opening it again raises the open one. It
is not modal, so the browser and Houdini stay usable beside it. A tab's
name, and the title, gain a `*` while there are edits not applied.

## The sheets

| Column | Tab | What it is |
|---|---|---|
| **Category** / **Sequence** | both | Fixed for a stored entity; editable on a new row (a new name makes a new category or sequence; so does **New Sequence…** / **New Category…**). |
| **Asset** / **Shot** | both | Editing it renames the entity on Apply. |
| **Status** | both | What Apply does with the row, or why it cannot. A long reason is cut short; its tooltip has all of it. |
| **Start**, **End** | both | The frame range (`frame_start`, `frame_end`). |
| **Length** | both | Frames from start to end. Typing a length moves the end frame. |
| **Pre-roll**, **Post-roll** | both | `roll_start`, `roll_end`: frames added before the start and after the end when rendering. |
| **FPS** | both | `fps`. Whole numbers are kept whole; `23.976` is fine. |
| **Animatable** | Assets | `animatable`, as a tick box. Typing `yes` or `no` works too. A shot's copies of an animatable asset, or of one with a rig published, are plain references rather than USD instances, so each copy can carry its own animation. An unticked asset with a rig reads **No, rigged**: its copies already animate, and ticking it also makes its own exports write every frame instead of the first. Copy leaves the note out. The import nodes stamp the answer (ticked, or a rig published) on the asset's prims as `animatable`, which Import Rigs' **Discover** reads ([`th::import_rigs`](../nodes/assets.md#thimport_rigs-sop)); a change shows up on a prim when its import node imports again. |
| **Root** | Shots | The shot's [Root](multis-and-roots.md), picked from a dropdown. **Inherit** puts it back to the sequence's, set on the [sequence's row](#sequence-and-category-rows). |
| **Multis** | both | The Multis the entity belongs to. Double-click to change them; see [Multis](#multis). |
| **Layers** | Shots | The shot's render layers, bottom to top: `characters: /CHAR/*; fx: /FX/*`. Double-click to edit them; see [Render layers](#render-layers). |
| **Departments off** | Shots | The departments turned off on the shot (`disabled_departments`): `effects, cfx`. Empty means every department of the pool is on. Double-click to tick them; see [Departments turned off on a shot](#departments-turned-off-on-a-shot). |
| **Assets** | Shots | What the shot imports: its Root's assets, then the Root's parents'. Empty for a shot with no Root. See [Which assets a shot imports](#which-assets-a-shot-imports). |

The dropdown above the sheet shows one category or sequence, or all of
them. **Filter** narrows the rows to names that contain the text.

The first two columns (the category or sequence, and the name) stay in view
while the others scroll sideways. **Shift+wheel** scrolls a table sideways,
as does the plain wheel when all its rows fit; a click on a cell that is
only partly in view selects it without scrolling it under the pointer.

### Sequence and category rows

Each sequence (or category) has a row of its own, shaded, above its shots
(or assets), which are listed under it. Its **Name** cell counts them. Its
value cells are the sequence's own values, which every shot in it inherits
unless the shot sets its own:

- Pick a **Root** on the sequence row and every shot in it with no Root of
  its own imports that Root's assets. The shots show it grey, as inherited,
  and so does their **Assets** cell. A shot that needs something else keeps
  (or gets) its own Root. **Add Shots…**' **Inherit from the sequence**
  entry names the Root the sequence gives.
- A frame range, rolls or fps set there works the same way, as does
  **Animatable** on a category row.

Typing, Delete, Copy, Revert, Undo and Apply work on a sequence row as on
a shot's; its **Status** says *Changed* while it has edits, or why a value
cannot be applied. A new sequence (one a new shot's **Group** names) gets
a row too, but its values wait until it has a name a sequence can have:
letters and digits, not the same as another sequence but for case, nor a
category's. A sequence's name, and which shots it holds, are not
edited here. Click a sequence's name to fold its shots away under it, and
again to show them. Filtering keeps each sequence row above its matching
shots.

### Inherited and set values

Values read the way the Database Editor shows them. An entity inherits
every value it does not set itself, from its category or sequence, then
**Assets** or **Shots**, then the project, then the project's defaults.

- **Grey** values are inherited. The tooltip says where from (*"5,
  inherited from sequence sq010"*).
- **Bright bold** values are set on the entity itself.
- **Typing** a different value sets it on the entity. Typing what an
  inheriting entity already inherits changes nothing, so it keeps
  following its parent.
- **Delete** (or **Reset to Inherited**) removes an entity's own value: it
  inherits again.
- A value an entity sets stays set when you type over it, even with what
  the parent says. Frame ranges are per-shot; only Delete un-sets.

**Tinted** cells hold edits that are not applied yet. New rows have green
names, a renamed entity's name is tinted, and an entity to be retired is
struck through. A selected cell has a light outline, so its tint still
shows, and the row of the cell you are on is faintly lit across the sheet so
it can be followed to the far columns. The line above the sheet shows each look on a sample cell; its
tooltip has the editing tips.

## Editing

- **Type into a selection.** Select cells (click, Shift/Ctrl-click, drag)
  and start typing. On Enter the value goes into every selected cell of
  that column, so selecting ten shots' **End** and typing `1100` sets all
  ten. Ticking one selected **Animatable** box ticks them all. Names change
  one row at a time.
- **Copy / Paste** (Ctrl+C / Ctrl+V). Copy puts the selected cells on the
  clipboard as tab-separated text. Paste writes one value into every
  selected cell, or a block of cells copied from a spreadsheet starting at
  the top-left selected cell. An empty pasted cell puts a value back to
  inherited. A block runs down the rows below, passing over a sequence's
  row rather than pasting into it. Lines that run past the last row are not
  added; use **Paste as New** for that.
- **Fill Down** (Ctrl+D) copies each column's top selected value into the
  selected cells below it (a sequence row's value can be the top one; it is
  never filled into).
- **Shift Frames…** (right-click) moves the selected rows' start and end by
  a number of frames, keeping their lengths.
- **Undo / Redo** (Ctrl+Z / Ctrl+Shift+Z or Ctrl+Y) step through every edit
  of the tab in front since it was loaded.

A value a field cannot hold (a frame that is not a whole number, a length
of 0) is refused, with the reason in the footer.

## Which assets a shot imports

A shot imports the assets of its Root: the Root's own list, then the lists
of its parent Roots (`outdoor` for `outdoor/forest`), once per asset: when a
Root and its parent both list an asset, the Root's own entry (its instance
count) wins. The **Assets** column shows exactly that. Double-click it (or right-click → **Edit Assets…**) to tick
assets and set instance counts:

- A Root is shared: every shot whose Root it is imports the same list. The
  dialog says which other shots use it. **Change Root *name*** changes the
  list for all of them.
- **Give *shot* its own Root** makes a new Root beside the current one
  (named `<sequence>_<shot>`, editable) holding the ticked list, and points
  the shot at it. The other shots keep the shared list. A Root beside the
  current one keeps the same parents, so their assets still come along.
- Grey rows in the dialog come from a parent Root and cannot be unticked
  here; change that Root to change them.
- A shot with no Root imports nothing. Ticking assets for it gives it its
  own Root.

Root changes are tinted like any other edit and are written on Apply. A new
Root no shot uses is not created. Apply then brings the shots up to date
(see [Apply](#apply)), so a workfile opened next imports what was ticked.

## Render layers

The **Layers** column is the shot's render layers, the bottom one first,
written as `name: prims` and separated by `;`:
`characters: /CHAR/*; fx: /FX/*`. `default` is always the bottom layer and
holds every prim no other layer names; it is left out of the text unless it
has prims of its own. A shot that renders as one layer shows nothing. What
the farm and the layered playblasts do with them is in
[Render layers from `channel_prims`](../nodes/lighting-and-rendering.md#render-layers-from-channel_prims-render-passes).

Double-click the cell (or right-click → **Edit Layers…**) for its dialog:
one row per layer with its **Prims** and, when the open workfile is that
shot's, how many prims each pattern **Matches** on the open stage (a
pattern that matches nothing turns red). **Add Layer** adds one on top,
**Move Up** / **Move Down** reorder them, and **Use Selection** puts the
prims selected in the Scene Graph Tree or a viewer into the current row.
Prims are paths and globs (`/CHAR/*`, `/SET/**`, `/CHAR/hero[12]`, `^` to
leave some out), separated by spaces; Houdini-only syntax such as
`%type:Mesh`, commas and a `[...]` set that is not one are refused, because
farm renders match them outside Houdini. Layer names are letters and digits,
and no two may differ only by case (`fx` and `FX`, or a `Default` beside
`default`): their exports would share a folder. Names and patterns stored
before these rules stay as they are until changed. A layer without prims
renders its own exports and makes no pass.

- **On a sequence's row** the layers are what its shots inherit, shown
  grey on each shot until a shot sets its own. A whole project's default
  goes on the project in the [Config Editor](config-editor.md).
- **On a selection** OK sets the same layers on every selected shot.
- **Inherit** puts a shot's layers back to what its sequence (or the
  project) gives it.
- A channel a department has **exported** cannot be removed or renamed
  here: its renders read those exports. Its row is locked and leaving it
  out is a problem that blocks OK.

The cell takes typed or pasted text too, in the same `name: prims; name`
form, so a column of layers can be copied from one sequence to another.
Apply writes the shot's `channels` and `channel_prims`
([Configuration](../configuration.md#storage)), keeping only what differs
from what the shot inherits: a shot that adds `characters` to its
sequence's `fx` stores the `characters` pattern alone. The shot's detail
panel shows the same layers on its **Layers** row, marked *(inherited)*
when they come from the sequence or project.

## Departments turned off on a shot

A shot that does not need a department at all (no effects, no crowd) can
turn it off. Double-click the **Departments off** cell for **Departments
of `<shot>`**: one tick box per department of the shot pool, ticked while
it is on. Untick one and press OK, then Apply. A department turned off:

- stays out of the shot's staged build, so every department after it sees
  the shot without it (`th::import_shot` leaves its layer out too, even
  from a stage built before it was turned off);
- gets no new workfile: **New from Template** and **New from Current** on
  it are refused with a message saying how to turn it back on;
- is not offered by [Farm Submit](submit-jobs.md) for that shot (its cell
  shows `×`), and the publish collects no export into it for that shot (a
  Multi's workfile still publishes its other members).

Nothing is deleted. Its workfiles and exports stay on disk and its existing
workfiles still open, so ticking it again brings it all back. The browser
lists it greyed with *(off)*, and its right-click menu has **Disable
department on this shot** / **Enable department** for the same switch on
one shot.

- **On a sequence's row** the list is what its shots inherit.
- **On a selection** OK sets the same list on every selected shot.
- **Inherit** puts a shot's list back to what its sequence gives it.
- The cell takes typed or pasted names too: `effects, cfx`. `none` turns
  every department on for a shot whose sequence turns some off. A name the
  shot pool does not have is refused.

Apply writes the list, in pool order, to the shot's own
`disabled_departments` ([Configuration](../configuration.md#departments-turned-off-on-a-shot)).

## Render settings

Below the Shots table, the **Render settings** section shows the render
settings a shot's root layer applies, and changes them without the Database
Editor. It edits what is selected:

- **shots** (one or more rows): the shots themselves;
- **a sequence's row**: what its shots inherit;
- **Project defaults** (the button at its right, pressed): all shots
  (`entity:/shots`), what every sequence and shot inherits.

With nothing selected and Project defaults not pressed, it asks for a
selection. The settings are exactly the ones the root layer applies
([Configuration → render settings](../configuration.md#render-settings-in-the-root-layer)):

| Setting | What it does |
| --- | --- |
| **Apply to render** | Whether the shot's root layer authors the two below, and the Karma samples and ray limits pinned in the Database Editor, on the project's RenderSettings prim (`render.apply_config`). Off, or not set anywhere: renders use the RenderSettings the scene carries. |
| **Resolution** | Width x height, typed as `1920x1080` or `1920 x 1080` (`render.resolution`). It also sizes the layout camera's frame and playblasts, applied to the render or not. |
| **Camera** | The camera prim the render looks through, e.g. `/cameras/render_camera` (`render.camera`). Authored only when that camera exists in the project's `root_default_prims.usda`; otherwise the scene's camera renders. |

Each setting shows its value and, beside it, where it comes from: *set
here*, *from sequence sq010*, *from all shots*, *from the project*, *from
the project's defaults*, or *not set anywhere*. Inherited values are grey,
as in the sheet. Several selected shots that differ show *(mixed)* (Apply
to render a half-ticked box) and *differs between them*.

- **Type or tick** to set a value on every target. Typing what a target
  already inherits pins nothing, as in the sheet. A value the root layer
  could not use (`0 x 5`, a camera without a leading `/`) is refused, and
  the footer says why.
- **Inherit** takes the value set here away, so the target follows its
  sequence, all shots or the project again. Clearing a field does the same.
- The line under the settings says what the shot renders with: *"The root
  layer renders at 2048 x 858 and through /cameras/render_camera when
  root_default_prims.usda has that camera."*, or *"Not applied: renders use
  the RenderSettings in the scene."* For a sequence or all shots it is what
  a shot that inherits all three gets.

The edits are pending like the sheet's (tinted, counted in the footer as
*all shots render defaults changed*, *1 sequence changed*, *2 shots
changed*, undone with Undo) and written by Apply, which writes only the one
key of `render` each setting is, leaving the rest of `render` (samples,
overscan, ...) as stored. A changed setting makes the shot's root layer
stale; Apply's staging brings it up to date (a shot with a Root gets a new
root layer at once; any other at its next build).

**Order.** A shot takes each setting from the first of: the shot, its
sequence, all shots, the project (`entity:/`), the project's defaults
(`schemas.json`). A [Farm Submit](submit-jobs.md) override (samples, motion
blur, depth of field, ray limits) is a separate, stronger layer on the
same RenderSettings prim: it wins for that one submission, and changes
nothing here. Farm Submit has no render resolution or camera override, so
for those two the order ends at the shot (its playblast width and height
start from the shot's resolution).

## Multis

A [Multi](multis-and-roots.md#multis) gives several shots (or several
assets) one workfile for the departments it *covers*. The editor is where
a Multi is set up, all of it: name, members, coverage, a shots Multi's
Timeline and its workfiles. The browser's **New Multi…**, **Edit Multi…**
and **Departments…** open it on the Multis tab, on that Multi (New Multi…
asks for the new one there, in the context it was chosen from).

**From the Assets or Shots tab.** The **Multis** column lists each row's
Multis; its tooltip says which departments each covers. Select rows and
double-click the column (or right-click → **Edit Multis…**) for a list of
the Multis of that kind, ticked where the rows belong. A Multi only some of
the selected rows are in is partly ticked and left alone unless clicked.
**New Multi…** in that dialog makes one holding the rows. The right-click
menu also has **Add to Multi ▸**, **Remove from Multi ▸** and **New Multi
from Selected…**. A row whose Multis changed says *Multis changed* in its
Status. On a sequence's (or category's) own row the same cell and menus
list the sequence whole: every shot in it, and any made later, is a member
(see [A whole sequence](multis-and-roots.md#a-whole-sequence)).

**On the Multis tab.** One row per Multi of the kind the dropdown shows
(**Shots** or **Assets**), with **Multi** (its name: edit to rename),
**Status**, **Members** (a count: double-click for a list of every row of
the kind, ticked where it is a member, with each sequence or category at the
top to tick whole; see
[A whole sequence](multis-and-roots.md#a-whole-sequence)), on a shots
Multi its **Timeline**, and a tick box for each department
in the pool, in pipeline order. Ticking a box covers that department; with
several cells of a column selected, one tick sets them all. Double-click
**Timeline** to choose **Per shot**, **Shared** or **Shared, with
bookmarks** ([Shared timeline](multis-and-roots.md#shared-timeline)); every
selected cell of the column follows, and the Status says *Timeline changed*.
**Add Multi…** asks for a name, a shots Multi's Timeline and the departments
(all of the pool to start with) and can start with the rows selected on the
Assets or Shots tab. **Retire**, **Revert**, **Undo** and **Redo** work as on
the other tabs. The Multis tab shares its kind's undo history: an undo on
the Shots tab can undo a shot Multi's edit, and the other way round.

**Workfiles.** Under the table, **Workfiles of *name*** lists the selected
Multi's workfile in each department it covers: its latest version and
**Open**, or *none* and **New from Template**, which makes the first version
from the department template with a graph per member. Either replaces the
open scene, after asking what to do with its unsaved changes (save a new
version, discard, or cancel); one at a time, the buttons waiting while a
workfile is made. A new Multi, or one with edits not applied, says *Apply
first* and has nothing to press. See
[The Multi's workfiles](multis-and-roots.md#the-multis-workfiles).

Members follow the sheet: rename a shot and its Multis list the new name on
Apply; retire it and it leaves every Multi. A member the project no longer
has stays listed, greyed in the members list.

Retiring a Multi removes it from the project; its workfiles stay on disk
under `groups/<context>/<name>`, and its members open their own workfiles
again. A Multi that has workfiles cannot be renamed (they would stay under
the old name): retire it and add a new one. A member added to a Multi whose
workfile already exists has no graph in it yet: the next save of that
workfile, or **Sync with Multi**, offers to add one, copied from another
member's. The other way round, an export added by hand for a shot that is
not a member offers to add the shot. See
[Keeping the workfile and the Multi in step](multis-and-roots.md#keeping-the-workfile-and-the-multi-in-step).

## Adding assets and shots

**Add Shots…** opens a small dialog:

- **Sequence**: pick one or type a new name. The note says how many shots
  it already has.
- **Names**: a pattern where `#` is the number, zero-padded to as many
  digits as there are `#` (`SH###` gives `SH010`), plus **from**, **step**
  and **count**. Picking a sequence takes the pattern its shots follow and
  continues after its highest number (`SH010`–`SH030` → from `40`). A
  sequence whose shots follow no letters-digits-letters pattern (such as
  `Shot_0030`) starts from `SH###` instead, and the note beside the
  sequence says *"named without a number pattern"*.
- **Frames**: start and end, defaulting to what the sequence's shots
  inherit. The new shots store this range.
- **Root**: **Inherit from the sequence**, an existing Root, or **New Root
  from assets…** (a name and ticked assets). The new Root is created on
  Apply.

**Add Assets…** takes a category (pick one or type a new name) and names
separated by spaces or commas. New assets inherit their timing until it is
set in the sheet. Assets added here can be ticked in a shot's **Assets**
before Apply: Apply creates the assets first.

**Paste as New** adds the clipboard's lines as new rows: `name`,
`category, name` (or `sequence, name`), `name, start, end` or
`category, name, start, end`, tab- or comma-separated. A bare name goes
into the category or sequence shown. A line whose frames are not whole
numbers, or a line of column titles, is skipped and named.

**New Sequence…** (Shots tab) or **New Category…** (Assets tab) asks for a
name and adds an empty one, created on Apply. The dropdown above the sheet
switches to it, shown as *sq030 (new)*, so **Add Shots…** or **Add
Assets…** goes straight into it. Its shots or assets inherit whatever is
later set on it (in the [Database Editor](config-editor.md), say). The name
follows the rules under [Problems](#problems): the dialog says why a name
is refused, and **Add** stays disabled until it is fine. While the new one
is shown in the dropdown, right-click → **Remove New Sequence *name*** (or
Undo) takes it back; new rows already in it still create it.

## Retiring and renaming

**Retire** marks the selected rows. On Apply each leaves the project and
its workfiles, exports, renders, playblasts and dailies move to
`_deleted/<date-time>/` in each storage location: the browser's **Delete**,
for many at once. Apply asks first, listing them. **Restore** (the same
button, on retired rows) or **Revert** keeps them. Retiring a new row just
removes it.

An asset or shot that has workfiles or exports on disk cannot be renamed:
they would stay under the old name and the renamed one would come up
empty. Its Status says so. Use **Duplicate…** on its card, then retire the
old one.

## Problems

Only rows you touched are checked, so old data never blocks an Apply. A row
needs fixing when:

- a new name uses anything but letters and digits, is empty, or is a
  Windows device name (`CON`, `PRN`, `AUX`, `NUL`, `COM0`-`COM9`,
  `LPT0`-`LPT9`, in any case). This covers
  a new or renamed asset or shot and a new category or sequence. An
  existing category or sequence keeps its name, so new rows still go into
  one named before this rule (such as `Seq_0010`);
- a name differs only by case from another one (*"Shot 'sh010' differs
  only by case from 'SH010'"*), or names one that already exists;
- two rows name the same entity (the later row is flagged);
- a name is still taken by an entity being retired or renamed in the same
  Apply (*"Apply that first"*);
- a new category is named like a sequence, or a new sequence like a
  category (a sequence or category added with **New Sequence…** / **New
  Category…** is held to these rules too, and Apply stops on one that
  breaks them after a Reload);
- it ends before it starts, a roll is negative, or the fps is not above 0;
- it renames an entity with files on disk.

Touched Multis are checked the same way. A Multi needs fixing when its new
name breaks the name rules or is taken, when it is renamed with workfiles
on disk, or when it covers a department another Multi also covers for the
same member (*"SH010's lighting is also covered by Multi seqA"*): which
Multi's workfile that member opens would be a guess.

The footer counts what Apply will do in every tab. **Apply** stays disabled
while any row needs fixing. The same line reports the last action until
the next edit: a value a field refused, pasted lines that were skipped, or
*"Applied: 5 new shots, 1 shot changed."*

## Apply

Apply writes every tab. It first re-reads the assets, shots and Multis. If
someone changed them in the meantime, your edits are carried over on top of
the new values (a member someone else added to a Multi stays), and the
editor stops so you can check them. Otherwise it writes the assets, then
the shots, each in this order:

1. retires,
2. renames,
3. new categories or sequences,
4. Roots: new ones (with any missing parents, empty), then changed asset
   lists,
5. new assets or shots (each with only the values the sheet set: for shots
   usually their frame range and Root),
6. changed values, merged into each entity's own values as they are on
   disk then. Properties the editor does not show are left alone,
7. Multis: retires, renames, new ones, then changed members and coverage.
   They come last so a new shot can be a member and a retired Multi's name
   is free for a new one.

After the writes, Apply stages the Roots: a Root whose list changed is
exported, a shot whose Root changed gets a new root layer, and a shot that
has no staged build yet is built (each reads only the layers it needs; no
scan of the project's dependency graph). The footer says what it did, e.g. *Applied:
3 shots; staged Root forest; updated 2 shots*. Shots without a Root are not
touched. If staging fails, Apply says so; the writes stay, and **Stage Root
& Rebuild Shots…** on the Root card in the browser runs it by hand. See
[Composition → A shot's Root assets belong to the Root](../composition.md#a-shots-root-assets-belong-to-the-root).

If a write fails (a retire whose files are open, say), Apply stops there
and says so. Another session reading the database at that moment no longer
counts: the write waits for the read to finish (up to a few seconds). Everything before it was applied. Everything after it is still
in the sheets, still tinted, so fix the cause and Apply again. **Reload**
re-reads every tab at any time, keeping your edits as they are, including
rows still flagged: a new row named like an existing asset or shot stays a
new row, never edits to that one. **Discard All** drops them.

Closing the editor with edits not applied asks: **Apply**, **Discard** or
**Cancel**.

The same name rules and spreadsheet parsing are used by the CSV importer
([`tools/csv_shot_import`](../../python/tumblepipe/tools/csv_shot_import.py)).
In the browser it is **Import Shots from CSV...** on the Shots section's
"+" and right-click menus: pick the sheet, and a dialog lists what the
import would do (`[ADD SEQ]`, `[ADD]`, `[ERROR]` lines, the dry run the
command line prints) before **Import N Shots** writes exactly that. The
header row is found by its column names (`Sequence`, `ShotName`, `Start`,
`End`) wherever it sits in the sheet; a sheet without one is refused with
the names it needs.
