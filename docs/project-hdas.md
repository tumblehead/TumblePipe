# Project HDAs

A **project HDA** is a digital asset that belongs to one project rather than
to the pipeline: a studio tool built for this film, a cleanup network the
modellers share. They live in the project's `otls/` folder, and every Houdini
session and farm task of the project loads them.

## Creating one

1. Build the network inside a **Subnetwork** in any context (SOP, LOP, OBJ,
   COP, …). Spare parameters you add to the subnet become the asset's
   parameters.
2. Right-click the subnet and choose **TumblePipe ▸ Create Project HDA...**
   (just below Houdini's own **Create Digital Asset...**).
3. Type a **Name** and click **Create**. **Cancel** changes nothing.

The name is the only input. It must be letters, digits and underscores,
starting with a letter (`mesh_cleanup`, `fogRig2`). The dialog fills in the
subnet's own name when that is already valid, and shows the type and file it
will create before you click **Create**.

The subnet becomes the first instance of the new asset, renamed to the name
you gave, wired as before and matching its definition (locked). Its spare
parameters are now part of the asset, so every new instance has them.
**Tab ▸ *project name* ▸ *label*** creates more, where the label is the name
in words (`mesh_cleanup` → **Mesh Cleanup**).

The entry is shown only for nodes Houdini can turn into an asset (subnets)
and only in a session with a project (`TH_PROJECT_PATH`).

## Names, versions and files

| | For project `HideAndReek`, name `mesh_cleanup`, a SOP subnet |
|---|---|
| Type | `hideandreek::mesh_cleanup::1.0` |
| Label | Mesh Cleanup |
| File | `<project>/otls/sop_hideandreek.mesh_cleanup.1.0.hda` |
| Tab submenu | HideAndReek |

- The **namespace** is the project folder's name, lowercased, with anything
  but letters, digits and underscores dropped.
- The **version** is `1.0` for a new name. If the name is already taken (an
  installed type, or a file a teammate saved to `otls/` since you opened
  Houdini), the new asset is **one major version above** the newest one:
  `2.0`, `3.0`, … (after `1.0.3` comes `2.0`). The dialog says so before
  you create it. Existing versions are never overwritten, so scenes that
  use them keep working; switch an instance to the new version with
  Houdini's **Asset Definition Toolbar** or by recreating it.
- **Case does not make a new name.** Windows and macOS ignore case in file
  names, so names are compared the same way, and a taken name keeps the
  spelling it already has: typing `Mesh_Cleanup` when `mesh_cleanup` 1.0
  exists creates `mesh_cleanup` 2.0, one version family. The dialog says
  *its spelling is kept*. If a name already exists in two spellings, the
  newest version's is used.
- **One file per version**, named the way the package's own `otls/` is.
  Creation leaves no `otls/backup/` copies of the new file.

## Editing a project HDA

Project HDAs are ordinary assets. Right-click an instance → **Allow Editing
of Contents**, change it, then **Save Node Type**, or open **Type
Properties...**. Saving writes to the version's file in `otls/`; teammates
get the change the next time they start Houdini (or after **Assets ▸ Refresh
Asset Libraries**). To make a change without affecting scenes that use the
current version, create a new version instead: run **Create Project HDA...**
on a subnet with the same name.

## How the folder is loaded

The package's manifest prepends `$TH_PROJECT_PATH/otls` to
`HOUDINI_OTLSCAN_PATH`. Houdini's package loader keeps the default scan path
after it, so the pipeline's own HDAs and Houdini's still load. Because it is
part of the package environment, farm tasks get it too: a scene that uses a
project HDA renders and publishes on the farm without extra setup. The folder
is created by the first **Create Project HDA...**; a project without one
loads nothing extra.

`otls/` is one of the pipeline folders the [project archive](archiving.md)
packs with the project.

The code is `python/tumblepipe/pipe/houdini/project_hda.py`; the menu entry
is the package's `OPmenu.xml`.
