# Libraries

A **library** is a folder outside every project that holds recipes and
texture sets for more than one project. Each project lists the libraries it
uses; two projects that list the same folder share it, so a recipe or a
texture set saved there once is on both projects' grids.

Source: [`libraries.py`](../../python/tumblepipe/asset_browser/libraries.py).
Design: [`designs/libraries-pickers-poses.md`](../../designs/libraries-pickers-poses.md).

## Which libraries a project has

A project's libraries come from two places, in this order:

1. **`TH_LIBRARY_PATH`**, the launch environment of the project Houdini was
   started for. It is a path list like `HOUDINI_PATH`: `;` between folders on
   Windows, `:` on macOS and Linux. Set it per project in TumbleTrove
   Desktop's project settings, under **Environment Variables**. It applies
   only to the project it was launched for, and is never written to your
   project list.
2. The folders you add on the **Projects** settings page (the gear in the
   sidebar header): select the project, then **Libraries ▸ Add…**, and
   **Apply Project Changes**. Rows from `TH_LIBRARY_PATH` are shown there
   greyed out, marked `(TH_LIBRARY_PATH)`; change those in Desktop.

A folder listed twice (by both, or by two projects) is read once. Paths are
per machine, so a macOS and a Windows artist can spell the same share their
own way.

## A library's folder

```
<library>/
  library.json            # {"name": "Studio", "read_only": false}
  recipes/<context>/<slug>/
  textures/<set>/vNNNN/
```

The layout is a project's own: `recipes/` as in [Recipes](recipes.md#where-they-are),
`textures/` as a project's library sets in [Texture sets](textures.md).

`library.json` names the library. Adding a folder that has none on the
Projects page asks for a name and writes it. Without one the folder's name
is used, with anything but letters, digits, `-` and `_` turned into `_`. The
name is how every project sees the library, so it must be unique among the
libraries you have: a second library with a taken name shows as an error at
the top of the grid, naming both folders, and is left out.

`"read_only": true` hides **Edit**, **Delete** and **Share to Library** for
the library's items, and leaves it out of **New Recipe**'s **Save to** list.
It only hides the actions: the folder's own permissions are what protect it.

A library that cannot be read (the share is down, the folder moved, a torn
`library.json`) is an error at the top of the grid; the other libraries and
the projects still load.

## In the browser

Beside the project sections, the sidebar has one folder per library, listed
once however many projects list it:

| Row | Shows |
|---|---|
| ***Name*** | Everything in the library. *(read-only)* follows a read-only library's name. |
| **Recipes** | One row per Houdini context, as a project's Recipes section. |
| **Texture Sets** | The library's texture sets. |

A library's cards are not in any project's scope: the project root and its
sections list the project's own items, the library folder lists the
library's. A library card can be a favourite in every project.

## Using a library item

What an item becomes in the scene decides whether it is copied first:

| Item | Dropped | Copied into the project? |
|---|---|---|
| Recipe | its nodes are loaded, as from the project | No: nothing in the scene points at the library afterwards. |
| Texture set | as a project texture set | **Yes**: the version is copied into the project's library sets (`$TH_EXPORT_PATH/textures/<set>/vNNNN/`) and the node is pinned to that copy. |

So no farm job, archive or other machine ever needs to reach a library.
Dropping the same library version into the project again reuses its copy;
a newer library version is copied as a new project version. The copy's
`context.json` records `copied_from` (the library card's id and version).
Dropping a library texture set needs a project workfile open: with an
untitled scene there is no project to copy it into, and the drop says so.

## Sharing and copying

On a card's right-click menu:

- **Share to Library: *name*** on a project's recipe or texture set copies it
  into that library (each writable library is listed).
- **Copy to Project: *name*** on a library's item copies it into that
  project (each registered project is listed).

A recipe copy replaces a recipe of the same context and name there, after
asking. A texture set copy is the destination's next version, unless that
same version was copied before.

**New Recipe…** lists the writable libraries under **Save to**, beside the
projects, preselected when you are in a library's section. Saving there
puts the recipe straight into the library.
