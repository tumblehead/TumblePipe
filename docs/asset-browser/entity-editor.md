# The Entity Editor

The **Entity Editor** shows a project's assets and shots as two
spreadsheets, an **Assets** tab and a **Shots** tab, one row per entity.
Add assets and shots, change frame ranges, rolls, fps, Animatable and
Roots, choose which assets each shot imports, rename and retire, then
**Apply**. The [Database Editor](config-editor.md) can change the same data
one raw property at a time; this is the friendly way. Source:
[`entity_editor.py`](../../python/tumblepipe/asset_browser/entity_editor.py)
(window) and [`entity_sheet.py`](../../python/tumblepipe/config/entity_sheet.py)
(model, checks and writes).

## Opening it

- **Entity Editor...** in the creation list of the **Assets** and **Shots**
  headers, a category or sequence row, the **+** card and the empty-space
  menus. From the Assets side it opens on the Assets tab, from the Shots
  side on the Shots tab; from a category or sequence, on that one.
- Right-click an asset or shot card → **Open in Entity Editor…**: opens on
  its tab and group with it selected.

There is one editor per project. Opening it again raises the open one. It
is not modal, so the browser and Houdini stay usable beside it. A tab's
name, and the title, gain a `*` while there are edits not applied.

## The sheets

| Column | Tab | What it is |
|---|---|---|
| **Category** / **Sequence** | both | Fixed for a stored entity; editable on a new row (a new name makes a new category or sequence). |
| **Asset** / **Shot** | both | Editing it renames the entity on Apply. |
| **Status** | both | What Apply does with the row, or why it cannot. A long reason is cut short; its tooltip has all of it. |
| **Start**, **End** | both | The frame range (`frame_start`, `frame_end`). |
| **Length** | both | Frames from start to end. Typing a length moves the end frame. |
| **Pre-roll**, **Post-roll** | both | `roll_start`, `roll_end`: frames added before the start and after the end when rendering. |
| **FPS** | both | `fps`. Whole numbers are kept whole; `23.976` is fine. |
| **Animatable** | Assets | `animatable`, as a tick box. Typing `yes` or `no` works too. |
| **Root** | Shots | The shot's [Root](multis-and-roots.md), picked from a dropdown. **Inherit** puts it back to the sequence's. |
| **Assets** | Shots | What the shot imports: its Root's assets, then the Root's parents'. Empty for a shot with no Root. See [Which assets a shot imports](#which-assets-a-shot-imports). |

The dropdown above the sheet shows one category or sequence, or all of
them. **Filter** narrows the rows to names that contain the text.

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
shows. The line above the sheet shows each look on a sample cell; its
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
  inherited. Lines that run past the last row are not added; use **Paste as
  New** for that.
- **Fill Down** (Ctrl+D) copies each column's top selected value into the
  selected cells below it.
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
Root no shot uses is not created.

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

- a new name uses anything but letters and digits, or is empty. This covers
  a new or renamed asset or shot and a new category or sequence. An
  existing category or sequence keeps its name, so new rows still go into
  one named before this rule (such as `Seq_0010`);
- a name differs only by case from another one (*"Shot 'sh010' differs
  only by case from 'SH010'"*), or names one that already exists;
- two rows name the same entity (the later row is flagged);
- a name is still taken by an entity being retired or renamed in the same
  Apply (*"Apply that first"*);
- a new category is named like a sequence, or a new sequence like a
  category;
- it ends before it starts, a roll is negative, or the fps is not above 0;
- it renames an entity with files on disk.

The footer counts what Apply will do in both tabs. **Apply** stays disabled
while any row needs fixing. The same line reports the last action until
the next edit: a value a field refused, pasted lines that were skipped, or
*"Applied: 5 new shots, 1 shot changed."*

## Apply

Apply writes both tabs. It first re-reads the assets and shots. If someone
changed them in the meantime, your edits are carried over on top of the new
values, and the editor stops so you can check them. Otherwise it writes the
Assets tab, then the Shots tab, each in this order:

1. retires,
2. renames,
3. new categories or sequences,
4. Roots: new ones (with any missing parents, empty), then changed asset
   lists,
5. new assets or shots (each with only the values the sheet set: for shots
   usually their frame range and Root),
6. changed values, merged into each entity's own values as they are on
   disk then. Properties the editor does not show are left alone.

If a write fails (a retire whose files are open, say), Apply stops there
and says so. Everything before it was applied. Everything after it is still
in the sheets, still tinted, so fix the cause and Apply again. **Reload**
re-reads both tabs at any time, keeping your edits. **Discard All** drops
them.

Closing the editor with edits not applied asks: **Apply**, **Discard** or
**Cancel**.

The same name rules and spreadsheet parsing are used by the CSV importer
([`tools/csv_shot_import`](../../python/tumblepipe/tools/csv_shot_import.py)).
