# Installation

TumblePipe runs on Windows, Linux, and macOS, but several pipeline tools (the
render-farm submission scripts in particular) expect a Linux-like environment.
On Windows, we provide that via WSL2.

## Prerequisites

### WSL2 and Ubuntu (Windows only)

TumblePipe's farm scripts run in a Linux environment. On Windows this is
provided by WSL2.

1. Install WSL2 following Microsoft's
   [official guide](https://learn.microsoft.com/en-us/windows/wsl/install).
   The default Ubuntu distribution works well.
2. This applies to both artist workstations *and* render farm workers.

### Required Linux tools

Install these on Ubuntu / WSL2:

```bash
# Astral UV — Python environment management for farm scripts
curl -LsSf https://astral.sh/uv/install.sh | sh

# Image and video tools used by render processing
sudo apt install ffmpeg openimageio-tools opencolorio-tools
```

See the [UV installation guide](https://docs.astral.sh/uv/getting-started/installation/)
for alternative install methods.

### Drive mapping

Windows project drives must be accessible from WSL2 using the same mount
points as the Windows side. Edit `/etc/fstab` in Ubuntu:

```text
P: /mnt/p drvfs defaults 0 0
```

The mounts must match what your launcher scripts reference in `TH_PROJECT_PATH`
and `TH_PIPELINE_PATH`. This is critical on render farm workers — without
matching drive paths, workers cannot read project files.

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

The simplest activation path is a launcher script (for example a `.bat`
file on Windows) that sets the required environment variables and launches
Houdini. See [Configuration](configuration.md) for the variables TumblePipe
expects.
