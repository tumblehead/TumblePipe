# Recipes

A **recipe** is a chunk of node network saved into the project — a lighting
rig, a scatter setup, a render-layer configuration — that anyone on the project
can drop back into a scene. Recipes are the third kind of entity in the
Pipeline catalog beside assets and shots: they have a **Recipes** section in
the sidebar, cards in the grid, and they show up in the browser's **All**
view with everything else.

Up to TumbleTrove 0.34 recipes lived in TumbleTrove's own *Network* catalog,
in a personal folder on each machine. They now live in the project, so a
recipe one artist saves is on every teammate's grid after a **Refresh**.

## Where they are

```
<project>/recipes/
  <context>/            # sop, lop, obj, cop, …
    <slug>/
      entry.json        # name, description, tags, node layout, who saved it
      recipe.cpio       # the nodes, as Houdini's saveItemsToFile writes them
      thumbnail.png     # a capture of the network editor, when one was taken
```

The sidebar's **Recipes** section has one row per context the project has
recipes in — **SOP**, **LOP**, **OBJ** — with a count pill; click a row to
show only that context's recipes. The section header shows the total. The
section is listed even before the first recipe, so its right-click menu can
offer **New Recipe...**. Rows and counts stay in place through a **Refresh**
and catch up as soon as the rescan finds recipes saved elsewhere.

## Saving a recipe

Either:

- **drag nodes from a network editor onto the grid** while the Pipeline
  catalog is scoped (any place in it — the project root, a category, the
  Recipes section). The recipe is exactly the nodes you dragged: drag one
  node of a selection to take the whole selection, or an unselected node to
  take just that node. The dragged nodes are selected when the form opens.
  Needs TumbleTrove 0.35.1 or later; before it, the recipe was whatever
  happened to be selected, which is not always what you dragged. Or
- select the nodes and pick **New Recipe...** from the **+** card, the list's
  **+** row, the empty-space menu, or the Recipes header's right-click menu.

Both open the same **New Recipe** form: **Name** (required), **Description**
and **Tags** (comma-separated), plus a **Project** dropdown when more than one
project is registered. The recipe's context is the network the selected nodes
sit in. The name is slugified for the directory — `Scatter Setup` becomes
`scatter_setup` — and saving over an existing slug in the same context asks
**Overwrite** or **Cancel**. The status bar reports **Saved recipe: *name* (N
nodes)** and the new card is selected.

Nothing is saved without a selection: the form is followed by *Select one or
more nodes in the network editor before saving a recipe.* A recipe holds the
nodes of one network. If you also have nodes selected in another network,
only the network you selected in last is saved.

## Using a recipe

Drag the card into a network editor. The nodes are recreated, wired, and
placed at the cursor, selected, with the display flag on the last one; the
status bar reports **Loaded recipe: *name* (N nodes)**. While you hover the
network editor a ghost of the saved layout is drawn under the cursor.

Cards carry a context chip — **SOP**, **LOP**, **OBJ** — for the network the
recipe was saved from, and are drawn disabled when the network editor shows a
different context. Dropping into a different context anyway asks **Load
Anyway** or **Cancel**. Only a network editor accepts a recipe; the viewport
does not.

## Card menu and detail actions

Right-click a recipe card for:

| Item | What it does |
|---|---|
| **Edit Description…** | Edits the description stored in `entry.json`. |
| **Set Icon…** | Picks a Houdini or Lucide icon to show instead of the thumbnail. |
| **Open Recipe Folder** | Opens the recipe's directory in the file manager. |
| **Delete Recipe** | Removes the directory from the project, for everyone, after a **Delete Recipe** confirmation. |

The detail panel offers **Open Recipe Folder**, **Edit…** (description and
tags; name and context are shown read-only) and **Delete**. Its tabs are
TumbleTrove's own tags and description; a recipe has no departments.

In list view a recipe row fills **Name**, **User** (who saved it) and
**Edited** (when); the department columns stay empty. **Latest Update** sorts
recipes by their save time alongside the entities.

## Sharing, refresh and old recipes

Nothing watches the project share, so a recipe saved on another machine
appears when the browser next rescans: press **Refresh**, or right-click it to
put it on a timer. If a recipe is there but shows an old thumbnail, right-click
the card and choose **Refresh Thumbnail**.

Recipes from the old Network catalog are not migrated automatically. Their
files are unchanged in shape, so copy a personal recipe's directory —
`<config>/asset_browser/recipes/<context>/recipe/<slug>/` — to
`<project>/recipes/<context>/<slug>/` (dropping the `recipe` level) and press
**Refresh**.
