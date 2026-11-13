# The editor

TumblePipe's editor cuts and plays *edits*: the shots, media and slugs of a
film on tracks, saved as versions and published like any other export. It
opens in its own window, outside Houdini, and plays every frame as made: a
playblast as it is, a render through the project's OCIO config.

Right-click a shot card and choose **Play in Cinedex** (the menu's first
catalog entry), or select several shots and choose **Play N Shots in
Cinedex**, to open them back to back in the editor as an
untitled edit. Nothing in the project changes until you save it, and
Resolve is not involved.

The toolbar's **Open Timeline** button plays the whole sequence of the
selected shot (or of the sequence you are viewing); with neither, the
project's master edit, else every shot. See the
[toolbar table](index.md#quick-actions).

To play more than a hand-picked selection: right-click a **sequence** in
the sidebar (or the empty space of a view narrowed to one sequence) and
choose **Play in Cinedex** for all its shots in name order, or choose **Play in Cinedex**
on a **shots Multi** for its members in [timeline order](multis-and-roots.md#play-open-folder-add-entity-and-delete).

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
version; choosing a source or department follows its newest. The **Layer**
submenu picks the render layer (`default`, `chars`, `env`, the `slapcomp`,
a **layers** stack) and changes only that: the clip keeps its source,
department and version rule, and **Best available** (the default) goes back
to the rule above. It lists every layer the shot has of the shown source
(in the chosen department, when one is chosen); the one on screen is
marked. A chosen layer is saved in the edit. While the shot has no
complete render of it yet, the clip plays what best available plays and
the layer is listed as **not ready**; once the render is complete the clip
changes to it by itself. A shot clip's name says which layer plays and how
many others the Layer submenu offers: `SH030 · chars (+2)`, and
`SH030 · chars (+2) · default not ready` while a chosen layer waits. **Reveal
folder** opens the folder of the take on screen in Explorer, Finder or the
desktop's file manager, and **Open in Houdini** opens a department's
workfile of the shot through TumbleTrove Desktop.

A shot with no take its rule picks (a planned shot, or one with nothing
made yet) plays as an empty clip saying so; it still has its length in the
cut. While a take opens, which can take a few seconds on the file server,
its clip says **Opening…**.

### Renders of a frame list

A render of a **Frame List** of more than one range (Farm Submit, for
example `1001-1030 1040-1055`) is never published. Its review movie plays only
the listed frames, back to back, and records which frames they are in a
`frame_ranges` tag. The editor shows it only when you choose it: Best
available, a department's newest version, a shot opening, and View > Show
pass it over, so a clip never switches to it by itself. Its version is
listed in the take menu's **Version** submenu as `v0012 · frame list`;
choosing it pins it. When a clip plays it, the editor puts each picture back
at its own shot frame, so the cut keeps its timing: frame 1040 plays where
1040 falls in the shot, its burn-in says 1040, and the frames between the
ranges (1031-1039) show as nothing (the track below, or black). Over a gap,
a fade still runs as the clip's own fade. **Export movie** places the
frames the same way. A movie whose `frame_ranges` tag does not list exactly
its frames does not open; its clip says why.

### The whole cut as renders or playblasts

**View > Show** plays every shot clip at once as one kind of take, to watch
the cut as renders or as playblasts without changing a clip (#57):

- **Show As Cut** (the default) plays each clip's own rule.
- **Show Renders** plays each shot as a render: the clip's own take when it
  is a render already (a pinned version too), else the newest render of its
  rule's department and layer, else the shot's best available render.
- **Show Playblasts** does the same with playblasts.

**V** steps through the three. A shot with no take of that kind keeps its
cut's take, and its clip says so: `animation v0004  (no render)`. While
renders or playblasts show, a **Showing renders** chip sits by the
playhead's timecode; click it to go back to the cut. Live playblasts play
over playblasts and the cut, never over renders.

It is the view's only: the edit's rules do not change, so saving, publishing
and Render Edit keep the cut's takes, and the next time the edit opens it
shows as cut. The take menu and the take keys still change the cut; they
start from the take on screen.

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
Layer submenu.

### AOVs and layers in the viewer

Under the picture, beside Display, the viewer shows what of the take on
screen plays. For a render, the **AOV** dropdown lists the AOVs of its EXR
frames (`RGBA` is the unnamed one) and picks which one shows, as colour; a
render opens on its beauty. For a layers take, a tick box per **layer**
shows or hides that layer of the stack; a stack of more than four layers
puts its tick boxes in a strip that scrolls sideways, and the label says
how many show while some are hidden (**Layers 3 of 7**). These change only what you look
at: they are not saved in the edit, published or exported, and a take that
updates (a new version) keeps them where it still has that AOV or layer.

### Live playblasts

A shot rendered live from Houdini on this machine (**Live** on
`th::animate` or the SOP `th::playblast`, the Live Playblast shelf tool,
Alt+Shift+L; see [Live Playblast](../nodes/import-and-export.md#live-playblast))
plays its live render in place of the take its rule picks, as the frames
land. Its clip and burn-in say how far the render is
(`animation live, rendering 40/88`), then `animation live` when it is done,
or where it stopped. A frame the render has not reached yet shows the
department's latest playblast at that frame (the take the rule picks when
the department has none and that take is a movie; a render cannot stand in,
so then the frame stays empty), and the clip says so
(`animation live, rendering 40/88 over animation v0012`); the shot plays
that playblast's sound. Press **Live** in Houdini again and the new run
takes over; the frames of the run before stay on screen until each is
rendered anew.

Live renders are local: they are on this machine only (other people see
your published playblasts), and the editor never publishes, saves or
exports one. When a live render is done and looks right, right-click its
clip and pick **Keep as playblast**: its frames become the shot's next
playblast version of that department, and its daily, as if you had pressed
Playblast in Houdini, without rendering again. It is encoded with the
Houdini that rendered it, so a live render made before this version of
TumblePipe has to be rendered again first. The edit keeps the clip's rule; untick **Live** in the
toolbar to see the takes again, which is also how to choose another take
of a shot that is live.

If Houdini was killed while rendering, the render may finish on its own;
if it did not, the clip says it stopped where it got to.

### New takes show by themselves

A playblast or render made on this machine shows in the editor within
about half a second: a playblast from `th::playblast` in Houdini, a farm
job run on **This machine**, render frames written here, **Keep as
playblast**, or a live render starting. The editor reads that shot's takes
again and plays on meanwhile; a clip whose rule picks the new take (Best
available, or a department's latest) shows it, and a clip pinned to a take
keeps it. The editor also follows writes from other sessions: the
**Master edit** box when a master is set or cleared elsewhere (the Asset
Browser, another editor), the bins' shots and frame ranges, and the board's
workfiles.

Playblasts and renders made on other machines (a farm job, another
artist's playblast or **Keep as playblast**) show by themselves too, within a few seconds of being
written: they announce themselves in the project's `_config/journal`
folder on the share, which every editor looks at. If the share is slow or
unreachable the editor plays on and looks again later. Anything else
made elsewhere, and media from a pipeline older than this, still needs
**View > Refresh** (Ctrl+R), which reads every shot's takes and the bins
again.

### Preview frames ready

A render or comp submitted with Preview Frames to **This machine** renders
a few frames first (first, middle and last, or the Preview Frames List).
Once they are on disk, the shot's clips show a **Preview ready** badge at
their top right, within about five seconds. The badge stays until the full
range of that version has rendered: the version is then a take the clip
can play, and the badge goes. The editor reads this from the local farm's
batches on this machine (see
[Running farm jobs on this machine](../deadline.md#running-farm-jobs-on-this-machine)),
so previews from farm (Deadline) jobs give no badge. A batch removed from
the Farm Manager takes its badge with it.

To look at the frames, press **Open in player** on the Farm Manager's
*Preview frames ready* notice. A window of their own opens with the
preview's frames as stills, through the project's OCIO config:
**Left**/**Right** or the frame buttons step between them, and
**Home**/**End** go to the first and the last. From a shell,
`cinedex --frames <folder>` opens the same window (see
[Running it by hand](#running-it-by-hand)).

Any render's frames open the same way, local or from the farm: right-click
a render, denoise, composite or slapcomp job (or a batch row) in the Farm Manager ▸
**Preview in Cinedex** (see [Farm Manager](../deadline.md#the-farm-manager)).

### Live view

Pressing **Live** in Houdini also shows the shot in the editor, on the
project's [master edit](#the-master-edit): an editor open on it comes to the
front without taking the keyboard from Houdini, and when none is open one
starts on it. The view loops from the start of the shot before to the end
of the shot after (the edit's start or end where there is none), plays,
and puts the playhead on the frame Houdini is at; **Live** is turned on if
it was off. A shot in the edit more than once is shown where it first
comes. The toolbar shows the loop's first and last timecode
(`Loop 00:00:05:00–00:00:13:00 ✕`); press it, or move the playhead outside
the loop, to play the whole edit again. The loop keeps to its clips while
the cut is edited: moved, trimmed or split, they are still the ones it
plays; it ends when none of them is left in the edit.

An editor open on another edit is left alone. When the project has no
master edit, or it has no clip of the shot, Houdini's status bar says so.
When an open editor is too busy to answer, no other one is started (it may
be the one on the master edit): the status bar says so, and the next
**Live** tries again.

How the window comes forward depends on the desktop:

- **Windows**: over the other windows, minimised or not, without the
  keyboard.
- **macOS**: in front of the other windows, minimised or not, without
  activating the editor: Houdini stays the active app and keeps the
  keyboard.
- **Linux on X11**: over the other windows without the keyboard. A
  minimised editor is restored and does take the keyboard, as GNOME
  restores a window no other way. The window manager is given a moment
  between its requests (0.4 s in all); the editor goes on playing
  meanwhile.
- **Linux on Wayland**: no program may raise its own window there, so the
  editor asks the desktop for attention instead. GNOME shows a
  "“<edit> - Cinedex” is ready" notification; click it to bring the
  editor forward. The keyboard stays with Houdini. Other desktops show the
  request their own way (sway marks the editor's workspace urgent).

Checked on the build workers (GNOME 46 on X11 and, headless, on Wayland;
macOS) with a test window under another; not yet checked on artists'
desktops: GNOME and KDE as artists run them, Houdini full screen, and a
real **Live** press from Houdini.

### Driving it from Claude

An agent such as Claude can read and change the edit open in a running
editor. `cinedex --mcp` is an MCP server (no window) that finds the
editor and passes the agent's calls on to it. Add it to Claude Code once:

```
claude mcp add cinedex -- <TumblePipe>/bin/<platform>/cinedex --mcp --project <project root>
```

Without `--project` it uses `$TH_PROJECT_PATH`, or when that is not set,
talks to any of your editors. Its tools:

- **get_state**, **get_edit**, **list_shots**, **list_takes**: what is
  open (name, version, unsaved changes, whether it is the master edit), the
  tracks and clips with what each shows now, the project's shots and media
  library items, and a shot's takes (`frame_list` marks a
  [Frame List render's](#renders-of-a-frame-list) movie, which only a rule
  pinning its version shows).
- **view_shot**, **seek**, **play**: show a shot as [Live view](#live-view)
  does (on any edit), move the playhead, start or stop.
- **edit**: one or more of the editor's operations (place, insert, lift,
  ripple delete, move, trim, roll, slip, split, join, fades, another take,
  gain, markers, tracks) as **one undo step**, all of them or none. With
  `dry_run` nothing changes, and the answer says what would: the clips
  taken out and put in on each track, the markers and the length.
- **undo**, **redo**: the same history as yours. Ctrl+Z undoes an agent's
  step too.
- **save**: the next working version (a name for an untitled edit).
- **publish**: a preview only (`dry_run`). For the master edit it lists
  the shot changes the publish would write (shots made, ranges set, shots
  retired). Publishing itself is done in the window.
- **events**: what happened since the agent last asked (edits, saves,
  publishes, takes changing, Live view), waiting up to 25 seconds for
  something new.
- **list_players**: the editors running, for when there is more than one
  (every tool takes `player`, a pid).

Every change shows in the window as it lands. A published version stays
read-only for agents too.

## The window

Over the picture is the toolbar: the edit's name and version (with `*`
while it has unsaved changes), **Master edit** (see below), **Live** (play
this machine's live playblasts over the shots' takes, on by default; see
[Live playblasts](#live-playblasts)), **Burn-ins**, the loop of a
[Live view](#live-view) while there is one, and the timecode. A ticked
box is filled light, an unticked one an empty frame. Under
the picture are the display, view and exposure.

The actions are in the menu bar: on macOS the one at the top of the screen,
and on Windows and Linux a bar drawn at the top of the window (press a
title, or Alt with its underlined letter, or F10). **File** (open,
save, publish, the master edit, new shot, import media), **Edit** (undo,
redo, split, lift, ripple delete, marker, selection, and putting the bin's
pick in the cut), **Playback** (play, step, jump by a second or to an edit
point, the start and end) and **View** (burn-ins, and **Refresh**, which
reads every shot's takes and the bins again). Each item shows its
shortcut, and is greyed while it does not apply: while a dialog is open, or
the cut's actions on a published edit. With a menu open, an item's
underlined letter chooses it. The single-letter keys (**S** split, **M**
marker, **B** burn-ins) act only with neither Ctrl (Cmd on macOS) nor Alt
held.

Beside the picture are the bins. **Shots** lists the project's shots,
each sequence's under its name;
**Media** its media library (movies, stills and sound copied into the
project as versioned items: **Import…** copies a file in, naming the item
after the file until you type a name). Select an item
and press **Insert** (the cut opens at the playhead and everything after it
moves on, on every track) or **Overlay** (it goes over what is there).
Either moves the playhead to the end of what it put, so picking SH010,
SH020 and SH030 and pressing Insert after each builds the cut in that
order (up to 1.101.0 the playhead stayed put and the cut came out
backwards). Double-clicking an item inserts it. A movie brings its sound onto an audio
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

- Click or drag on the ruler, or on empty track, to seek. A click past the
  last clip parks on the cut's end (one past its last frame), where an
  Insert appends.
- Click a clip to select it; Shift or Ctrl-click adds it or takes it out.
- Drag a clip to move it, onto another track of its kind too. With Alt held
  it slips: the clip stays and the frames in it move.
- Drag a clip's edge to trim it; what follows moves with it. With Alt held
  the cut rolls: the clip and its neighbour change together and the length
  holds.
- Drag along a clip's top band to fade it in (left half) or out (right
  half). Where a clip on a higher track fades over one below, the two
  dissolve; over nothing, it fades from or to black.
- With one clip selected, the strip above the tracks shows its **Fade in**
  and **Fade out** in frames and, for a video clip, its **Opacity** in
  percent: type a number and press Enter to set it (a fade that does not fit
  the clip is refused, and the status line says why). A clip at less than
  100% shows the tracks below through it, as inside a fade; at 0% it is not
  seen at all. The edit file writes `opacity` only for a clip under 100%,
  so an edit at full opacity reads in an editor from before it.

Every change is one step of undo, however many clips it moves.

| Key | Does |
| --- | --- |
| Space | play / pause |
| Left / Right | step one frame back / forward |
| Alt+Left / Alt+Right | jump one second back / forward |
| Ctrl+Left / Ctrl+Right | jump to the previous / next cut |
| Home / End | the first frame / the cut's end, one past its last frame (up to 1.101.0 End stopped on the last frame, so an Insert there split the last clip) |
| Up / Down | the selected clips (none: the clip under the playhead) show the next newer / older version of the take they show, pinned, as the take menu's Version rows do |
| Ctrl+Up / Ctrl+Down | the previous / next department of that kind (its newest version), as the Department rows |
| Shift+Up / Shift+Down | playblast / render, as the Source rows |
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
| V | View > Show: the cut, renders, playblasts, round (view only, never saved) |

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
the project's shots unchanged since it opened). **Open Cinedex** in
TumbleTrove Desktop's Scripts panel (the project card's right-click menu,
under tumblepipe; called **Edits** up to 1.104) starts the editor on the whole film: the project's
[master edit](#the-master-edit), or when the project has none, an untitled
edit of every shot.
**Save** writes
its next working version (`v0001`, `v0002`, ...) with a note; **Save As**
names a new edit (a name it refuses says why under the Name field, as you
type). While an edit is open it is autosaved every minute;
when the editor finds an autosave newer than the working version it opens,
it offers it back. Undo your changes back to the saved version and the
autosave goes (up to 1.103 it stayed, and the next open offered the undone
changes back). Closing with unsaved changes asks first.

**Publish** freezes the edit: every shot clip's rule is written down as the
exact take it plays, so a published version always plays the same frames.
A publish also writes the edit as OpenTimelineIO (`.otio`, which Resolve
imports). Dissolves become transitions, and so do fades from and to black
(a fade with nothing on the tracks below it): a cross dissolve between the
clip and a gap; how Resolve imports those fades has not been checked
yet. A fade over clips below, and an opacity under 100%, are
not in it: the clips stay as they are, and a yellow marker says what was
left out. Its movies
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
one), **Render Edit…** (also only once published) opens Farm Submit on its
Edits tab with this edit ticked, **Open Folder** its working folder, and **Set as Master Edit** makes
it the master edit (the master edit's card is ticked **Master Edit** and
offers **Clear Master Edit** instead; in the grid its card carries a
**MASTER** chip, on a TumbleTrove newer than 0.59.0). Its menu ends with
**Rename Edit…** and **Delete Edit…**, and **New Edit…** (on the Edits
section, or anywhere unscoped) makes one with nothing saved yet, which
**Open in Editor** opens empty for its first **Save**; see
[Creating entities](index.md#creating-entities) and
[Editing and deleting entities](index.md#editing-and-deleting-entities).
An edit card's thumbnail is the shot thumbnail of its first shot clip, and
**Refresh Thumbnail** picks up a changed cut. An edit card is not offered
**Add to Multi**.

A published version can be rendered into a movie on the farm: the **Edits**
tab of [Farm Submit](submit-jobs.md#the-edits-tab) offers each edit's
published versions, the latest first, with **Render Edit**. A worker plays
the version through the editor without a window, as you see it (fades,
dissolves and the project's OCIO view included) and with its sound mixed,
and encodes `render:/edits/<name>/<name>_edit_vNNNN.mp4`: H.264 with AAC
sound and a timecode track from the edit's start timecode, ready for
Resolve or for sending out. The same render runs by hand with
`cinedex --project <root> --ocio <config> --render entity:/edits/<name>@vNNNN --out <dir>`,
which writes the frames as PNGs, the sound as a WAV and `render.json`.

### Export movie

**File > Export Movie…** makes the same movie on your machine, from the edit
as it is in the window: saved or not, published or working, with the cut's
takes, as Render Edit uses them. What View > Show shows is only for viewing
and is not exported. The dialog offers a file (the edit's name and version,
`Main_v0003.mp4`, in your Videos folder, Movies on macOS, or the folder you
last exported to); type another or pick one with **Browse…**, which opens
the system's Save dialog (zenity or kdialog on Linux). It says what the movie
will be: the edit's resolution and frame rate (the project's, unless the
edit was set otherwise), its length, and the OCIO display and view it is
shown through, the one the viewer shows (the project's default until you
pick another). **Export** renders every frame as Render Edit does, fades,
dissolves and sound included, and encodes it as an H.264 `.mp4` or `.mov`
with AAC sound and a timecode track from the edit's start timecode. A bar
shows the frames done; **Cancel** stops it, and nothing is written: the
movie is made beside the file under a temporary name
(`.Main_v0003.exporting.mp4`) and only replaces the file once it is
complete. When it is done, **Play** opens the movie in your
player and **Show in Folder** its folder.

The editor decodes with its own FFmpeg but encodes with Houdini's
`hffmpeg`: the one of the Houdini it was started from (`$HFS`), else the
newest Houdini installed in SideFX's default place. Set `TH_HFFMPEG` to use
another FFmpeg (it needs openh264 and AAC). With none, the dialog says so and
does not export. A live playblast never goes into a movie: a shot shows its
take, as in Render Edit. The viewer's exposure and burn-ins are not part of
the movie.

### The master edit

One edit can be the project's **master edit** (the toolbar's checkbox): the
cut the shots' frame ranges follow, and the one **Open Cinedex** in TumbleTrove
Desktop opens. Publishing it lists the shot changes its
cut makes, each with a tick box:

- a planned shot (**New shot…** in the Shots bin puts one in the cut, with
  its sequence, name and length; the name offered is the sequence's next
  free one in its style, ten above the highest, so SH050 after SH040, or
  SH010 in a new sequence, and follows the sequence field until you type a
  name of your own) is created with the range it has;
- a shot whose clip is longer or shorter than its range gets the new range;
- a shot (or planned shot) that plays at another fps than the master edit
  gets the master's fps, pinned on the shot (its own `fps` key; up to
  1.101.0 the master never wrote fps). Roll is not written: the cut has no
  handles to say otherwise, and a created shot takes the project's;
- a shot the master edit used to have and no longer does is retired. These
  are not ticked: tick them to retire the shots.

A change is flagged when the shot's take does not cover the new range (a
playblast made before the range grew). The ticked changes are written after
the publish, and the shots bin follows them.

## Running it by hand

The editor ships inside the TumblePipe package, as
`bin/<platform>/cinedex`:

```
cinedex [--project <root>] [--ocio <config>] [--edit entity:/edits/<name>[@vNNNN] [--view <shot uri> [--at <frame>]] | --shots <shot uri>...]
cinedex [--project <root>] --mcp
cinedex [--ocio <config>] --frames <batch folder | frames folder | frame>
```

`--edit` opens the edit's newest working version, or with `@vNNNN` that
published version (an edit never saved, every shot of the project);
`--shots` the shots end to end as an untitled edit; neither, every shot of
the project as an untitled edit. The project is `--project`, else
`$TH_PROJECT_PATH`; the OCIO config `--ocio`, else `$OCIO`. When either is
missing, or the edit does not open, the window says why. `--view` opens the
edit as [Live view](#live-view) shows a shot, `--at` at that shot frame.
`--mcp` opens no window: see [Driving it from Claude](#driving-it-from-claude).
`--frames` opens [preview frames](#preview-frames-ready) as stills, and no
edit: given a local farm batch folder (one holding `batch.json`, under
`batches/` in the local farm's folder), the preview frames its runner
recorded as ready, every preview of the batch in job order; given any
other folder, the EXR and JPG frames in it, in frame order; given a frame
file, that frame. It needs only the OCIO config.

While it runs, it listens on a port of 127.0.0.1 for Houdini's Live view
calls and agents' commands; the port and a token it checks are in
`players/<project key>/<pid>.json` under the live folder, and the file is
removed when it quits.

It is built for Windows, Linux and macOS. See
[Contributing and development](../development.md#cinedex-the-editor) for how it
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
