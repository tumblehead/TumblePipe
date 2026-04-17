# TumblePipe
A small studio pipeline for animation and VFX projects in Houdini, developed for the Turbulence short film.

This readme will help you get up and running with TumblePipe. For documentation on further development, understanding the various parts of the project, and extending it for your own purposes, there will be additional external documentation in the future.

# Disclaimer
- This project is free, and you can do with it and the resources you find here as you please.
- The project was made by, and for, a small studio. So the design decisions made, were with regards to what we have resources to manage.
    - We have kept in mind that we might need to scale up and down in the future; team size, project size, locations etc.
    - The project should work well for individuals and small teams (up to maybe 20 artists), but is currently not appropriate for larger teams.
- We do not have the resources to give tech support, but would be happy to receive feedback and questions.
- We will continue to update this project, as long as we use it ourselves.
- We won't give any deprecation warnings, and can not give a backwards compatible guarantee for future versions.

# Project Structure
- `otls/` — Houdini Digital Assets (HDAs) in text format for version control
- `python/1x/tumblepipe/` — Pipeline Python modules
- `python3.11libs/` — Python libraries loaded by Houdini at startup
- `scripts/` — Houdini startup scripts (123.py, etc.)
- `desktop/` — Houdini desktop layout
- `python_panels/` — Houdini Python panels (project browser)
- `ocio/` — OpenColorIO configuration
- `resources/` — Pipeline resource files
- `resolver-src/` — Source code for the `entity://` USD asset resolver (tumbleResolver)
- `hpm.toml` — HPM package manifest

# Download

## Via [TumbleTrove Desktop](https://tumbletrove.com/desktop) (recommended)
The [TumbleTrove Desktop](https://tumbletrove.com/desktop) app lets you browse, install, and update Houdini packages — including TumblePipe — with a graphical interface. No command line required.

## Via [HPM](https://hpm.readthedocs.io)
[HPM](https://hpm.readthedocs.io) is the Houdini Package Manager — a CLI tool for installing and managing Houdini packages.

```bash
hpm add tumblepipe --git https://github.com/tumblehead/TumblePipe
```

## Manual
You can find zipped package releases on the GitHub repository under [releases](https://github.com/tumblehead/TumblePipe/releases).

# Prerequisites
Before installing TumblePipe, you'll need to set up a Linux environment with the required tools. This is necessary both for workstations and render farm workers.

## WSL2 and Ubuntu
TumblePipe's tools and farm scripts run in a Linux environment, which on Windows is provided by WSL2 (Windows Subsystem for Linux):

1) **Install WSL2** by following Microsoft's official guide: [Install WSL on Windows](https://learn.microsoft.com/en-us/windows/wsl/install)
    - The default Ubuntu distribution works well
2) This applies to:
    - **Individuals**: Your workstation where you run Houdini
    - **Teams**: Both artist workstations and render farm workers

## Required Tools
Install the following tools on Ubuntu/WSL2:

### Astral UV
UV provides Python environment management for the pipeline's farm scripts.

- **Installation Guide**: [UV Getting Started](https://docs.astral.sh/uv/getting-started/installation/)
- **Quick Install**:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

### Image and Video Tools
The pipeline uses these tools for render processing:

```bash
sudo apt install ffmpeg
sudo apt install openimageio-tools
sudo apt install opencolorio-tools
```

## Drive Mapping
Your Windows project drives must be accessible from WSL2 for the pipeline to read/write files.

1) Edit `/etc/fstab` in Ubuntu to mount your project drives:
   ```bash
   sudo nano /etc/fstab
   ```

2) Add mount entries for your project drives, for example:
   ```
   P: /mnt/p drvfs defaults 0 0
   ```

3) The drives you mount must match what your launcher scripts reference in environment variables like `TH_PROJECT_PATH` and `TH_PIPELINE_PATH`

4) This drive mapping is required on:
    - **Individuals**: Your workstation
    - **Teams**: All artist workstations and **all render farm workers** (critical for file access)

## Environment Variables
The pipeline uses several `TH_*` environment variables (detailed in the Configuration section). Note that:
- `TH_USER` is optional and defaults to your system username
- Other variables like `TH_PROJECT_PATH` and `TH_PIPELINE_PATH` are set in your launcher script

# Installation
Install TumblePipe as a Houdini package. Where you place it depends on your studio setup:

- **Individuals**: Place it alongside your other Houdini packages, in your Houdini preference directory, or bundle it per project.
- **Teams**: Place it on a shared file server, or install locally per workstation.

You then need to make Houdini aware of the package. A simple way is to create a launcher script (e.g. a Windows `.bat` file) that sets the required environment variables and launches Houdini.

# Configuration
The configuration allows you to customize TumblePipe to your house rules, as well as informing the tools which assets and shots are in your project, departments, etc.

## Framework
The pipeline uses a small framework of interfaces for customization. You implement these Python modules in a shared config directory:

- `config_convention.py` — workspace configuration
- `naming_convention.py` — naming of items in the pipeline context
- `storage_convention.py` — mapping project resource URIs to filesystem paths
- `render_convention.py` — render configuration

The file names are not optional; the pipeline expects these modules to exist in the root of your config directory.

As an additional example, check out the Turbulence project work files on SideFX's [tech-demo](https://www.sidefx.com/tech-demos/turbulence/) website.

# Deadline and Render Farm
For render farm usage, set up Deadline workers with the same prerequisites as your workstations.

## Render Farm Prerequisites
Each farm worker needs the same WSL2/Ubuntu setup:

- **WSL2 and Ubuntu**: Follow the same installation steps
- **Required Tools**: Install UV, ffmpeg, openimageio-tools, and opencolorio-tools
- **Drive Mapping**: **Critical** — workers must have identical drive mappings in `/etc/fstab` to access project files

## Deadline UV Plugin
TumblePipe uses a custom Deadline plugin for UV-based render processing. The plugin is maintained separately:

- **Repository**: [tumblehead/deadline-uv-plugin](https://github.com/tumblehead/deadline-uv-plugin)
- **Installation**: Copy the plugin contents to `<DeadlineRepository>/custom/plugins/UV/` and restart Deadline workers.

## Additional Resources
- **Deadline Plugin Development**: [Thinkbox Documentation](https://docs.thinkboxsoftware.com/products/deadline/10.1/1_User%20Manual/manual/manual-plugins.html)
- **UV Documentation**: [Astral UV Docs](https://docs.astral.sh/uv/)
