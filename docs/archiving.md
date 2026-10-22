# Archiving and restoring projects

When a project is delivered, TumblePipe can pack it into a zip archive:
for the client as proof of work, or for cold storage. It can also unpack
one again and re-make the caches it left out. The project on the server
is never changed. Archiving only reads it.

Three buttons in TumbleTrove Desktop's per-project **Scripts** panel:

| Button | What it does |
|---|---|
| **Archive Project** | Opens the archive window: pick a profile, read the project, see what goes in and what is left out, write the archive. |
| **Restore Archive** | The same window on its Restore tab: check an archive, unpack it. |
| **Rebuild Caches** | In a project restored from an archive, re-make the caches the archive left out by re-running their workfiles in hython. Needs a Houdini licence. |

The same tool runs from a shell, which is handy for big projects or a
script that archives several at once (see [From a shell](#from-a-shell)).

## What goes in

An archive takes the project's **sources**: the workfiles and the pipeline
config. It also takes the latest version of every export, so the work opens
with its inputs. What can be re-made from the workfiles, and what isn't pipeline data,
is left out. Pick one of three profiles:

| Profile | Workfiles | Exports | For |
|---|---|---|---|
| **Proof of work** | every version | the latest finished version of each | the client: the work and its history |
| **Latest** | the latest version per department | the latest finished version of each | small cold storage |
| **Full** | every version | every finished version | complete cold storage |

Every profile also takes `_config`, the workfile sidecars (`context.json`,
`_context/`), anything else artists keep in workfile folders (painted
textures, reference images, training runs), and the project's `otls`,
`recipes` and `library` folders. The scenes load those.

**Only the latest export.** The pipeline's import nodes move to the latest
version of each export when a workfile opens, so older export versions are
never needed to open the work. The archive keeps the newest *finished*
version of each: if the newest one never finished (no `context.json`), the
one before it is kept instead. *Full* keeps every finished version.

**Caches** and **renders** (frames, playblasts, dailies) are left out unless
you tick them. Then only their latest finished version goes in. On
HideAndReek the renders alone were 2.2 TB.

## What is always left out

| Left out | Why |
|---|---|
| Houdini's `backup/*_bakN.hip` | Houdini's own save backups; the versioned workfiles are the history |
| `export/other/jobs/` | copies of workfiles made for farm jobs |
| Unfinished versions | an export version with no `context.json`, or a cache version with no sidecar |
| Solaris render galleries, `render/` in a workfile folder, `.rat` / `.tx` | re-made from the workfiles or their source textures |
| The `_cache`, `_proxy`, `_turntable` folders beside the project | derived |
| Python environments (any folder with a `pyvenv.cfg`), `node_modules`, `__pycache__` | installed software |
| `*.bak*`, `*~`, `Thumbs.db`, `desktop.ini` | noise |
| The edit (`storage.json`'s `edit` root) and every other top-level folder: client material, `delivery`, `temp`, … | not pipeline data |

Those folders stay on the server. They just aren't part of an archive you
might send someone. To archive one of them anyway, name it in
`_config/archive.json`:

```json
{
  "include": ["resources"],
  "exclude": ["library"]
}
```

`include` adds top-level project folders; `exclude` leaves out one of the
folders archived by default.

## Check before sending

Reading the project ends with a list of things worth a look:

- **Caches with no record of the workfile that wrote them.** Caches made
  before TumblePipe recorded this (see [What the pipeline records](#what-the-pipeline-records)),
  or ones that never finished. They are left out. Keep any that can't be
  cached again, like a sim nobody can re-run.
- **Folders left out as not pipeline data**, with their sizes.
- **File paths outside the project** that the archived scenes use. The latest
  workfile of each department (plain `.hip`) and every archived `.usda` are
  read for absolute paths. A path outside the project means the file isn't in
  the archive. A path is placed by the project's folder name, so `P:/<project>`,
  `W:/projects/<project>` and `//server/share/projects/<project>` all count as
  the project. Software locations (the Houdini install and preferences,
  installed packages, temp folders) are ignored.
- **Paths into a left-out folder**, such as a texture in `resources/`. The
  scene loads it, but the archive doesn't have it.
- **Project files named by absolute path**, like the
  `//TRUENAS/production/projects/<project>/…` texture paths lookdev exports
  write. They're archived, but the scene only finds them when the project is
  restored at that same path.
- `.hiplc` / `.hipnc` latest workfiles. These are encoded and can't be read
  for paths, so check them in Houdini.

## The archive

An archive is one or more ordinary ZIP64 files, so a client opens it with
what their computer already has. Already-compressed formats (USD crate, EXR,
`.bgeo.sc`, VDB, movies, images) are stored as they are. Text and `.hip` are
compressed. Split a big archive into parts with **Split into parts of at most** in the
window, or `--part-size` in a shell (`50G`).
Each part is a complete zip of its own, and no file spans two parts.

Inside, every location keeps its layout under a top-level folder:
`<project>/` for the project folder and `<project>_render/` for the render
folder beside it. Unpacking next to each other gives back the layout
`storage.json` expects.

`ARCHIVE/manifest.json` (also written next to the zip as
`<name>.manifest.json`) records:

- the profile, when, and by whom
- the TumblePipe and Houdini versions the workfiles were saved with
- every file with its size and SHA-256
- what was left out and why
- the caches and renders a rebuild could make again

`ARCHIVE/README.txt` says the same for a person.

Writing goes to `*.partial` files that are renamed only when the whole
archive is done. A cancelled or failed run leaves nothing that looks like an
archive. The archive can't be written inside the project.

## Restoring

**Check archive** reads every file back and compares it with its hash in the
manifest. **Restore** unpacks into a folder you pick and checks every file as
it goes. It can restore under a new project name. The project folder and its
`<name>_render`-style neighbours follow the new name. A restore that would
overwrite a file refuses before writing anything.

The restored project keeps the manifest as `_archive/<name>.manifest.json`.
It's the record of where the project came from, and what **Rebuild Caches**
reads.

To work in it, make a TumbleTrove project pointing at the restored project
folder, with the TumblePipe version the README names.

## Rebuilding caches

**Rebuild Caches** re-makes each cache the archive left out. It does this
when the cache's record names the workfile and node that wrote it, and that
workfile came back with the archive. A *Latest* archive keeps only the newest
workfile per department, so a cache written by an older version can't be
re-made from it; the rebuild lists those.

Each workfile opens once in hython, and its caches run in the order they were
first made, so a cache that feeds another is made first. Every cache is
written back into **its own version**. Downstream workfiles load caches by
version, so a re-made cache must land where it was. A finished version is
never overwritten.

A rebuild doesn't reproduce a cache bit for bit when it depends on a random
seed, the time, or an input that's gone. Keep such caches in the archive
(tick caches, or leave them on the server).

## What the pipeline records

Archiving is easier the more the files say about themselves, so TumblePipe
writes these as you work:

- **Every `th::cache` version** gets a `context.json` when it finishes:
  - the entity and department
  - the cache name, version and frame range
  - who cached it, and when
  - a `producer` block: the workfile (path and version), the node, and the
    TumblePipe and Houdini versions

  Because it's written last, it also marks the version as finished.
- **Every export's `context.json`** carries the same `producer` block.
- **Every workfile version's `_context/vNNNN.json`** records the TumblePipe
  version beside the Houdini version.

Caches made before this have no record. Their archives flag them, and
**Rebuild Caches** can't re-make them.

## Pruning farm job folders

Archiving never deletes anything. Freeing space on the server is a separate
tool:

```
python -m tumblepipe.prune [project] [--apply] [--min-age-days 2]
```

Every farm submission writes a folder under `export/other/jobs/<id>/`: a
copy of the workfile and the job's settings. Deadline reads it when the job
runs, and again whenever someone requeues it. On HideAndReek these were 559
folders and 25 GB. The prune asks Deadline which jobs still use each folder:

| Folder | Pruned? |
|---|---|
| every job that used it is **Completed** | yes |
| a job that uses it is Suspended, Failed, Queued, Rendering or Pending | no, it may be requeued |
| no Deadline job uses it any more (the jobs were deleted from Deadline) | yes, once it is older than `--min-age-days` |
| Deadline cannot be asked, or reports no TumblePipe jobs at all | nothing is pruned |

Without `--apply` it only lists what it would delete. With it, it deletes
those folders and writes their names to
`export/other/logs/prune-<date>.log`.

## From a shell

```
python -m tumblepipe.archive plan    [project] [--profile proof|latest|full]
                                     [--with-caches] [--with-renders] [--list FILE]
python -m tumblepipe.archive create  [project] --out ARCHIVE.zip [same options] [--part-size 50G]
python -m tumblepipe.archive verify  ARCHIVE.zip
python -m tumblepipe.archive restore ARCHIVE.zip --to FOLDER [--name NEW] [--overwrite]
python -m tumblepipe.archive rebuild [restored project] [--dry-run] [--hython PATH]
```

`project` defaults to `$TH_PROJECT_PATH`. `plan` reads the project and writes
nothing. `--list` also writes every file with its decision and reason, as
tab-separated text. Put TumblePipe's `python/` folder on `PYTHONPATH`;
nothing else is needed, and Houdini isn't (except for `rebuild`).
