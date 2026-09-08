# Workfiles and versions

A **workfile** is a Houdini scene for one entity and one department: the
`animation` hip of a shot, the `lookdev` hip of an asset. Every save through
the browser writes a new numbered version; nothing overwrites an earlier
one. This page follows a department row from first workfile to the version
history it grows into.

Source: [`workfiles.py`](../../python/tumblepipe/asset_browser/workfiles.py)
(open and create), [`scene.py`](../../python/tumblepipe/asset_browser/scene.py)
(save, reload, the unsaved-changes prompt),
[`pipe/paths/workspace.py`](../../python/tumblepipe/pipe/paths/workspace.py)
(paths and version numbers) and
[`pipe/context.py`](../../python/tumblepipe/pipe/context.py) (the sidecars).

## Where workfiles live

Paths come from the project's storage convention
([`storage_convention.py`](../../scripts/project_template/_config/storage_convention.py)):

| Entity | Department folder |
|---|---|
| asset `entity:/assets/CHAR/Baby` | `<project>/assets/CHAR/Baby/<department>/` |
| shot `entity:/shots/010/sh020` | `<project>/shots/010/sh020/<department>/` |
| Multi `groups:/shots/seqA` | `<project>/groups/shots/seqA/<department>/` |

Inside the folder every version is one file named
`<segments>_<department>_<vNNNN>.<ext>`, where the segments are the entity's
URI minus its context: `CHAR_Baby_model_v0001.hip`,
`010_sh020_animation_v0003.hip`, `seqA_light_v0001.hip`.

Exports land elsewhere, under `<project>/export/<context>/<…>/<channel>/<department>/vNNNN/`
(see [Composition → Department exports and staged files](../composition.md#department-exports-and-staged-files)).
**Open Export Folder** on the detail panel and **View Latest Export** on a
department row take you there.

## Version numbers

Versions are `v` followed by at least four digits, zero-padded: `v0001`,
`v0002`, … `v9999`, `v10000`. Each department of each entity counts on its
own. The next number is one above the highest file already in the folder,
and it is **reserved** before the save happens by creating
`_context/vNNNN.json` exclusively — two artists (or an artist and a farm
publish) saving the same department at the same moment get two different
numbers rather than one file overwriting the other.

"Latest" — the version a double-click opens and the label a row shows — is
the highest version on disk, cross-checked against the `context.json`
pointer described below; whichever is higher wins. A disagreement is logged
and healed by the [context chain audit](../development.md#context-chain-audit).

## Licence and file extension

The extension follows the Houdini licence the session runs on: `.hip`
(commercial), `.hiplc` (Indie), `.hipnc` (Apprentice). The browser picks the
extension from the running session so the file it records is the file
Houdini actually writes. The extension is also stored in the version's
sidecar, which is how a later open finds the file without probing the share.

## What the sidecars are for

Two JSON files sit in every department folder:

- `context.json` — the **pointer**: which entity (`uri`), `department` and
  `version` this folder's latest workfile is, plus `timestamp` and `user`.
  This is what tells the browser, the session panel and the export nodes
  what an open hip *is*; a hip without it (an old migrated project) falls
  back to parsing the folder path.
- `_context/vNNNN.json` — one **lineage entry** per version: `user`,
  `timestamp`, `from_version` (the version it was saved from),
  `to_version`, `houdini_version`, `extension` and `note`.

Details and the repair tool are in
[Development → Context chain audit](../development.md#context-chain-audit).
Do not hand-edit these; the browser reads them on every card build.

## Opening a workfile

Double-click a department row (or a deck item), pick **Open Latest (vNNNN)**
from its right-click menu, or press the play button on a row of the detail
panel's Departments tab (which opens whichever version its dropdown shows).
What happens, in order:

1. If the department is covered by a Multi the Multi's workfile is opened
   instead; the row showed **ⓜ** to warn you. See
   [Multis and Roots](multis-and-roots.md).
2. If your current scene has unsaved changes you are asked what to do with
   them (next section).
3. The file loads with Houdini in manual update mode, so the load itself
   does not cook the whole graph.
4. The project's **FPS** is set. The **frame range** from the shot's
   configuration is applied for animatable entities — shots, and assets
   unless they are marked `animatable: false` — so a re-timed shot opens at
   its current length. Non-animatable assets keep whatever range was saved
   in the hip.
5. With **Auto-import latest on workfile open** enabled (the default) every
   `th::import_*` node is re-executed so the scene picks up newer
   publishes; see
   [Composition → Picking up new versions on open](../composition.md#picking-up-new-versions-on-open).

A row with no workfile has nothing to open: double-clicking it does nothing,
its menu offers **New: Template** and **New: Current**, and the detail
panel's play button creates `v0001` (tooltip *Create dept/v0001 from the
dept template.*). If a version that should exist is missing from disk the
status bar reads *Workfile not found: entity / dept / vNNNN*.

## Switching scenes with unsaved changes

Opening a workfile replaces the whole Houdini scene, so the browser deals
with the current scene first. Houdini's own prompt would overwrite the
current version in place, which the pipeline never does, so the browser
substitutes its own:

> **Save Scene** — The current scene has unsaved changes. Save a new version
> before switching?
> **Save new version** / **Discard changes** / **Cancel**

**Save new version** writes the next version of the scene you are leaving
(with a blank note; see below) and then opens the new one. **Cancel** leaves
you where you are. With the **Autosave (version up) on scene change**
setting on, the new version is written silently and no prompt appears.

Two cases fall through to Houdini's native save prompt instead: an untitled
or off-pipeline scene (there is no version to bump), and a scene whose dirty
state cannot be read.

**Reload** (quick action, or **Reload Scene** on the open department's row)
goes through the same prompt, then reloads the open hip from disk and
re-applies the timeline and the import refresh.

## Saving a new version

**Save** in the quick-action toolbar saves the open scene as the next
version of *its own* entity and department. The status bar confirms with
*Saved CHAR_Baby_model_v0013.hip* and the entity's row updates in place.
Houdini's File ▸ Save still writes the open file in place; use the browser's
Save to version up.

The steps are: reserve the number, save the hip, write the lineage entry,
then move the `context.json` pointer last. If the hip save itself fails the
number is released; if the bookkeeping after it fails the hip is kept and the
audit reconciles the rest.

A scene with no pipeline context refuses with a dialog rather than saving
somewhere unexpected: *Save: the current scene has no pipeline context, so
there is no version to save it as. Open or create the scene through the
pipeline first.*

**Emergency Save (off-thread)** (right-click the Save button) runs the same
save without waiting for Houdini's event loop, for use while Houdini is
showing its crash-report dialog. It never prompts, and reports failure on the
status bar only.

### The version note

With **Ask for a version note on save** enabled (the default), Save first
opens **Save Version**: *Note for the next animation version of
shots/010/sh020 (after v0012) — optional:*. Type what changed, or leave it
empty. **Cancel** aborts the save entirely and burns no version number. The
note is stored as `note` in that version's `_context/vNNNN.json` and shows
in the list view's **Note** column: on the department row for its own
version, and on the entity row for the newest version across departments.
The column needs TumbleTrove 0.24 or newer; older browsers still store the
note, they just cannot show it.

Saves you did not ask for — the autosave-on-switch path, the save-before-
switch prompt's **Save new version**, and the emergency save — never prompt
and store a blank note.

### User and Edited

The **User** column is the `user` written into the sidecar at save time
(`TH_USER`, which comes from your TumbleTrove login — see
[Configuration → Environment variables](../configuration.md#environment-variables)).
**Edited** is the hip file's modification time, shown relative (`2h ago`).
On an entity row both describe the most recently saved department. The
detail panel's department rows show the same pair per row, plus the age and
author of the department's latest export.

## New: Template

**New: Template** on a department row creates the department's first
workfile, or a fresh one after existing versions (`v(N+1)`), from the
department template:

1. The next version number is reserved and an empty scene is saved there.
2. `_config/templates/<context>/<department>/template.py` is run against
   `/stage` to build the department's starting graph
   ([Configuration → Department templates](../configuration.md#department-templates)).
3. The configured frame range and FPS are stamped into the scene. Creation
   is the one time a non-animatable asset receives the config range;
   afterwards the artist owns it.
4. The scene is saved again and becomes your open scene.

Status: *Created CHAR_Baby_model_v0001.hip*, with *(no template)* appended
when no template module exists for that department and *(frame range/fps
NOT applied — check the log)* if step 3 failed. Because the empty scene is
saved first, the version exists on disk even when the template throws; the
log has the traceback.

## New: Current

**New: Current** saves the scene you have open **as the next version of the
department you right-clicked**, regardless of which entity it was loaded
from. The scene stays open, now pointing at the new file. The previous
scene's context is recorded as the new version's `from_version`, so the
lineage shows where it came from. Use it to seed a shot's `light` from a
sibling shot, or to fork a department across assets. Status: *Saved
010_sh030_light_v0001.hip*.

## Open in New Houdini

**Open in New Houdini** on a department row launches a second Houdini (the
same executable as the running one) on that department's newest hip, with
`TH_PROJECT_PATH` and `TH_CONFIG_PATH` set to the entity's project. The
browser does this on its own when you open a workfile from a project other
than the one your current scene belongs to; the status bar reads *Opening
X scene in a new Houdini instance...*.

This path picks the newest file by modification time in the entity's own
department folder, so for a department covered by a Multi it does not
redirect to the Multi's hip the way an in-session open does.

## Open Location and View Latest Export

- **Open Location** opens the department folder in Explorer, or the entity's
  folder when the department has no folder yet.
- **View Latest Export** opens the department's newest export folder
  (default channel). Nothing published yet: *No export found for dept.*
- **Open Export Folder** (detail panel) opens the entity's export root.

## Generate Master

**Generate Master…** on a card merges the latest workfile of every
department that has one into a single scene, each department's nodes in a
network box with its name, laid out left to right. After the **Generate
Master Scene** confirmation it writes and opens
`<entity folder>/master/<name>_master.hip`. This is a review scene outside
the version system: it has no `context.json`, so Save, Publish and Render
refuse it. Departments whose hip would not merge are named in a warning
afterwards.

## Picking an older version

At Home (no catalog selected on the rail) the detail panel's **Departments**
tab shows a version dropdown per department, newest first. Pick one and
press the play button, or right-click the row for **Open vNNNN**. The
choice is remembered for the session only.
