# TumblePipe
An example of an all Houdini pipeline for animation film production, developed for the Turbulence short film.

This readme is here to help you get up and running with this project, and get it ready for use on your own Houdini creations. For documentation on how to do further development, to understand what the various parts of the project does, and how to extend the project for your own purposes, there will be additional external documentation in the future.

# Disclaimer
- This project is free, and you can do with it and the resources you find here as you please.
- The project was made by, and for, a small studio. So the design decisions made, were with regards to what we have resources to manage.
    - We have kept in mind that we might need to scale up and down in the future; team size, project size, locations etc.
    - The project should work well for individuals and small teams (up to maybe 20 artists), but is currently not appropriate for larger teams.
- We do not have the resources to give tech support, but would be happy to receive feedback and questions.
- We will continue to update this project, as long as we use it ourselves.
- We won't give any deprecation warnings, and can not give a backwards compatible guarantee for future versions.

# Project Structure
Here is a sparse walkthrough of this repository's directory structure:
- `./deadline/Shell` (A custom deadline plugin, that the farm scripts expects to be available)
- `./examples`
    - `config` (An example pipeline configuration, could be used as a starting point)
    - `turbulence_houdini.bat` (An example Windows .bat launcher for running Houdini and configuring the TumblePipe package)
- `./houdini`
    - `TumblePipe` (The TumblePipe Houdini package)
        - `otls` (The directory containing all the pipeline HDAs)
        - `python/1x/tumblehead` (The Python package containing all the pipeline scripts)
        - `python3.11libs/external` (The directory that will contain the pipeline's Python virtual environment)
    - `TumblePipe.json` (The TumblePipe Houdini package description)

# Download
You can find downloads for the zipped package releases on the GitHub repository, under [releases](https://github.com/tumblehead/TumblePipe/releases).

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
Installing a Houdini package is simply as unzipping the downloaded release.

1) Where you wan't to place the package then depends on your studio setup:
    - For Individuals
        - You could have a location where you have placed other Houdini packages already
        - You could also locate it in the houdini preference directory, under the user documents directory
        - You could locate and bundle the package per project
    - For Teams
        - You could have it on a shared location, e.g. a shared file server
        - You could install it for each workstation locally

2) You then need to make Houdini aware of the TumblePipe Houdini package
    - For Individuals
        - A simple way is to create a launcher script, e.g. on Windows with .bat file. For an example check out `./examples/launcher/windows_launcher.bat`
    - For Teams
        - You might have your own system for managing environments and launching DCCs
        - You could also use a launcher script, as described above for individuals

# Configuration
The configuration of the pipeline will allow it to customize it to your house rules, as well as informing the tools which assets and shots are in your project, as well as departments etc.

For an example, and as a starting point for you own configurations, you can copy and use the example config that can be found in `./examples/config`

## Framework
The approach we took to make the various scripts in the pipeline flexible enough to handle different projects, was to factor out a small framework of interfaces for you to implement. This allows you to customize the pipeline to fit with your house rules. This is also where the pipeline would allow you to hook it up with other systems; such as databases, storage systems and other project management software.  

You will need to create a set of Python modules in a shared config directory, and implement some methods and functions:
- config_convention.py (responsible for managing workspace configuration)
- naming_convention.py (responsible for managing naming of items in the pipeline context)
- storage_convention.py (responsible for mapping project resource URIs to filesystem paths)
- render_convention.py (responsible for managing render configuration)

The file name of the these Python modules are not optional, the pipeline expect these modules to exist, and be placed in the root of your config directory.

As an additional example of a pipeline configuration, please check out the Turbulence project work files. You can find this resource along with other information on the Turbulence project's dedicated SideFX [tech-demo](https://www.sidefx.com/tech-demos/turbulence/) website.

# Deadline and Render Farm
In order to use the render farm scripts, you'll need to set up Deadline workers with the same prerequisites as your workstations.

## Render Farm Prerequisites
Each farm worker needs the same WSL2/Ubuntu setup as described in the Prerequisites section:

- **WSL2 and Ubuntu**: Follow the same installation steps
- **Required Tools**: Install UV, ffmpeg, openimageio-tools, and opencolorio-tools
- **Drive Mapping**: This is **critical** - workers must have identical drive mappings in `/etc/fstab` to access project files
  - If your workstation has `P: /mnt/p drvfs defaults 0 0`, all workers need the same mapping
  - Without matching drive mappings, farm jobs will fail to read/write files

## Installing the UV Deadline Plugin
TumblePipe uses a custom Deadline plugin that leverages Astral UV for fast Python environment management.

1) **Copy the plugin** to your Deadline repository:
   ```
   Copy deadline/UV/ to <DeadlineRepository>/custom/plugins/UV/
   ```

2) **Restart Deadline workers** to load the new plugin

3) **For detailed configuration**, see the [UV Plugin README](deadline/UV/README.md) which covers:
   - Performance tuning (cache directories, Python pre-installation)
   - Troubleshooting common issues
   - Advanced usage and optimization

## Additional Resources
- **Deadline Plugin Development**: [Thinkbox Documentation](https://docs.thinkboxsoftware.com/products/deadline/10.1/1_User%20Manual/manual/manual-plugins.html)
- **UV Documentation**: [Astral UV Docs](https://docs.astral.sh/uv/)