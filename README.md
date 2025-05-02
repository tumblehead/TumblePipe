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
In order to be able to use the render farm scripts, there is some work that needs to be done regarding setting up the render workers.

🚧 This will be documented in the future 🚧