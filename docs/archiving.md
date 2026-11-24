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
`recipes` and `library` folders. The scenes load those. It takes every
edit's working versions too (the `edits` root: small JSON files, the only
record of a cut before it is published); published edits and the media
library are exports, kept as exports are. A published edit's `media/`
folder holds the movies it plays as hard links, which the archive copies
as files: each kept version carries its own copy of its movies.

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
| The `edit` root (`storage.json`'s, Resolve projects and their exports; not the editor's `edits`) and every other top-level folder: client material, `delivery`, `temp`, … | not pipeline data |

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
  workfile of each department (`.hip`, `.hiplc` or `.hipnc`) and every archived `.usda` are
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
- `.hiplc` / `.hipnc` latest workfiles that can't be read for paths. Indie
  and Apprentice workfiles are read like a `.hip` (the same text sections,
  each behind a `HouLC`/`HouNC` header in place of cpio's); only one in a
  form the reader doesn't know is listed here, so check it in Houdini.

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
never overwritten, and a version name must be spelled the standard way
(`v0001`, not `v00001`), so a rebuild never starts a second folder for the
same version.

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

## Cleaning up a project

Archiving never deletes anything. Freeing space on the server is a separate
tool: **Clean Up Project** in TumbleTrove Desktop's Scripts panel. A project
only grows (every export, render and save adds a version), and on
HideAndReek 1.6 TB of its 2.5 TB was nothing anyone would open again.

The window opens on the project it was launched for. The rules on the left
say what counts as old in each category; press **Scan** and it reads the
whole project under them, showing its progress as it goes (**Indexing**,
then **Sizing**, each category filling up as it finds things). It then
shows, per category, what would go and what stays, each with its reason,
and a coloured bar of each category's share of the space freed. Untick a
category to leave it out. Nothing is deleted until you press **Purge** and
confirm; changing a rule after a scan asks for a new scan first. A running
purge can be paused, resumed or stopped: it stops between items, what went
stays gone, and the window scans again to show what is left. Everything
removed is written to `export/other/logs/cleanup-<date>.log`.

Everything the cleanup removes is made by a workfile: an export, render or
cache is exported, rendered or cached again from it, a farm job folder is
written again by the next submit. Houdini backups and deleted entities are the
exceptions, and they go only past the ages set for them.

| Category | What goes | What always stays |
|---|---|---|
| **Old export versions** | versions older than the newest 3 finished of each export | anything a current staged build or a latest workfile loads ([below](#pruning-old-export-versions)) |
| **Old render versions** | versions older than the newest 2 complete of each render layer; check renders older than 14 days, with their review movies | the highest version of a layer, and anything a workfile, an edit or a kept composite names ([below](#old-render-versions)) |
| **Old cache versions** | `th::cache` versions older than the newest 2 of each cache, with or without a record of what made them | anything a latest workfile or an export on disk still loads ([below](#old-cache-versions)) |
| **Houdini backups** | `backup/*_bakN.hip` of a workfile version that has since been saved as a newer version; of the latest version, all but the newest 5 | any backup newer than 7 days ([below](#houdini-backups)) |
| **Farm job folders** | folders neither Deadline nor the local farm can run from again | anything requeueable ([below](#pruning-farm-job-folders)) |
| **Deleted entities** | `_deleted/<date>-<time>/` folders older than 30 days | anything else in a `_deleted` folder |

From a shell, where it lists unless given `--apply`, the package's
`bin/<platform>/cleanup` (the program the window is) or the Python it was
ported from take the same arguments and print the same listing:

```
cleanup                      [project] [--only renders,backups] [--context shots/sq010]
python -m tumblepipe.cleanup [project] [--only renders,backups] [--context shots/sq010]
                             [--keep-exports 3] [--keep-renders 2] [--check-render-days 14]
                             [--keep-caches 2] [--keep-backups 5] [--backup-days 7] [--deleted-days 30]
                             [--job-days 2] [--local-job-days 14] [--apply]
```

`cleanup` with no arguments, or with `--gui`, opens the window instead.

`--context` narrows exports, renders, caches and backups to shots, assets
or a path under them; job folders and deleted entities are of the whole
project. It is for the shell only: the window always scans the whole
project.

Playblasts, dailies, turntables, the grade's mattes and workfile versions
are not touched.

### Old render versions

Every render writes a new `render/<entity>/<department>/<layer>/vNNNN`
under the render root, and these are most of a project's bytes (2.1 TB on
HideAndReek, 1.4 TB of it older than the newest two versions). A version
goes only when none of these is true:

| Kept because | |
|---|---|
| it is one of the **newest 2 complete** versions of its layer, or newer | complete: every frame its `context.json` records is there in every AOV. The shot's render is the newest complete one, so partial renders and multi-range renders on top of it never push it out |
| it is the **highest** version of its layer | the next render takes the highest number plus one; a number used twice would hand everything naming the old version the new frames |
| a **latest workfile** names it | `th::import_render` set to a version, or frozen by the farm |
| an **edit** names it | a published edit plays a render's frames by path, a working edit can pin a take, and the sync to the edit root records the version it copied |
| a kept **composite** version read it | followed through: what that composite's inputs read stays too |
| it was **written to in the last two days** | it may be rendering |
| it holds a **`.keep` file** | put an empty file named `.keep` in a version folder to keep it whatever else is true: a delivered render |

A check render (Farm Submit's Step) is never the shot's render, so it goes
by age alone, under the same guards.

Workfiles are read as for exports (`.hip`, `.hiplc` and `.hipnc`). A DaVinci Resolve
project is not read either: if a grade reads frames straight from the
render root, at a version older than the newest two, mark that version
with `.keep` first.

### Old cache versions

A cache is the one derived thing other files point straight into: a
`th::cache` node loads the exact version it is set to, and a published
export references a cache's files instead of copying them. So a
`cache/<name>/vNNNN` or `lops_cache/<name>/vNNNN` folder (in a workfile
folder or under the proxy root) goes only when none of these is true. Whether
it has a `context.json` record, or names a workfile still in the project, does
not matter: it is made again by caching again.

| Kept because | |
|---|---|
| it is one of the **newest 2** versions | so the highest always stays: a number used twice would hand a node set to the old version the new files |
| a **latest workfile** names it | a node whose Name and Version are its, in any department, or any path into it. A path is matched by how it ends (drives and shares are spelled differently per machine), so it also keeps the cache of the same name under the proxy root |
| an **export** references it | every USD layer under the export root is read for the files it references. Where USD cannot be loaded, no cache is deleted |
| it was written to in the last two days, or holds a `.keep` file | |

What it deletes, **Rebuild Caches** could make again from the workfile
(not bit for bit, for a cache that depends on a random seed or an input
that has changed: mark such a version with `.keep`).

### Houdini backups

Houdini copies the file it is about to overwrite into `backup/` on every
save and never removes one (63 GB on HideAndReek). TumblePipe saves a new
workfile version each time, so a backup is only a safety net for the
version being worked on: a backup of `…_v0012` is dead weight once
`…_v0013` is saved. Those go, and of the latest version's backups all but
the newest few. No backup newer than a week goes, and a file in `backup/`
not named like a backup of a workfile version is left alone.

### Deleted entities

Deleting an asset, a shot or an edit moves its folders to
`_deleted/<date>-<time>/` in every storage root, so a mistake can be put
back by hand. Clean Up empties those once the delete is a month old.

## Pruning farm job folders

The farm job category of Clean Up Project, on its own:

```
python -m tumblepipe.prune [project] [--apply] [--min-age-days 2] [--local-min-age-days 14]
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
| a batch in this machine's local farm queue runs from it (queued, failed or done) | no, the batch can be retried |
| it was written for the local farm and no batch here uses it | yes, once it is older than `--local-min-age-days`: the batch may be in another machine's queue |
| Deadline is installed here but cannot be asked, or reports no TumblePipe jobs at all | nothing is pruned |

On a machine without Deadline the project renders on the local farm, so no
Deadline job uses any folder: folders go by the age and local-queue rows above.

Without `--apply` it only lists what it would delete. With it, it deletes
those folders and writes their names to
`export/other/logs/prune-<date>.log`.

## Pruning old export versions

Every export writes a new version folder, and nothing removes the old ones:
on HideAndReek, 2,000 export versions held 157 GB. The export category of
**Clean Up Project** finds what can go and purges it when you confirm; on
its own, from a shell:

```
python -m tumblepipe.prune exports [project] [--keep 3] [--context shots] [--apply]
```

It keeps, for every export (an entity's department in one channel, a shot's
root layer), the newest `--keep` finished versions, and also every version
that:

| Kept because | |
|---|---|
| it is **unfinished** (no `context.json`) | it may still be being written; it does not count towards `--keep` either |
| a **current staged build** uses it | the newest staged build of every shot, asset and Root pins exact versions of its layers and of the staged assets it brings in, and those pins are followed too |
| a **latest workfile** pins it | the newest `.hip` in every department folder is read for the versions its nodes are set to (`th::import_layer` always loads the exact version it was set to; the other import nodes can be set to one) |

`.hip`, `.hiplc` (Indie) and `.hipnc` (Apprentice) workfiles are all read:
a `.hiplc` / `.hipnc` holds the same text sections as a `.hip`, each behind
a `HouLC`/`HouNC` header in place of cpio's. One in any other form (no such
header, or a first section that is not text) is not guessed at: the listing
says how many latest workfiles it could not read: versions only they load
are not kept for them, and are exported again from those workfiles if wanted. An older workfile's pins are not read either: open it
on latest, or re-export what it needs.

`--context` narrows it to shots, assets or a path under them
(`shots/sq010`). Staged builds themselves (a few KB each) and
`export/other` are never touched. Renders, playblasts and dailies are not
exports and are not touched.

Without `--apply` (or until you confirm in the window) nothing is deleted.
Each version deleted is written to `export/other/logs/prune-<date>.log`.

## From a shell

```
cleanup                              [project] [--only exports,renders,caches,backups,jobs,deleted] [--apply]
python -m tumblepipe.cleanup         [project] [--only exports,renders,caches,backups,jobs,deleted] [--apply]
python -m tumblepipe.prune [jobs]    [project] [--apply] [--min-age-days 2]
python -m tumblepipe.prune exports   [project] [--keep 3] [--context shots] [--apply]
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
nothing else is needed, and Houdini isn't (except for `rebuild`). `cleanup`
is a program of its own, in the package's `bin/<platform>/` folder, and
needs neither.
