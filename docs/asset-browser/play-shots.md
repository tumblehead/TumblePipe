# The editor

TumblePipe's editor cuts and plays *edits*: the shots, media and slugs of a
film on tracks, saved as versions and published like any other export. It
opens in its own window, outside Houdini, and plays every frame as made: a
playblast as it is, a render through the project's OCIO config.

Right-click a shot card and choose **Play in Cinedex** (the menu's first
catalog entry), or select several shots and choose **Play N Shots in
Cinedex**, to open them in the editor. When an edit of the project holds
every one of them, it opens on that edit, playing the first of them in
place (as [Live view](#live-view) does): the master edit when it holds
them, else the edit holding them that you opened or saved last. Otherwise
they open back to back as an untitled edit. Either way **File > Open…**
lists every edit. Nothing in the project changes until you save, and
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
  short, is not a take yet; the shot's clips say how far it is (see
  [Renders on their way](#renders-on-their-way)).

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
shown has a tick (✓) before it, as the current row of **View > Show** has
(up to 1.112.0 it had `(shown)` after it). Choosing a version of the shown department pins that
version; choosing a source or department follows its newest. The **Layer**
submenu picks the render layer (`default`, `chars`, `env`, the `slapcomp`,
a **layers** stack) and changes only that: the clip keeps its source,
department and version rule, and **Best available** (the default) goes back
to the rule above. It lists every layer the shot has of the shown source
(in the chosen department, when one is chosen); the one on screen is
ticked. A chosen layer is saved in the edit. While the shot has no
complete render of it yet, the clip plays what best available plays and
the layer is listed as **not ready**; once the render is complete the clip
changes to it by itself. A shot clip's name says which layer plays and how
many others the Layer submenu offers: `SH030 · chars (+2)`, and
`SH030 · chars (+2) · default not ready` while a chosen layer waits. **Reveal
folder** opens the folder of the take on screen in Explorer, Finder or the
desktop's file manager, and **Open in Houdini** opens a department's
workfile of the shot:

- If a Houdini of the project already has that workfile open, that Houdini
  comes to the front.
- Otherwise, if you opened the editor from Houdini (**Open Timeline**,
  **Play in Cinedex** or **Live**) and that Houdini is still open, the
  workfile opens there, just as the Asset Browser's Open does: Houdini comes
  to the front, asks what to do with unsaved changes, and sets the shot's
  frame range. If you cancel, nothing opens, and the editor's status line
  says so.
- Otherwise (you opened the editor from TumbleTrove Desktop, or that Houdini
  has closed), TumbleTrove Desktop starts a new Houdini on the workfile.

The editor's status line says which happened.

A shot with no take its rule picks plays as an empty, hatched clip; it
still has its length in the cut. The clip says why in a few words, and the
viewer says it in a sentence, with what to do about it, when the playhead
is on the clip (up to 1.112.0 the clip said only "No take fits its rule"):

| The clip says | It means |
| --- | --- |
| **No playblast or render yet** | Nothing of the shot has been published: no playblast, and no render with every frame. |
| **Render not finished, no playblast** | As above, but a render is on disk with frames missing: it is still going, or stopped short. It shows by itself once every frame is there. |
| **Only check renders so far** | The shot has only check renders (Farm Submit's Step) or Frame List renders. Those are never shown by themselves: right-click the clip and choose a version. |
| **Nothing from lighting yet** | The clip is set to a department that has no playblast or finished render of this shot. Right-click > **Best available** shows what the shot has. |
| **No render yet**, **No playblast yet**, **Render not finished** | The clip is set to a source (render or playblast) the shot has none of yet. |
| **v0012 is not there** | The clip is pinned to a version that was removed, or to a render that no longer has every frame. Choose another version. |
| **Planned: made when the edit is published** | A planned shot (**New shot…**): the project gets it when the master edit is published. |
| **Cannot play: …** | The take was found but would not open; the words after say why. When its file is simply gone it says **its file is not on disk** and the path (moved or deleted, or the share is offline). |

While a take opens, which can take a few seconds on the file server, its
clip says **Opening…**.

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

### Check renders

A render with a **Step** above 1 (Farm Submit, every Nth frame) is a check
render, never published ([Stepped renders](submit-jobs.md#stepped-renders)):
its version's `context.json` says `"published": false`. Its frames are all
there on its step, so it plays (each frame held until the next), but it is
never the shot's render. Like a frame list's, the editor shows it only when
you choose it: Best available, a department's newest version, a shot
opening and View > Show pass over its EXRs, its stacks of layers and the
render department's review movie of it, so a clip never switches to it by
itself. Each of its takes is still listed, labelled `render v0014 default
beauty (check, every 2nd)`, and the **Version** submenu shows it as
`v0014 · check`; choosing it pins it, and a clip pinned to it keeps playing
it. A shot whose only takes are check renders opens on nothing (its cut
says so) until a published render or a playblast is made.

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
renders or playblasts show, a **Showing renders** chip sits in the
toolbar; click it to go back to the cut. Live playblasts play
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

### Renders on their way

While a shot's newest render is still missing frames, its clips say so:
their second line ends with the render and its count,
`animation v0004  ·  lighting v0007: 34/120 rendered`, and a thin strip
runs along the clip's bottom edge, dark where the render is to have a
frame and lit where the frame is on disk. The strip follows the clip: it
shows the frames that clip shows, so a trimmed clip shows its own part of
the render. It is not the frame cache, which is the strip under the ruler
(the frames of what plays that are decoded in memory).

The clip goes on playing what its rule picks of the finished takes (the
playblast, or the render before), or stays hatched when the shot has
nothing else: an unfinished render is never played in the cut, so no frame
is ever missing from playback or from **Export Movie**. When its last frame
lands the count and the strip go and the clip changes to the render by
itself, as with any new take. To look at the frames that are there
meanwhile, use **Preview in Cinedex** on its job in the Farm Manager, or
`cinedex --frames <folder>`: [the preview window](#the-preview-window)
shows the frames not rendered yet as gaps.

Which render is counted: the newest version of a render layer, when it
records its frame range, is not a check render, and lacks frames. Of
several layers still rendering it is the one the clip would pick once
done (the most downstream department, its slapcomp, else `default`). An
older version that stopped short under a newer finished one is an
abandoned render and is not shown.

A playblast the farm (or this machine's queue) is rendering is said the
same way, `animation v0005 playblast: 34/120 rendered`, with the same
strip. A farm playblast renders on its worker's own disk and writes only
the finished movie to the project, so until now nothing of it could be
seen before it was done. Its worker now copies each frame it finishes into
a folder beside the movie to come (`<shot>_<dept>_v0005.mp4.frames`, a few
seconds behind the render and always one frame short of it: the newest
frame is copied when the next one starts), and removes the folder when the
playblast ends. The clip plays the movie when it arrives; to watch the
frames meanwhile, use **Preview in Cinedex** on the playblast's job in the
Farm Manager. A shot with a render on its way too shows the render. This
needs the worker to run this version of TumblePipe: a playblast rendered
by an older one shows nothing until its movie is written. A playblast made
in Houdini with **Renderer: Houdini GL** writes its movie directly and has
no frames to show; a [live playblast](#live-playblasts) is played as it
renders, as before.

The editor lists the frames folder of each render it is following every
five seconds, on a thread of its own, and stops when the render is
complete. It looks for renders that have started two shots at a time and
at each shot again after five minutes, so that a long cut on a file server
costs a listing or two per poll: a render started after the editor opened
can take a few minutes to show its strip.

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
preview's frames, through the project's OCIO config. From a shell,
`cinedex --frames <folder>` opens the same window (see
[Running it by hand](#running-it-by-hand)).

Any render's frames open the same way, local or from the farm: right-click
a render, denoise, composite or slapcomp job (or a batch row) in the Farm Manager ▸
**Preview in Cinedex** (see [Farm Manager](../deadline.md#the-farm-manager)).

#### The preview window

The preview window plays the frames as the editor plays a cut, with the
same transport and a timeline under the picture:

- **Play** (Space, or the Up arrow) plays from the frame shown at the shot's
  fps (24 when the job names no shot); the button's tooltip names both
  keys. Up plays in the editor too, as it does in Houdini. It loops back to the start while **Loop** (L) is
  ticked, as it is when the window opens. The rate beside it says when the
  frames cannot be read that fast ("playing 9 of 24 fps", or "waiting for
  frames"): a network share or very large EXRs. Playback then waits for
  each frame instead of skipping it, and a frame read once plays at full
  speed after that.
- **Left**/**Right** step one frame, **Alt+Left**/**Alt+Right** one second,
  **Home**/**End** go to the first and the last frame, and a click or drag
  on the timeline goes to a frame. Each of these is also in the
  **Playback** menu and on the transport buttons.
- **Display**, **View** and **Exposure** under the picture are the editor's.
- **AOV**, before them, picks what of the render is shown. It lists the
  other AOVs of the version the frames are in (its AOV folders, the beauty
  first) and, for an EXR that holds several layers, the layers its header
  lists. Picking one swaps the picture in place: the playhead stays on its
  frame number and playback goes on, also while frames are still landing
  (an AOV's frames not written yet are gaps, as the beauty's are). An AOV
  that is not colour says so in the list, *depth (data, shown raw)* or
  *CryptoObject00 (cryptomatte ids, shown raw)*: it is shown as written,
  through no display transform, and **Exposure** scales it (a depth of
  hundreds of units reads once it is brought down a few stops). Which AOVs
  are data is told from their names and channels (one channel, X/Y/Z, or a
  name such as `depth`, `N`, `P`, `*_id`, `crypto*`). The chooser is not
  shown when there is one layer and no other AOV: a playblast, a slapcomp,
  a folder of JPGs.
- **Version**, beside it, lists the other versions of what is shown, newest
  first: a render's are the version folders of the same department and
  layer (shown as the same AOV), a playblast's the other playblasts of its
  department. A version that is not whole says so: *v0007 · 12 of 100
  frames*, *v0008 · 3 frames, not finished*, *v0005 · no frames* (which
  cannot be picked), and a check render ends in *check*. Picking one swaps
  the frames in place at the same frame number (the nearest, when that
  version has no such frame), playing on if it was playing. **Page Up** and
  **Page Down** step to the newer and the older version, as in the editor,
  passing over versions with no frames; the **Version** menu has both and
  the list, the version shown ticked. An earlier playblast is its movie; a
  playblast still rendering, its frames so far. The chooser shows only for
  frames in the project's render or playblast folders (what *Preview in
  Cinedex* opens), and only when there is more than one version; the
  department and source keys of the editor (Ctrl and Shift with Page
  Up/Down) do nothing here. **View > Refresh** reads the lists again.
- **Frames not rendered yet** are gaps on the timeline: a hatched span
  labelled *not rendered yet*, and the picture says "Frame 1015 is not
  rendered yet" when the playhead is on one. **Ctrl+Left**/**Ctrl+Right**
  jump to the edges of the gaps. *Preview in Cinedex* tells Cinedex which
  frames the job renders, so frames not started yet at the end of the range
  show as a gap too; the title bar counts them ("40 of 60 frames
  rendered").
- **Frames that land while it is open** fill their gap within about two
  seconds, and a frame written again is read again, so you can leave the
  window open on a render that is still going. **View > Refresh** (Ctrl+R)
  looks at once. A frame that cannot be read (half written, damaged) says
  so on the picture and is read again when the file changes.

Cinedex has an icon of its own (a film strip with an amber playhead) in the
taskbar, the title bar and Alt+Tab, on Windows, macOS and Linux (X11).

### Shots on the farm

A shot with a job on the farm (Deadline, or this machine's queue) shows it
on every one of its clips, whatever department or layer the clip plays: a
thin coloured line along the top of the clip and, where there is room, a
badge at its top right saying **Rendering**, **Queued**, **Waiting** (on
another job), **Suspended** or **Failed** (a dot of that colour when the
clip is too narrow for the word). The editor asks the farm every 15
seconds, so a job submitted or finished shows within about that.

Only the shot's newest submission counts. When it has finished the shot
shows nothing, even if an older one failed; of the jobs in that submission
the most urgent speaks for it (failed, then rendering, suspended, queued,
waiting).

Right-click a clip with a job:

- **Show Farm Job in Farm Manager** opens the Farm Manager as its own
  window, with that job selected (a failed job at its error). It needs
  Houdini to be installed, and does not reuse a Farm Manager already open
  in Houdini.
- **Copy Farm Job ID** puts the job's id on the clipboard.

No farm reachable shows no badges, and no error.

### Live view

Pressing **Live** in Houdini also shows the shot in the editor, on the
project's [master edit](#the-master-edit): an editor open on it comes to the
front without taking the keyboard from Houdini, and when none is open one
starts on it. The view is the shot's clip: the earliest one on any video
track when the shot is in the edit more than once. It loops from the start
of the clip just before that clip on its track to the end of the clip just
after it, whatever those clips show (another shot, media or a slug), with
any gap between them; where the shot's clip has no clip before or after it
on its track, the loop starts or ends with the shot's clip itself. It plays
and puts the playhead on the frame Houdini is at (the clip's nearest frame
when Houdini is outside the shot); **Live** is turned on if it was off.
The toolbar shows the loop's first and last timecode
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
  ripple delete, move, shift, trim, roll, slip, split, join, fades, another
  take, gain, markers, and tracks: add, remove with its clips, move,
  rename, colour) as **one undo step**, all of them or none. With
  `dry_run` nothing changes, and the answer says what would: the clips
  taken out and put in on each track, the markers and the length.
- **undo**, **redo**: the same history as yours. Ctrl+Z undoes an agent's
  step too.
- **save**: the next working version (a name for an untitled edit).
- **publish**: a preview only (`dry_run`). For the master edit it lists
  the shot changes the publish would write (shots made, ranges set, shots
  retired). Publishing itself is done in the window.
- **export_timeline**: the cut as a timeline file for Resolve or Premiere
  Pro, as [File > Export Timeline…](#export-to-resolve-premiere) writes
  it: `path`, `format` (`fcp7xml` or `otio`), `collect_media`, `sequences`.
  It answers what was carried and what was dropped or approximated.
- **export_movie**: the cut as one movie on this machine, as
  [File > Export Movie…](#export-movie) makes it, answered when the movie
  is written: `path`, and the dialog's settings, each optional:
  `resolution` (`"edit"`, `"half"`, or `{"width": n}` / `{"height": n}`),
  `range` (`"whole"`, `"selection"`, `"loop"`, or
  `{"first": a, "last": b}`), `codec` (`h264`, `mjpeg`), `quality` (`best`,
  `high`, `standard`, `small`), `audio`, `burnins`. A setting left out is
  the dialog's first offer, not what the user last chose.
- **events**: what happened since the agent last asked (edits, saves,
  publishes, takes changing, Live view), waiting up to 25 seconds for
  something new.
- **list_players**: the editors running, for when there is more than one
  (every tool takes `player`, a pid).

Every change shows in the window as it lands. A published version stays
read-only for agents too.

## The window

At the top of the window is the toolbar: the menus (on Windows and Linux),
**Cut** | **Board**, **Master edit** (see below), the loop of a
[Live view](#live-view) while there is one, and the chips of what the view
is doing (**Showing renders**, **Flipped**). The edit's name and version
(with `*` while it has unsaved changes) are in the window's title.

The switches of what the picture shows sit on the player, in the strip
right over the picture, at its right: **Live** (play this machine's live
playblasts over the shots' takes, on by default; see
[Live playblasts](#live-playblasts)) and **Burn-ins** (also **B** and
**View > Show Burn-ins**). On the board, where no picture is shown, the
strip is not there either. A ticked box is filled light, an unticked one
an empty frame. Under the picture are the transport bar and, below it, the
display, view and exposure.

The playhead's timecode, large and in amber, heads the timeline, at its
left over the tracks' names and beside the clip fields, so a timing is
read where the playhead is. With the timeline folded away (see
[Making room](#making-room)) it moves to the right end of the toolbar.

The transport bar plays the cut without the keyboard. Its buttons, left to
right: go to start, previous edit point, one frame back, play (pause while
playing), one frame forward, next edit point, go to end. Each does exactly
what its key and its **Playback** menu item do, and its tooltip names the
key. Beside them is a scrub bar over the whole cut (drag it to move the
playhead, as on the ruler), then the playhead's timecode and its edit frame
of the cut's length (`45 / 200`; frames count from 0). With the timeline
folded away (see [Making room](#making-room)) the bar is enough to play and
scrub. The buttons do not keep the keyboard: after a click, Space still
plays. The editor has no loop of its own (only a [Live view](#live-view)
loops), so the bar has no loop button; the [preview
window](#the-preview-window) has one.

The actions are in the menu bar: on macOS the one at the top of the screen,
and on Windows and Linux a bar drawn at the top of the window (press a
title, or Alt with its underlined letter, or F10). **File** (open,
save, publish, the master edit, new shot, import media), **Edit** (undo,
redo, split, lift, ripple delete, marker, selection, and putting the bin's
pick in the cut), **Playback** (play, step, jump by a second or to an edit
point, the start and end) and **View** (burn-ins, fitting the timeline,
which tracks have a row, and **Refresh**, which
reads every shot's takes and the bins again). Each item shows its
shortcut, and is greyed while it does not apply: while a dialog is open, or
the cut's actions on a published edit. With a menu open, an item's
underlined letter chooses it. The single-letter keys (**S** split, **M**
marker, **B** burn-ins) act only with neither Ctrl (Cmd on macOS) nor Alt
held.

Beside the picture are the bins. **Shots** lists the project's shots,
each sequence's under its name;
**Media** its media library (movies, stills and sound copied into the
project as versioned items).

**Import…** (the Media bin's button, or **File > Import Media…**) opens
your system's file browser: pick one file or several. Each movie, sound or
single picture is copied in as an item named after the file (its letters and digits:
`cut 3.mov` becomes `cut3`); a name the library already has gets a new
version, and two files of one batch that would get the same name get `2`,
`3`… after it. When it is done the status line names every item made and
every file that did not go in, with why:

- a file that is no movie, picture or sound, or a folder, is named as such;
- a frame of a numbered image sequence (`plate.1001.exr`) stands for the
  whole sequence, which is named once with its frame count and range. The
  library holds one file per item, so a sequence is **not imported yet**:
  make a movie of it and import that;
- a file the player cannot read is named with the reader's error. The
  other files still go in.

To rename an item or change its note, right-click it in the Media bin:
**Rename or Edit Note…**. An item that clips of the open edit use is not
renamed (take it out of the cut first); other saved edits that use the old
name will show it as missing, so rename before you cut with it. On a Linux
without `zenity` or `kdialog` there is no file browser to open: the old
dialog with a typed path opens instead and says so. (Up to 1.112.0 Import
was always that typed path.)

Dropping files from Explorer or Finder onto the window is **not there
yet**: the UI toolkit does not hand dropped files to the app. Everything
behind it is built (the same import, then the clips put on the track and
frame under the pointer), so it arrives with the toolkit's next step.

Right-click a row of either bin for its menu. On a shot, several picked
rows, or a sequence's name: **Insert at Playhead** and **Overlay at
Playhead** (not on a read-only edit), **Open in Houdini** with the shot's
workfiles (one shot), **Farm Submit…**, **Reveal in File Browser** (the
folder of what the shot plays; one shot), **Copy Name**, **Copy URI** and
**Refresh Thumbnail**. On a media item: Insert, Overlay, Reveal, Copy Name
and Rename or Edit Note. **Farm Submit…** opens the pipeline's Farm Submit
window with those shots ticked, as the Render quick action does in
Houdini: you read and change the settings there and press Submit. Cinedex
itself never sends anything to the farm. The row is there only where
Houdini is found (`HFS` is set, which it is when Cinedex is opened from
TumbleTrove Desktop or from Houdini). A shot clip's menu in the timeline
has **Copy URI** and **Farm Submit…** too.

Select an item
and press **Insert** (the cut opens at the playhead and everything after it
moves on, on every track) or **Overlay** (it goes over what is there).
Either moves the playhead to the end of what it put, so picking SH010,
SH020 and SH030 and pressing Insert after each builds the cut in that
order (up to 1.101.0 the playhead stayed put and the cut came out
backwards). Double-clicking an item inserts it. Double-clicking a
sequence's name inserts every shot of the sequence, one after the other
from the playhead, in name order with numbers read as numbers (`sh9`
before `sh10`, `061` between `060` and `070`); the whole run is one step of
undo (up to 1.112.0 it inserted the sequence's first shot only). A shot
with nothing to play yet goes in like any other, as an empty clip of its
length. The bins list shots in that same order. With **Shift** held, a
double-click overlays instead of inserting: Shift+double-click a shot or
media item lays it over what is at the playhead, and Shift+double-click a
sequence's name lays all its shots over the cut, back to back on one track
(the lowest with room for the whole run, a new one on top when none has).
Enter and Shift+Enter in a bin list you clicked in do the same. The Insert
and Overlay buttons say so when you hover them. A movie brings its sound
onto an audio track.

A row is one line: the thumbnail and the name. Rest the pointer on a row
and a tip says the rest: a shot's length and frame range, a media item's
version and what it holds (up to 1.112.0 that was a second line under every
name, and half as many shots fitted in the list).

The chevron before a sequence's name folds that sequence's shots away, and
brings them back; Cinedex remembers which sequences you folded, per
project, for next time. A folded sequence's name still works for all its
shots: double-click it to insert them, Shift+double-click to overlay them.
On a project that lists its shots flat (Settings > **Flat shot list (no
sequences)**, see [Settings](settings.md#flat-asset-and-shot-lists)) the
Shots bin has no sequence names to head or fold the shots: it lists every
shot by its name alone, in name order (numbers read as numbers), in the
list and in the grid.

**Ctrl**-click (**Cmd** on macOS) adds a row to the picked ones, or takes
it out. Insert and Overlay then put all of them, in the bin's order, as
one step of undo.

Each shot and media item has a thumbnail: a shot's is the
first frame of its best available take (what a double-click puts in the
cut), a media item's the first frame of its newest version. A shot with
nothing to play has no picture, and its row says why in the same words its
clip would (see the table above; in the grid the words are inside the empty
picture). Thumbnails are read as their rows come on
screen, a few at a time and never on the window's own thread, so a project
of hundreds of shots on a slow file server opens and scrolls as before and
the pictures fill in; one that was read is kept until Cinedex closes. A
shot's is read again when it gets a new playblast or render, and
**View > Refresh** reads all of them again.

Tick **Grid** above the bin to see it as a grid of thumbnails, each
sequence's name over its shots, as many tiles a line as the bins' width
holds (drag the line beside the bins to make them larger); untick it for
the list. Cinedex remembers the choice. The grid works as the list does:
click a tile to pick it for **Insert** and **Overlay**, double-click to
insert it, Shift+double-click to overlay it, and the same on a sequence's
name for all its shots.

You can also drag out of a bin onto the timeline, from the list or the
grid: a shot, a media item, several rows picked together (drag any one of
them), or a sequence's name for all its shots in order, folded or not.
While you carry it, words beside the pointer say what it is and what
letting go will do, and the timeline shows a box for each clip where it
would land, on the track under the pointer:

- Let go, and it is **laid over** the cut from the frame under the
  pointer: on the track under the pointer when that has room, else the
  next one up with room, else a new track on top. Nothing that is there
  moves.
- Hold **Ctrl** (**Cmd** on macOS) as you let go, and it is **inserted**
  on the track under the pointer: what follows moves on, on every track.
- Over the empty layer above the top video track it makes that track.
- Where it cannot land the box is red and says why, before you let go: a
  picture over an audio track, sound over a video track, a read-only edit.
  Letting go there changes nothing.
- **Escape**, or letting go anywhere off the tracks' rows, drops nothing.

A drop is one step of undo, and the playhead goes to the end of what was
put, as after Insert. Sound alone goes where Overlay puts it: the first
audio track with room. (Up to 1.112.0 nothing could be dragged out of a
bin.)

Under the picture are the tracks: a ruler, then the video tracks (the top
track on top), then the audio tracks. Each clip shows its first frame, its
name and its take, its fades as ramps, and an outline when selected. An
audio clip shows its waveform, through its in point, gain and fades (a
flat line while its sound is still being read). A
shot's clip is coloured by the department its take comes from (every
animation take the same muted blue, say), so the cut shows at a glance how
far each shot has got; a clip with nothing to play is hatched, and says
why. Every clip has a thin light edge, so two touching clips of the same
colour still show where one ends and the next starts; a clip with nothing
to play yet (not rendered, say) has a brighter grey edge, which reads
against the hatching of a neighbour. The ruler marks the markers, the
playhead, and in a lighter strip the frames already decoded. Amber means *now*: the playhead, the timecode, a
clip being dragged.

- Click or drag on the ruler to seek; a click on empty track seeks too. A
  click past the last clip parks on the cut's end (one past its last
  frame), where an Insert appends.
- Click a clip to select it. **Ctrl+click** adds it or takes it out.
  **Shift+click** selects the range from the clip you clicked last (plainly
  or with Ctrl) to this one, in place of what was selected: on one track
  the clips between the two; across tracks, on every row from the one to
  the other as shown, the clips with a frame between the start of the
  earlier clip and the end of the later one. Shift+click again and the
  range is taken from the same first clip. With nothing clicked before, it
  selects the clip alone.
- Drag from empty track to **box select**: every clip the box touches, on
  the rows it crosses, is selected (after 4 pixels, so a click stays a
  click). Dragging on the ruler still scrubs. With **Alt** held, a drag
  along an empty stretch of a video track makes a new shot of that length
  there instead ([a new shot from the timeline](#the-master-edit)).
- The selection stays on its clips: move them, trim them, undo, redo, and
  the same clips are selected where they are now. A clip that is gone
  (deleted, joined away, undone out of the cut) is deselected; the
  selection never passes to whichever clip took its place.
- Click the empty frames between two clips (or before a track's first clip)
  to select that **gap**: it is hatched and outlined. **Delete** closes it,
  as does **Close Gap** in its right-click menu: the clips after it on that
  track move earlier by its length, as one step of undo. The other tracks
  and the markers stay where they are. The empty frames after a track's
  last clip are no gap.
- Right-click where there is no clip (empty track, the ruler, a track's
  name) for the timeline's menu. On a frame: **Add Marker Here** (or
  **Remove Marker Here**), **New Shot Here…**, and with a pick in the bins
  **Insert Bin Pick Here** and **Overlay Bin Pick Here**; each parks the
  playhead on the frame you clicked first, then does what its menu bar
  entry does there. **Add Video Track** and **Add Audio Track** put an
  empty track on top of its kind, named with the first free number. On a
  track's row (its name, or its empty frames):
  - **Move Track V2 Up** and **Move Track V2 Down** change its place with
    the track above or below it (among the tracks of its kind, and only
    where there is one that side). For video the picture changes with it:
    the upper track is drawn over the lower. The row's height and a solo
    go with the track.
  - **Rename Track V2…** asks for the new name. An empty name, or one
    another track of its kind has, is refused there, and the dialog says
    why.
  - **Track Colour of V2 (None)** lists the eight colours (Violet, Blue,
    Teal, Green, Olive, Ochre, Brick, Plum) and **None**; the colour the
    track has is named in the title and left out of the list. A coloured
    track has a stripe of its colour down the left of its name. The
    colour is saved in the edit, so everyone sees it.
  - **Remove Track V2** for an empty track, **Delete Track V2 and 3
    Clips** for one that holds clips: the track and every clip on it go,
    and the other tracks keep their clips where they are. Not offered for
    the last track of its kind.
  - **Solo** it and, once dragged, **Reset Track Height**.

  Moving, renaming, colouring and deleting a track are each one step of
  undo (a deleted track comes back with its clips, name and colour).
  Always: hide or
  show the audio tracks, **Select All** and **Fit Timeline**. A published
  edit offers only the rows that change nothing. (A right-click on a clip
  opens the clip's own menu, below. There is no Paste: the editor has no
  copy yet.)
- Right-click a clip, any clip (a shot's, a media item's, a slug, a
  sound), for what the edit can do to it. The right-click selects the clip
  first, as a click does: a clip that was not selected becomes the only
  one, and one that was keeps the selection it is part of. The rows, each
  only where it applies:
  - **Split Here**, at the frame you clicked, when that is not the clip's
    first frame; **Split at Playhead** too, when the playhead is over the
    clip at another frame.
  - **Delete** and **Ripple Delete**, as the Delete and Shift+Delete keys:
    they act on the selection, and say how much of it there is when more
    than the one clip is selected (**Delete 3 Clips**).
  - **Join with Next Clip**, when the clip after it continues it (the
    same source, the frames running on).
  - **Close Gap Before**, when there are empty frames before the clip on
    its track: as **Close Gap** on the gap, the clip and those after it on
    its track move earlier by the gap's length.
  - **Reset Level to 0 dB (now -6.0 dB)**, on a sound whose level is not
    0 dB.
  - **Go to Clip Start** and **Go to Clip's Last Frame** park the
    playhead there.

  A shot's clip keeps its own rows above these, as before (its takes,
  **Reveal folder**, **Open in Houdini** and the rest); a clip that is not
  a shot offers **Convert to Shot…** above them, and a converted clip its
  swap row (see [An animatic clip made a shot](#the-master-edit)). A
  published edit, which cannot change, offers of these only the two Go to
  rows. Each change is one step of undo. There is no mute or disable row:
  a clip has no such switch in the edit. (Up to 1.120.3 only a shot's clip
  had a right-click menu: on any other clip it opened empty.)
- Drag a clip to move it, onto another track of its kind too. With Alt held
  it slips: the clip stays and the frames in it move. A moved clip
  **snaps**: when its start or its end comes within 8 pixels of the cut's
  start, the playhead, a marker or an edge of a clip that stays, it lands
  exactly there (the nearest wins; a line shows where). And it always
  **lands**: dragged along its own track onto other clips, it goes in
  between them (below); dragged onto another track, or as one of several
  selected clips, where another clip is in the way it goes beside it,
  butted against the nearest free place it fits (as near either way, the
  one nearer where it came from). The clip drawn under the pointer is
  where it will land, and the tag beside it says *snapped* or *beside the
  clip there*. Several selected clips dragged along their own tracks move
  together, by one distance, and keep their spacing. A click that does not
  drag never moves a clip. (Up to 1.112.2 a move onto another clip was
  refused, and the clip went back.)
- A single clip dragged along its own track and let go on other clips is
  **inserted** among them, to change the order. Past the middle of a clip
  it goes after that clip, before the middle it goes before it. The clips
  between where it was and where it goes move over to make room, all by
  the same distance: as far as it overlaps the clip it now sits beside,
  never more than its own length, so they only move into the room it left
  and nothing past its old place moves. Clips that touched swap and still
  touch, the gaps between the clips that move stay as they were, the track
  keeps its length, and no other track moves. While you drag, the places
  the clips that make room will take are outlined, and the tag says so
  ("+40 · in between, 2 clips make room"). Letting go on free frames (the
  clip fits there) or onto another track moves the clip as above, as does
  a drag of one of several selected clips. Only the clips it passes move;
  to move every clip after a place, push (Ctrl, below). One step of undo. (Up to 1.120.3 a drop between
  two clips with too little room between them went to the nearest free
  place instead.)
- Hold **Ctrl** while dragging a clip's body to **push**: the clip and
  every clip after it on its track move together, so room opens (or
  closes) before it. It stops at the clip before it and at the cut's
  start. Only that track moves; the other tracks and the markers stay. The
  tag says how far and how many clips follow ("push +12 · 2 clips
  follow"). One step of undo.
- Over the top video track there is always one empty layer more, its name
  faint (*Drag a clip here for a new layer*). Drag a clip onto it and it
  becomes a track of the edit, with the next empty layer over it, so you
  never run out of layers. Undo takes the clip back and the track away in
  one step. The empty layer is only shown: it is not saved in the edit
  until a clip is on it. (A published edit, which cannot change, has none,
  and neither has a soloed track.)
- Drag a clip's edge to trim it; what follows moves with it. The clip keeps
  its start, and the clips after it on its track move by what it grew or
  shrank. On the other tracks no clip is ever cut or removed: when the
  trim puts frames in, the clips that start at or after that point move
  later with it; when it takes frames out, the clips after them move
  earlier only if no clip of that track starts inside the frames taken out
  and there is room before them, and otherwise that whole track stays
  where it is. The tag beside the edge says so while you drag ("-5 · 2
  clips follow · V2, A1 stay (no room)"). (Up to 1.112.2 a trim could cut
  into or remove clips on other tracks.) With Alt held
  the cut rolls: the clip and its neighbour change together and the length
  holds. A trim throws nothing away: the clip only shows less of its shot
  or media, and dragging the edge back out shows the frames again. An edge
  goes out as far as there is footage (a shot's frame range with its roll,
  a media item's frames; on the master edit with **Sync Shot Ranges** on, a
  shot's clip goes further and the shot grows with it, see
  [the master edit](#the-master-edit)) and stops there: a red bar on the edge, hatching
  past it, and *no more footage* beside the frame count. A slip and a roll
  stop the same way. (Up to 1.112.0 a drag past the footage was undone
  whole when you let go, so a trimmed clip dragged out too far snapped back
  to its trimmed length.)
- A trimmed or rolled edge **snaps** too: when it comes within 8 pixels of
  the start or end of a clip on another row, the playhead or a marker, it
  goes exactly there (the nearest wins). The clips on its own row are not
  snapped to, since they move with the trim. A line shows where it
  snapped, as for a move, and the tag adds *snapped*. Hold **Ctrl**
  (**Cmd** on macOS) while you drag the edge to trim freely, without
  snapping. Where the footage runs out before the snap point, the edge
  stops at the footage, not snapped (*no more footage*). (Up to 1.120.3
  only a move snapped.)
- Drag along a clip's top band to fade it in (left half) or out (right
  half). Where a clip on a higher track fades over one below, the two
  dissolve; over nothing, it fades from or to black.
- The timeline keeps its scale while you work: moving, trimming, slipping,
  rolling or fading a clip, and undo, never rescale the ruler, so a clip
  stays the size it was under the pointer. The tracks are fitted to the cut
  (and a second past it) when the edit opens and when the first clip goes
  into an empty edit; a change that makes the cut longer than the tracks
  show widens them once, when you let go; a shorter cut leaves them as they
  are. **View > Fit Timeline** (F) fits them to the cut again (up to
  1.110.0 the tracks refitted after every change, so a trim rescaled the
  whole ruler).
- **Zoom and scroll** the timeline with the wheel over the tracks:
  **Ctrl+wheel** zooms in and out about the frame under the pointer (a
  notch is a quarter; in as far as 8 frames, out as far as the whole
  timeline); **Shift+wheel**, a sideways wheel or a sideways swipe on a
  trackpad scrolls through time. The wheel alone scrolls the tracks up and
  down when they are taller than the timeline, as before. Under the
  tracks a thin strip shows the whole timeline, its clips and the
  playhead, with the part you see as a thumb as wide as that part is of
  the whole: drag the thumb to scroll, drag either end of it to zoom (the
  other end stays), or click the strip beside it to page a view's width
  that way (up to 1.119.0 a drag of the thumb stopped after its first move
  and a click centred the view). **View > Fit to
  Clips** (Shift+F, also in the timeline's right-click menu) shows the
  first clip's start to the last clip's end (at least 8 frames); **Fit
  Timeline** (F) shows everything. Zoomed in, an edit never changes the
  scale or the place. The zoom is not saved with the edit.
- The strip above the tracks shows the selected clip's **Fade in** and
  **Fade out** in frames and, for a video clip, its **Opacity** in
  percent. With no clip, or several, selected, the same fields stay there
  greyed (the strip and the tracks never move when the selection changes).
  Type a number and press Enter to set it (a fade that does not fit
  the clip is refused, and the status line says why). A clip at less than
  100% shows the tracks below through it, as inside a fade; at 0% it is not
  seen at all. The edit file writes `opacity` only for a clip under 100%,
  so an edit at full opacity reads in an editor from before it.
- The same strip holds a video clip's **transform**: **X** and **Y** move
  it right and up, in percent of the picture's height (so 50 in X moves it
  half the picture's height to the right, at any resolution); **Scale** is
  its size in percent, 100 fitting the picture; **Rotate** turns it, in
  degrees, anticlockwise. The clip is first fitted into the edit's picture
  as always, then scaled and turned about the picture's centre, then
  moved. **Reset** puts it back as fitted. Each change is one step of
  undo. Around a clip that is moved, scaled or turned, the tracks below
  show (black where there are none), in the viewer, in **Export Movie**
  and in a farm **Render Edit** alike. X and Y take -1000 to 1000, Scale 1
  to 10000, Rotate -3600 to 3600; anything else is refused. The edit file
  writes `transform` only for a clip that has one, so an edit without
  reads in a Cinedex from before it. Playing a clip with a transform is
  blended on the CPU, as a dissolve is, so it can play slower than a clip
  as fitted.
- Middle-drag one of these fields to step it, as in Houdini: a column of
  steps (100, 10, 1, and 0.1 for the opacity and the transform) opens at the pointer. Move up
  or down to pick a step, then left or right to change the value one step
  at a time. Letting go sets it, as one step of undo. Ctrl + middle-click
  puts the field back to its default: no fade, 100% opacity, no move or
  turn, 100% scale. The
  **Frames** field of **New shot…** steps the same way (its default is 48).
- Any of these fields takes maths: type `100/2` and Enter sets 50. `+ - * /`
  and brackets work (`(1001+48)*2`), with or without the field's `%` or `°`
  at the end. `+=10`, `-=10`, `*=2` and `/=2` change what the field holds
  (`+=10` on a fade of 5 sets 15). A leading sign is not that: `+10` is 10
  and `-10` is minus 10, since X, Y and Rotate take negative numbers. A
  fade, and **New shot…**'s **Frames**, round to whole frames, a half
  upwards (`7/2` is 4). A sum with no answer (`1/0`, `2+`) is refused like
  any text that is no number: the status line says why and the clip keeps
  its value. There are no names, exponents or decimal commas. TumblePipe's
  number fields in Houdini (Farm Submit, the Entity Editor, the Database
  Editor) read maths by the same rules.

Every change is one step of undo, however many clips it moves.

| Key | Does |
| --- | --- |
| Space, or Up | play / pause. Up alone, as in Houdini; with a key held, and Down, do nothing (up to 1.113.0 Up and Down changed the version, see Page Up below) |
| Left / Right | step one frame back / forward |
| Alt+Left / Alt+Right | jump one second back / forward |
| Ctrl+Left / Ctrl+Right | jump to the previous / next cut |
| Home / End | the first frame / the cut's end, one past its last frame (up to 1.101.0 End stopped on the last frame, so an Insert there split the last clip) |
| Page Up / Page Down | the selected clips (none: the clip under the playhead) show the next newer / older version of the take they show, pinned, as the take menu's Version rows do (up to 1.113.0 these three were on the Up and Down arrows, where a play habit from Houdini changed the cut) |
| Ctrl+Page Up / Ctrl+Page Down | the previous / next department of that kind (its newest version), as the Department rows |
| Shift+Page Up / Shift+Page Down | playblast / render, as the Source rows |
| S | split the selected clips at the playhead (none selected: the top video clip there) |
| M | a marker at the playhead |
| Delete | lift the selected clips, leaving a gap; with a gap selected (and no clip), close it |
| Shift+Delete | ripple delete: what follows closes the gap; with a gap selected, close it |
| Ctrl+Z / Ctrl+Shift+Z (Ctrl+Y) | undo / redo |
| Ctrl+O | open an edit |
| Ctrl+S / Ctrl+Shift+S | save / save as |
| Ctrl+A | select every clip |
| Escape | clear the selection |
| B | show / hide the burn-ins |
| V | View > Show: the cut, renders, playblasts, round (view only, never saved) |
| F | View > Fit Timeline: the tracks span the cut and a second past it again |
| Shift+F | View > Fit to Clips: the first clip's start to the last clip's end |
| Ctrl+wheel / Shift+wheel | zoom about the pointer / scroll through time |

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

### Making room

The bins and the timeline can be resized to make room for the picture:

- Drag the line between the bins and the picture to make the bins wider or
  narrower, and the line above the timeline to make it taller or shorter.
  The pointer turns into a resize arrow over the line. The picture takes
  whatever is left.
- Double-click the line to fold the bins (or the timeline) away to a thin
  strip with three dots. Click the strip to bring them back at the size
  they had.
- A timeline lower than its tracks scrolls them.
- Drag the bottom edge of a track's name (the pointer turns into a resize
  arrow there) to make that track taller or lower, from 24 to 240 pixels:
  a taller track shows a larger picture on its clips, a low one only their
  names. The other tracks keep their heights.
- **View > Hide Audio Tracks** takes the audio tracks' rows away, so the
  timeline is the video tracks alone; **Show Audio Tracks** brings them
  back. It is remembered like the sizes.
- Double-click a track's name to *solo* it: only that track's row shows
  (its name says *solo*). Double-click it again, or **View > Show All
  Tracks**, for every track. A solo lasts until you close the editor.

Hiding and soloing change only which rows you see, as in the picture
nothing is muted: every track still plays, is saved and published, and
moves with an insert or a ripple delete. A line under the rows says what is
hidden ("2 audio tracks hidden"). Clips without a row are not selected
(**Select All** takes only the clips you see), so Delete never removes
something hidden.

The sizes are remembered for you: the next Cinedex you open, in any project,
opens with them (a track's height by its place: V1, V2, A1, in every edit;
they are kept in `tracks.json` beside `layout.json`). A window smaller than when you set them squeezes the panels
to fit, and they are back at your size when the window is large again. On
the board (**Board** in the toolbar) the line above the timeline works the
same way. The sizes, and whether the bins show as a grid or a list, are
kept in `layout.json` in a `tumblepipe/cinedex`
folder of your own (`%LOCALAPPDATA%` on Windows, `~/.config` on Linux,
`~/Library/Application Support` on macOS); delete it to start from the
default sizes, and the list, again.

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
yet. A dissolve cuts the clip before it where the dissolve starts; when
that would leave the clip shorter than its own fade in, the dissolve is
left out and noted instead (up to 1.112 it was written, and the track was
not well formed). A fade over clips below, an opacity under 100% and a transform are
not in it: the clips stay as they are, and a yellow marker says what was
left out. (The opacity and the transform are kept in the clip's
`tumblepipe` metadata, so they are not lost from the file; Resolve does
not read them.) Its movies
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
complete. If another program (Resolve, a player) has the old movie open,
the export waits up to five seconds for it to let go before it gives up.
When it is done, **Play** opens the movie in your
player and **Show in Folder** its folder.

Under the file, the dialog asks what the movie is to be (at first the
whole cut at the edit's size, with sound, clean, as it always was before
there were settings):

| Setting | Choices |
| --- | --- |
| **Size** | **Edit's** (the edit's resolution), **Half**, or **Custom**: type a width or a height and the other follows, so the picture keeps the edit's shape. 16 to 16384 pixels a side. |
| **Frames** | **Whole cut**; **Selection**, from the first selected clip's start to the last one's end (offered when clips are selected); **Loop**, the range a Live view loops over (offered while it loops); or **Custom**, a first and a last frame counted from 1 at the cut's start, both included. The timecode track starts at the first frame's timecode. The editor has no in and out marks yet. |
| **Codec** | **H.264** in a `.mp4` or `.mov`, which plays everywhere, or **Motion JPEG** in a `.mov`: every frame is a whole picture, so an NLE scrubs it easily, and the file is several times larger. Choosing Motion JPEG changes a `.mp4` name to `.mov`. |
| **Quality** | **Best**, **High**, **Standard** or **Small file**. Standard is the encode Render Edit makes on the farm. |
| **Sound** | Untick for a movie with no sound track. |
| **Burn-ins** | Tick to have the viewer's burn-ins in the picture: the shot top left, the take top right, the cut's timecode bottom left and the take's frame bottom right, each frame saying what the viewer says there. They are laid out as the viewer lays them out on a full-HD picture and keep that share of a larger one; nothing is written over frames where nothing plays. They need a font on the machine (any desktop has one). |

The number fields take [maths](submit-jobs.md#maths-in-number-fields)
(`1920/2`). The line under the settings says what they come to (size,
frames, length, codec), or why no movie can be made of them. The settings
are remembered for you, on this machine, the next time you export
(`export.json` beside the editor's layout file); a remembered selection,
loop or custom range that the cut at hand does not have goes back to the
whole cut.

The codecs are the ones Houdini's `hffmpeg` can write on every machine. It
has no ProRes or DNxHD encoder, so those are not offered; its H.265 encoder
refuses many sizes and its hardware encoders depend on the graphics card, so
neither are those. **Render Edit** on the farm is unchanged: it renders the
whole published edit at its own size with the Standard encode and no
burn-ins.

The editor decodes with its own FFmpeg but encodes with Houdini's
`hffmpeg`: the one of the Houdini it was started from (`$HFS`), else the
newest Houdini installed in SideFX's default place. Set `TH_HFFMPEG` to use
another FFmpeg (it needs openh264 and AAC). With none, the dialog says so and
does not export. A live playblast never goes into a movie: a shot shows its
take, as in Render Edit. The viewer's exposure is not part of the movie,
and neither are its burn-ins unless **Burn-ins** is ticked.

### Export to Resolve / Premiere

**File > Export Timeline…** writes the cut as a timeline that DaVinci
Resolve and Adobe Premiere Pro import: the clips still as clips, each
referring to its own playblast or render, to go on cutting or to grade. It
exports the edit as it is in the window, saved or not, published or working,
every take as the version it shows now. Nothing in the project is written or
changed. Neither import has been checked in Resolve or Premiere yet (see
[what is not verified](#what-is-not-verified)).

The dialog asks for:

- **Format**. **Final Cut Pro 7 XML (Resolve, Premiere)**, the default, is
  the one both read. **OpenTimelineIO (Resolve)** is the `.otio` a publish
  writes, of the edit as it is now.
- **File**: the edit's name and version (`Main_v0003.xml`) in your Videos
  folder or the folder you last exported to; type another or **Browse…**.
- **Collect media beside the file**: hard-links every movie the cut plays
  into `<name>_media` beside the file, each under a name of its own
  (`S_010_animation_playblast_v0003.mp4`), and the timeline refers to
  those. Hard links take no extra disk space and leave the originals alone,
  but only work on the drive the media is on: a movie that cannot be linked
  is referred to where it is (never copied), and the summary says so. On
  for OpenTimelineIO (Resolve's OTIO import takes movies that share a file
  name for one movie, and every playblast is `vNNNN.mp4`), off for XML.
  EXR sequences are never collected.
- **Image sequences written for** (XML only): **Resolve** or **Premiere
  Pro**. The two refer to an EXR sequence differently: for Resolve the file
  is named with its frame range (`beauty.[1001-1100].exr`), for Premiere it
  is the first frame (`beauty.1001.exr`).

The file is written under a temporary name (`Main_v0003.xml.partial`) and
only replaces the file once it is whole, waiting up to five seconds for a
program that has the old timeline open. A folder of the file's name is
refused at once. When it is done the dialog lists
what was carried and, in yellow, everything that was dropped or
approximated, each with the clips it happened to.

| In the cut | Final Cut Pro 7 XML | OpenTimelineIO |
|---|---|---|
| Tracks | One to one, bottom first. Track names are not carried | One to one, with their names |
| Track colour | Not carried (the format has no place for it); the summary says so | Only in the track's metadata (`tumblepipe.color`); Resolve does not show it, and the summary says so |
| Clip | A clip named after its shot (or media item), on its frames | The same, named shot, department and version |
| Gap | A gap | A gap |
| Fade in or out | A one-sided Cross Dissolve on the clip's own track, whatever is below | A dissolve only for a plain dissolve or a fade over nothing; any other is left out, with a yellow marker |
| Opacity | An Opacity filter | Left out, with a yellow marker |
| Transform (moved, scaled or turned) | Left out: the clip is as fitted, and the summary names it | Left out, with a yellow marker (kept in the clip's metadata) |
| A shot's sound (its playblast's) | An audio clip linked to the picture, on a track of its own after the edit's audio tracks | Not carried: it comes with the movie when Resolve links it |
| Audio clip | A clip | A clip |
| Audio level | An Audio Levels filter (at most +12 dB) | Only in the clip's metadata |
| Audio fade | A one-sided Cross Fade | Only in the clip's metadata |
| Marker | A sequence marker | A red marker |
| Shot with no take yet | An offline clip, to relink later | A missing reference |
| Slug | A gap, and a marker over it with its text | A generator clip |
| Stacked render layers | The bottom layer only | The bottom layer only |
| [Check render](#check-renders) | Referred to; it will not play at speed | The same |
| Media not on disk | Referred to as written; offline until it is there | The same |
| A clip running past its movie's frames | Shortened to them | As written |
| Resolution, frame rate, start timecode | The sequence's | The timeline's (resolution only in its metadata) |

To import the XML:

- **Resolve**: **File > Import > Timeline…** (Ctrl+Shift+I), pick the
  `.xml`. Keep **Automatically import source clips into media pool** on and
  set the timeline resolution and frame rate to the edit's when it asks.
  Clips that come in offline: right-click them in the media pool, **Relink
  Selected Clips…**, and point at the folder.
- **Premiere Pro**: **File > Import…**, pick the `.xml`. It makes a bin
  with the sequence and its clips. Offline clips: right-click, **Link
  Media…**. An EXR sequence may come in as a still of its first frame; if
  it does, import the frames yourself (**File > Import…**, the first frame,
  **Image Sequence** ticked) and replace the still's footage with it.

To import the `.otio`, in Resolve: **File > Import > Timeline…**.

**Paths are this machine's.** The timeline refers to every file by the path
it has here (`file://localhost/P:/Show/...`, or `file://server/share/...`
for a network path), so it links by itself only where the project is at the
same path: the same drive letter on Windows, the same mount on macOS or
Linux. Opened on a machine that sees the project elsewhere (a Mac reading
`P:/` as `/Volumes/Projects`), every clip comes in offline and is relinked
by pointing the NLE at the folder once. Collected media has the same limit:
the links are beside the file, at this machine's path to it.

A playblast made before playblasts recorded their start frame is counted
from its shot's first frame with roll, which is where it starts unless the
shot's range has changed since.

#### What is not verified

Neither format's import has been run in Resolve or Premiere Pro. In
particular: how each takes a one-sided dissolve over a clip on a track
below, the linked sound clips (one stereo clip item per clip), image
sequences in either NLE's form, and clips offline for media that is not
there. The file itself is checked: well formed, every clip on the frames
the edit shows, every path reading back from its URL.

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

**Edit > Sync Shot Ranges** (on unless you turn it off, for the window's
session) writes a shot's range as you trim, instead of at the publish.
While the window is on the master edit's working version, trimming a
shot's clip sets the shot's frames there and then: trimming the tail sets
its end frame, trimming the head its start frame (a shot whose renders start
at 1001, its head trimmed by 8, starts on 1009: frame 1009 is still the
renders' 1009). A slip moves both, a roll one end of each of the two shots.
Only the end the clip moved is written. **Undo** moves the clip back and
writes the frame back, **Redo** again. The edge of a shot's clip then goes
out past the shot's frames (that is how a shot gets longer); the frames no
take has yet show as missing until a new playblast or render. The status
line says what was written. A shot whose range someone else changed since
the editor read it is left alone, and the status line says so (the publish
still offers its range). Nothing is written for slugs, media, planned
shots, any other edit, or a published version; turn it off and the
publish offers the ranges as before.

**The cut's sound in a shot's scene.** The TumblePipe shelf's **Load Audio
from Edit**, in a shot's workfile, mixes the master edit's audio tracks
(the ones you pick) over the shot's frames and sets the mix as the scene's
audio, so the animation is timed against the cut's dialogue. See
[The edit's sound in a shot scene](index.md#the-edits-sound-in-a-shot-scene).

**A new shot from the timeline.** Hold **Alt** and drag along an empty
stretch of a video track: an amber outline shows the frames, with how many.
Let go, and **New shot** opens over them: its **Frames** are the frames
dragged, the name is the sequence's next free one, and **Create the shot
now** is ticked. **Create Shot** makes the shot in the project at once (its
frames from the project's start frame, 1001 unless the project sets
another, for the length dragged, on the shared timelines of the Multis
listing its sequence, as a publish would make it) and puts its clip where
you dragged. Untick it for **Plan Shot**, a planned shot made when the
master edit is published. **Placeholder** puts a slug of those frames
there instead. The drag stays inside the empty stretch it started in, so
the clip fits; type more frames than that and it goes on the lowest track
above with room. The clip is one step of undo; a shot made stays made
(retire it from the master's publish or the Entity Editor). A drag without
Alt is still the box select, and Alt on a clip still slips or rolls it.

**An animatic clip made a shot.** Map the film out as a 2D animatic first
(media or slugs on a video track), then right-click a clip that is not a
shot and choose **Convert to Shot…**: the same **New shot** dialog opens
over that clip's frames. **Create Shot** (or **Plan Shot**) puts the new
shot's clip in the animatic clip's place, on its track at its frame,
keeping its fades; one step of undo. The animatic clip is not lost: the
shot clip keeps it, saved with the edit, and its right-click offers **Swap
to Original Clip**, which puts the animatic back in the shot clip's span
(the animatic's right-click then offers **Swap Back to Shot**). The
animatic follows what is done to the shot clip: split it and each half
swaps back to its own part of the animatic; trim, roll, slip or move it
and the animatic is trimmed, rolled, slipped or moved with it. **Join with
Next Clip** joins two halves whose animatics continue each other, and is
refused when it would lose one (the other clip has none, or another). A
publish keeps only what plays, not the clip a clip replaced.

On a project that lists its shots flat (Settings > **Flat shot list (no sequences)**), New
shot has no **Sequence** field: every new shot goes into the project's
hidden sequence, offered a name no shot has, and a name any shot already
has (in any case) is refused.

## Running it by hand

The editor ships inside the TumblePipe package, as
`bin/<platform>/cinedex`:

```
cinedex [--project <root>] [--ocio <config>] [--edit entity:/edits/<name>[@vNNNN] [--view <shot uri> [--at <frame>]] | --shots <shot uri>...]
cinedex [--project <root>] --mcp
cinedex [--project <root>] --shot-audio entity:/edits/<name>[@vNNNN | @vNNNN_working] --shot <shot uri> --out <wav> [--tracks <A1 | name,...>] [--first <frame> --last <frame>]
cinedex [--ocio <config>] --frames <batch folder | frames folder | frame | movie> [--fps <rate>] [--expect <frame list>]
```

`--edit` opens the edit's newest working version, or with `@vNNNN` that
published version (an edit never saved, every shot of the project);
`--shots` the shots end to end as an untitled edit; neither, every shot of
the project as an untitled edit. The project is `--project`, else
`$TH_PROJECT_PATH`; the OCIO config `--ocio`, else `$OCIO`. When either is
missing, or the edit does not open, the window says why. `--view` opens the
edit as [Live view](#live-view) shows a shot, `--at` at that shot frame.
`--mcp` opens no window: see [Driving it from Claude](#driving-it-from-claude).
`--shot-audio` opens no window either: it mixes the edit's sound over a
shot into a WAV, what **Load Audio from Edit** runs (see
[The edit's sound in a shot scene](index.md#the-edits-sound-in-a-shot-scene)).
The edit is its newest working version, with `@vNNNN` that published
version, or with `@vNNNN_working` that working version (an edit never
saved has no sound and is refused, as is a version it does not have).
Load Audio from Edit and its refresh always name the version: the tracks
are asked for by their places in it, so a working version saved in the
meantime must not be the one mixed. The summary's `version` and
`published` say which was, and TumblePipe refuses a mix of any other than
it asked for. `--shot` is the
shot (`entity:/shots/SQ010/SH020`), which must have one clip in the edit
(none, or two, is refused). `--tracks` picks the audio tracks by place
(`A1` is the first audio track) or by name, comma-separated, case
ignored; a place wins over a name, so with a track named `A1` sitting
second, `A1` is the first track and `A2` that one (the editor names new
tracks `A1`, `A2`, ... and they keep the name when moved). Without it
every audio track is mixed, and a word naming no track is refused with
the tracks the edit has. `--first` and `--last` (together)
are the shot frames to cover; without them the shot's range with its
roll, else its clip's frames. The WAV (48 kHz, 16-bit, stereo) starts on
the first of those frames; its folders are made. It mixes as a farm
Render Edit does (each clip's level and fades), the audio tracks only,
never the shots' own playblast sound. It prints a summary as one line of
JSON: `edit`, `version`, `published`, `shot`, `first`, `last`,
`edit_first` (the edit frame the first frame lands on), `fps`, `rate`,
`samples`, `tracks` (the names mixed), `clips` (how many sound in the
window) and `out`. No option but these and `--project` goes with it (no
`--ocio`).
`--frames` opens [the preview window](#the-preview-window) on frames, and
no edit: given a local farm batch folder (one holding `batch.json`, under
`batches/` in the local farm's folder), the preview frames its runner
recorded as ready, every preview of the batch in job order, back to back;
given any other folder, the EXR and JPG frames in it, in frame order, with
any frame missing between the first and the last shown as a gap; given a
frame file, that frame; given a movie (a playblast's `.mp4`), its pictures.
It needs only the OCIO config. `--fps` is the rate
it plays at (1 to 240, default 24). `--expect` (with a folder) is the frame
list the render writes, as Deadline spells one (`1001-1100`,
`1001,1050,1100`, `1-99x2`): frames of it not in the folder yet are gaps,
so a render's unstarted end shows too. *Preview in Cinedex* passes both.

While it runs, it listens on a port of 127.0.0.1 for Houdini's Live view
calls and agents' commands; the port and a token it checks are in
`players/<project key>/<pid>.json` under the live folder, and the file is
removed when it quits.

Started from Houdini, it is told how to reach that Houdini in its
environment: `TH_HOUDINI_ENDPOINT` (a port of 127.0.0.1 the Houdini listens
on) and `TH_HOUDINI_TOKEN`. **Open in Houdini** asks that Houdini to open
the workfile there.

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
