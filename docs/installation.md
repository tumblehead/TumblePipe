# Installation

TumblePipe runs on Windows, Linux, and macOS. On Windows render-farm workers,
tasks run in native Windows python and drive Houdini's bundled tools directly —
`husk.exe`, the Windows USD resolver, `iconvert`, and Houdini's
`hoiiotool`/`hffmpeg` for image and video processing.

## Prerequisites

### Houdini

Render-farm workers need a Houdini install matching the project's pinned version.
It provides everything the farm uses: `husk`, the USD resolver, `iconvert`, and
the bundled `hoiiotool` (OpenImageIO) and `hffmpeg`.

> The **legacy UV farm plugin** additionally needs WSL2 + UV. See
> [Deadline and the render farm](deadline.md).

### Drive mapping

Render-farm workers must map the project drives to the same letters the
workstations use (e.g. `P:`), so a path like `P:\...` referenced in
`TH_PROJECT_PATH`/`TH_PIPELINE_PATH` resolves identically everywhere. Without
matching drive letters, workers cannot read project files.

## Installing TumblePipe

There are three supported install paths. Pick the one that fits your workflow.

### TumbleTrove Desktop (recommended)

The [TumbleTrove Desktop](https://tumbletrove.com/desktop) app lets you
browse, install, and update Houdini packages — including TumblePipe — through
a graphical interface. No command line required.

### HPM (command line)

[HPM](https://hpm.readthedocs.io) is the Houdini Package Manager.

```bash
hpm add tumblepipe --git https://github.com/tumblehead/TumblePipe
```

### Manual install

Zipped archives per release are available on the
[GitHub releases page](https://github.com/tumblehead/TumblePipe/releases).
Extract the archive and register the package with Houdini via a package JSON
or launcher script (see below).

## Making Houdini aware of TumblePipe

Where you place the package depends on your setup:

- **Individuals** — place it alongside your other Houdini packages, in your
  Houdini preferences directory, or bundle it per project.
- **Teams** — place it on a shared file server, or install locally per
  workstation.

If you installed via TumbleTrove Desktop, click **Configure** on the
TumblePipe package card to launch the project setup wizard — it points
the package at an existing project on disk or scaffolds a new one from
the bundled template. See
[Project setup wizard](configuration.md#project-setup-wizard).

For HPM or manual installs, the simplest activation path is a launcher
script (for example a `.bat` file on Windows) that sets the required
environment variables and launches Houdini. See
[Configuration](configuration.md) for the variables TumblePipe expects.
