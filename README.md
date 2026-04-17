# TumblePipe

A small studio pipeline for animation and VFX projects in Houdini.

TumblePipe is distributed as an HPM package at `tumblehead/tumblepipe`
and installs into Houdini 21+ via the [HPM](https://github.com/3db-dk/hpm)
package manager. The package provides:

- A shelf of `th_`-prefixed Houdini operators (SOP / LOP / COP / VOP)
  spanning asset import, look-dev, lighting, rendering, and playblast
  tooling.
- A USD asset resolver (`tumbleResolver`) that maps `entity://` URIs to
  on-disk paths, built per-platform and shipped as a compiled plugin
  under `resolver/<platform>/houdini<major>/`.
- A project browser pypanel, keymap, desk layout, OCIO config, and
  studio-pipeline python modules under `python/1x/tumblepipe`.

See the [public mirror](https://github.com/tumblehead/TumblePipe) for
tagged releases and download links.
