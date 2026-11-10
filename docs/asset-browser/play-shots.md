# The editor

TumblePipe's editor cuts and plays *edits*: the shots, media and slugs of a
film on tracks, saved as versions and published like any other export. It
opens in its own window, outside Houdini, and plays every frame as made: a
playblast as it is, a render through the project's OCIO config.

Right-click a shot card and choose **Play**, or select several shots and
choose **Play N Shots**, to open them back to back in the editor as an
untitled edit. Nothing in the project changes until you save it, and
Resolve is not involved.

An edit with nothing in it yet starts as the whole film: the editor
opened on no shots, and an edit that has never been saved, open with every
shot of the project end to end, in sequence and shot order, each its play
range and best available take. Cut it down from there; the Shots bin still
holds every shot to put back. Once saved, an edit opens as it was saved,
however many shots the project has since gained.

## What a shot clip plays

A shot has **takes**:

- each of its **playblasts**, every department and every version;
- each of its **renders**, every render department, layer and version whose
  frames are all there, as an EXR sequence: its `beauty` AOV, or its first
  AOV when it has no beauty. A render still running, or one that stopped
  short, is not a take yet.

A shot clip keeps a *rule*, not a file: which take it shows is worked out
each time the edit opens, so a new playblast or render shows up in the cut
by itself. A new clip's rule is **Best available**: a render when the shot
has one, else a playblast; the most downstream department; for a render the
`slapcomp` layer, else `default`; the newest version. In the composite
department the `slapcomp` layer is the comp itself (`th::export_comp` on
Layer `comp`), not an oiiotool stack of the channels.

Right-click a shot clip to change its rule. **Best available** goes back to
the default. The **Source** (playblast or render), **Department** and
**Version** submenus list what the shot has for the take on screen; the one
shown is marked. Choosing a version of the shown department pins that
version; choosing a source or department follows its newest.

A shot with no take its rule picks (a planned shot, or one with nothing
made yet) plays as an empty clip saying so; it still has its length in the
cut. While a take opens, which can take a few seconds on the file server,
its clip says **Opening…**.

### Render layers stacked

A shot's render layers are its channels (`default` first, then the others).
Where they were made apart, a department also has **layers** takes
(`animation v0015 layers`):

- a playblast version made with th::playblast's **Render Layers** has a
  movie per layer (that layer's prims on a transparent background); its
  layers take stacks them;
- a render version with two or more channel layers whose frames are all
  there stacks them (the slapcomp is not one of them: it comps them
  already). Layers whose frame range differs from the bottom one's are left
  out.

The layers are stacked in channel order, the first at the bottom, each laid
"over" the ones below: playblast layers over black as they are, render
layers in scene linear and then through the display transform like any
render. Best available never picks a layers take; choose it from the clip's
menu.

## The window

Over the picture is the toolbar: the edit's name and version (with `*`
while it has unsaved changes), **Master edit** (see below), **Burn-ins**, and
the timecode. Under the picture are the display, view and exposure.

The actions are in the menu bar: on Windows the window's, on macOS the one
at the top of the screen, and on Linux a bar drawn at the top of the window
(press a title, or Alt with its underlined letter, or F10). **File** (open,
save, publish, the master edit, new shot, import media), **Edit** (undo,
redo, split, lift, ripple delete, marker, selection, and putting the bin's
pick in the cut), **Playback** (play, step, jump by a second or to an edit
point, the start and end) and **View** (burn-ins). Each item shows its
shortcut, and is greyed while it does not apply: while a dialog is open, or
the cut's actions on a published edit. With a menu open, an item's
underlined letter chooses it. The single-letter keys (**S** split, **M**
marker, **B** burn-ins) act only with neither Ctrl (Cmd on macOS) nor Alt
held.

Beside the picture are the bins. **Shots** lists the project's shots,
each sequence's under its name;
**Media** its media library (movies, stills and sound copied into the
project as versioned items: **Import…** copies a file in). Select an item
and press **Insert** (the cut opens at the playhead and everything after it
moves on, on every track) or **Overlay** (it goes over what is there).
Double-clicking an item inserts it. A movie brings its sound onto an audio
track.

Under the picture are the tracks: a ruler, then the video tracks (the top
track on top), then the audio tracks. Each clip shows its first frame, its
name and its take, its fades as ramps, and an outline when selected. A
shot's clip is coloured by the department its take comes from (every
animation take the same muted blue, say), so the cut shows at a glance how
far each shot has got; a clip with nothing to play is hatched, and says
why. The ruler marks the markers, the playhead, and in a lighter strip the
frames already decoded. Amber means *now*: the playhead, the timecode, a
clip being dragged.

- Click or drag on the ruler, or on empty track, to seek.
- Click a clip to select it; Shift or Ctrl-click adds it or takes it out.
- Drag a clip to move it, onto another track of its kind too. With Alt held
  it slips: the clip stays and the frames in it move.
- Drag a clip's edge to trim it; what follows moves with it. With Alt held
  the cut rolls: the clip and its neighbour change together and the length
  holds.
- Drag along a clip's top band to fade it in (left half) or out (right
  half). Where a clip on a higher track fades over one below, the two
  dissolve.

Every change is one step of undo, however many clips it moves.

| Key | Does |
| --- | --- |
| Space | play / pause |
| Left / Right | step one frame back / forward |
| Alt+Left / Alt+Right | jump one second back / forward |
| Ctrl+Left / Ctrl+Right | jump to the previous / next cut |
| Home / End | the first / last frame |
| S | split the selected clips at the playhead (none selected: the top video clip there) |
| M | a marker at the playhead |
| Delete | lift the selected clips, leaving a gap |
| Shift+Delete | ripple delete: what follows closes the gap |
| Ctrl+Z / Ctrl+Shift+Z (Ctrl+Y) | undo / redo |
| Ctrl+O | open an edit |
| Ctrl+S / Ctrl+Shift+S | save / save as |
| Ctrl+A | select every clip |
| Escape | clear the selection |
| B | show / hide the burn-ins |

On macOS, use Cmd for Ctrl.

The keys are the timeline's whatever you clicked last: the Shots/Media switch
and the bin's buttons do not keep the keyboard. A bin list you click in takes
Up/Down, Home/End and Enter to move through it; click the timeline to give
the keys back.

Playback shows every frame: when the next frame is not decoded yet, the
editor waits for it instead of skipping it. While there is sound, playback
keeps to it. A render's frames are large, so each can take seconds to come
from the file server; the frame under the playhead is read first.

Burn-ins over the picture name what is on screen: the shot top left, its
take top right, the edit's timecode bottom left and the frame bottom right:
the shot frame and its place in the take (`1046 (46 / 68)`). A playblast
records which shot frame it starts on; one made before TumblePipe recorded
that shows only its place (`46 / 68`) until
`scripts/fix_playblast_start_frames.py` gives it its start frame (see
[Older playblasts](#older-playblasts)). They are only drawn in the editor:
nothing is written into the media.

Playblasts are shown as they are. EXR takes are scene-linear, so the editor
shows them through the project's OCIO config (`$OCIO`, which the TumblePipe
package sets; the editor does not start without one). It reads the input
colour space from the EXR (ACEScg for TumblePipe renders; a render that
does not name one cannot play) and starts on the config's default display
and view. The window around the image is neutral grey, so it does not shift
how the image's colours read.

## Saving and publishing

An edit is an entity of the project, `entity:/edits/<name>`. **Open…** lists
the project's edits, each with its newest working version and newest
published one; the edit chosen opens in a window of its own (an untitled
window it was chosen from closes when it holds nothing of yours: empty, or
the project's shots unchanged since it opened). **Edits** in TumbleTrove
Desktop's Scripts panel starts the editor on the whole film: the project's
[master edit](#the-master-edit), or when the project has none, an untitled
edit of every shot.
**Save** writes
its next working version (`v0001`, `v0002`, ...) with a note; **Save As**
names a new edit. While an edit is open it is autosaved every minute;
when the editor finds an autosave newer than the working version it opens,
it offers it back. Closing with unsaved changes asks first.

**Publish** freezes the edit: every shot clip's rule is written down as the
exact take it plays, so a published version always plays the same frames.
A publish also writes the edit as OpenTimelineIO (`.otio`, which Resolve
imports: dissolves become transitions, other fades markers). Its movies
are linked into the version's `media/` folder under names of their own,
such as `S_010_animation_playblast_v0003.mp4` (hard links, copies where a
link cannot be made), and the `.otio` refers to those. Resolve takes movies
with the same file name and start timecode for one, and every playblast is
`vNNNN.mp4`. A movie places by its timecode, so a playblast made before
playblasts recorded their start frame does not link in Resolve until it is
playblasted again or `scripts/fix_playblast_start_frames.py` has written
it. A published version opens read-only; **Save As** makes a new edit from it.

In the Asset Browser, a project with edits has an **Edits** section: a card
per edit, with its latest published version and newest working version, and
whether it is the master edit. Right-click a card: **Open in Editor** opens
its newest working version (an edit never saved opens empty), **Play
Published** its latest published version, read-only (there only once it has
one), **Open Folder** its working folder, and **Set as Master Edit** makes
it the master edit (the master edit's card is ticked **Master Edit** and
offers **Clear Master Edit** instead). Edits are made in the editor, not in
the browser.

A published version can be rendered into a movie on the farm: the **Edits**
tab of [Farm Submit](submit-jobs.md#the-edits-tab) offers each edit's
published versions, the latest first, with **Render Edit**. A worker plays
the version through the editor without a window, as you see it (fades,
dissolves and the project's OCIO view included) and with its sound mixed,
and encodes `render:/edits/<name>/<name>_edit_vNNNN.mp4`: H.264 with AAC
sound and a timecode track from the edit's start timecode, ready for
Resolve or for sending out. The same render runs by hand with
`th_player --project <root> --ocio <config> --render entity:/edits/<name>@vNNNN --out <dir>`,
which writes the frames as PNGs, the sound as a WAV and `render.json`.

### The master edit

One edit can be the project's **master edit** (the toolbar's checkbox): the
cut the shots' frame ranges follow, and the one **Edits** in TumbleTrove
Desktop opens. Publishing it lists the shot changes its
cut makes, each with a tick box:

- a planned shot (**New shot…** in the Shots bin puts one in the cut, with
  its sequence, name and length) is created with the range it has;
- a shot whose clip is longer or shorter than its range gets the new range;
- a shot the master edit used to have and no longer does is retired. These
  are not ticked: tick them to retire the shots.

A change is flagged when the shot's take does not cover the new range (a
playblast made before the range grew). The ticked changes are written after
the publish, and the shots bin follows them.

## Running it by hand

The editor ships inside the TumblePipe package, as
`bin/<platform>/th_player`:

```
th_player [--project <root>] [--ocio <config>] [--edit entity:/edits/<name>[@vNNNN] | --shots <shot uri>...]
```

`--edit` opens the edit's newest working version, or with `@vNNNN` that
published version (an edit never saved, every shot of the project);
`--shots` the shots end to end as an untitled edit; neither, every shot of
the project as an untitled edit. The project is `--project`, else
`$TH_PROJECT_PATH`; the OCIO config `--ocio`, else `$OCIO`. When either is
missing, or the edit does not open, the window says why.

It is built for Windows, Linux and macOS. See
[Contributing and development](../development.md#sequence-player) for how it
is built.

## Older playblasts

A playblast records the shot frame its first picture is: a `start_frame` tag
and a timecode track that starts on that frame (frame 1001 at 24 fps is
`00:00:41:17`), which Resolve reads as the clip's source timecode. Playblasts
made before that record nothing. `scripts/fix_playblast_start_frames.py`
gives them it, once:

```text
python scripts/fix_playblast_start_frames.py P:/HideAndReek --houdini 22.0.429
python scripts/fix_playblast_start_frames.py P:/HideAndReek --houdini 22.0.429 --apply
```

Run it with Houdini's own Python (`python313/python.exe` in the Houdini
folder; no licence is taken). Without `--apply` it only lists. For every movie
in the shots' playblast folders (`--shot SEQUENCE/SHOT` for some) it says
one of:

| Says | Means |
|---|---|
| (nothing) | the movie records its start frame already |
| `would fix` / `fixed` | its frame count is its shot's full range (the frames plus roll, which a playblast covers), so it starts at that range's first frame. The movie is copied with the start frame added (no re-encode), the copy is checked, then it replaces the movie |
| `cannot place` | anything else, with the reason: usually the shot's range changed since the playblast was made. Make a new playblast of it |
| `FAILED` | the movie could not be replaced, often because another program (Resolve) has it open. It is left as it was; run again later |
