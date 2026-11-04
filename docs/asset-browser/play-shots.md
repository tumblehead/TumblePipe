# Playing shots

Right-click a shot card and choose **Play**, or select several shots and
choose **Play N Shots**, to watch them back to back in the TumblePipe
sequence player. It opens in its own window, outside Houdini. Nothing in
the project changes, and Resolve is not involved.

## What it plays

Every shot gets a list of **takes**:

- each of its **playblasts**, every department and every version;
- each of its **renders**, every render department, layer and version whose
  frames are all there, as an EXR sequence: its `beauty` AOV, or its first
  AOV when it has no beauty. A render still running, or one that stopped
  short, is left out.

Departments come in pipeline order (layout before animation before light,
...) and a render's layers by name, with the slapcomp last.

### Render layers stacked

A shot's render layers are its channels (`default` first, then the others).
Where they were made apart, a department also gets **layers** takes
(`animation v0015 layers`), right after its other takes of that kind:

- a playblast version made with th::playblast's **Render Layers** has a
  movie per layer (that layer's prims on a transparent background); its
  layers take stacks the ones it has;
- a render version with two or more channel layers whose frames are all
  there stacks them (the slapcomp is not one of them: it comps them
  already). Layers whose frame range differs from the bottom one's are left
  out.

The layers are stacked in channel order, the first at the bottom, each laid
"over" the ones below: playblast layers over black as they are, render
layers in scene linear and then through the display transform like any
render. A shot never opens on a layers take while it has another take.

When the shot under the playhead shows a layers take, a row per layer
stands over the track, the top layer on top as in an editor. Each row has
the layer's key, its name, and a filled box when it shows (an empty one and
**(hidden)** when it does not). Click a row, or press its digit (**1** is
the bottom layer, up to **9**), to show or hide that layer. The toolbar
over the picture says which layers show. The player remembers what you hid
on each layers take until it closes. The rows go away on any other take.
Hiding or showing a layer decodes that shot's frames again.

A shot opens on the playblast written last, whichever department it was cut
at: a farm playblast cut at animation after one cut at render opens, not the
older render one. (Two written at the same moment go to the more downstream
department.) A shot with no playblasts opens on its
latest render. A shot with neither is left out, and Houdini names the shots
left out. When none of the chosen shots has anything to play, the menu entry
reports it. A shot whose frame range or frame rate is not set in the project
cannot be placed on the track, and the menu entry reports that too.

## The track

Under the video, the track shows the shots in the order they had in the
selection. Each shot's clip is as long as its take; while the take opens, or
when it cannot play, the clip is as long as the shot's cut and says so. A
clip shows the first frame
of the take it plays, the shot's name, the take, and whether that is a
playblast or a render. The ruler along the top has a tick per frame (per 5,
10, ... frames when the shot is long), and a green strip along its bottom
marks the frames already decoded.

Click or drag on the ruler to seek. Clicking a clip seeks too. Right-click a
clip to choose its take (see below).

Up and Down change the shot under the playhead. To change several shots at
once, select them: Ctrl-click a clip (Cmd-click on macOS) to add it or take
it out, Shift-click to select the run of shots from the last one clicked,
Ctrl+A for all of them. The shots a change applies to are outlined. A plain
click on a clip, or Escape, clears the selection.

Changing a shot's take changes only that shot. The others keep the frames
already decoded, and the playhead stays on its frame.

A take the player has not shown yet has to be opened first, which can take a
few seconds on the file server. The player keeps working meanwhile: the
shot's clip names the new take and says **Opening**, and the shot plays its
old take until the new one is open. Press Up or Down again while it opens
and it steps on from the new take. A take shown before opens at once.

## In the player

The player is driven from the keyboard:

| Key | Does |
| --- | --- |
| Space | play / pause |
| Left / Right | step one frame back / forward |
| Alt+Left / Alt+Right | jump one second back / forward (the shot's frame rate, rounded) |
| Ctrl+Left / Ctrl+Right | jump to the previous / next shot |
| Home / End | the first / last frame |
| Up / Down | a newer / older version of the same department |
| Ctrl+Up / Ctrl+Down | the department upstream / downstream, its latest version |
| Shift+Up | the playblast |
| Shift+Down | the render |
| Ctrl+A | select every shot |
| Escape | clear the selection |
| B | show / hide the burn-ins |
| 1 to 9 | show / hide layer 1 to 9 of a layers take (1 is the bottom) |

On macOS, use Cmd for Ctrl. Up and Down stay within what the shot shows. For
a playblast they step through that department's playblasts. For a render
they step through the versions of that department and layer. Ctrl+Up and
Ctrl+Down move between the departments (and render layers) of the same
kind. Shift+Up and Shift+Down switch between the playblast and the render,
going back to the one the shot showed last. When it has not shown one of
that kind yet, they go to the latest of the most downstream department.

Right-click a shot's clip to change its take. The menu has three submenus,
each showing what is there for the take on screen: **Source** (playblast or
render), **Department** (the departments of that source; a department
rendering several layers lists each, as `light (slapcomp)`, and its layers
takes as `light (layers)`) and **Version**
(that department's versions, newest first). The current choice is marked
**(shown)**. Choosing a source or department goes where its key would: the
source to the take you last showed of it, else its newest; a department to
its newest version. Hover a row to open its submenu, or use the arrow keys:
Right opens a submenu, Left closes it, Enter picks. That changes only the
shot clicked, selected or not. Clicking a menu or a control
never takes the keys: Space and the arrows keep working as above.

Playback shows every frame: when the next frame is not decoded yet, the
player waits for it instead of skipping it, so what you see is always
frame-accurate. After a take change, the old take's frame stays up until the
new one's is decoded. A render's frames are large, so each can take seconds
to come from the file server. The player reads the frame under the playhead
first, on its own, and the frames after it once that one is in.

Burn-ins over the picture name what is on screen: the sequence and shot top
left (`HideAndReek / 010`), the take top right (`animation v0015`), the frame
rate bottom left (`24 fps`) and the frame bottom right. A playblast's frames
carry no numbers, so that is the frame's place in the take (`46 / 68`); an
EXR take's frame shows its own number first (`1046 (46 / 68)`). They
describe the frame shown, so while a new take loads they still name the old
one. **Burn-ins** in the toolbar over the picture, or B, shows or hides
them. They are only drawn in the player: nothing is written into the media.

What went wrong is said where it is: while the playhead is on a take that
will not open, a frame that will not decode, or a render the chosen display
and view cannot show, the reason is on a line of its own under the track,
and in the picture when there is no frame to show. Playback does not stop
for it: it plays on past the shot.

Playblasts are shown as they are. EXR takes are scene-linear, so the player
shows them through the project's OCIO config (`$OCIO`, which the TumblePipe
package sets; the player does not start without one). It reads the input
colour space from the EXR (ACEScg for TumblePipe renders; a render that
does not name one cannot play)
and uses the config's default display and view (`sRGB - Display`,
`ACES 1.0 - SDR Video`). For an EXR take you can also choose (layer and
channel in the toolbar over the picture, display, view and exposure right
under it):

- **Layer** and channel: any layer of the file, and the whole layer
  (**Colour**) or one of its channels on its own (shown as grey);
- display and view: any of the config's;
- **Exposure**: in stops, before the display transform.

The window around the image is neutral grey, so it does not shift how the
image's colours read.

The player ships inside the TumblePipe package, as
`bin/<platform>/th_player`. TumblePipe hands it the playlist (the shots,
their takes and the OCIO config, as JSON) on its standard input; it takes no
arguments. To run it by hand, give it a playlist the same way:

```
th_player < playlist.json
```

The playlist's schema is the `th_playlist` crate's (`src/player/playlist`).

It is built for Windows, Linux and macOS. See
[Contributing and development](../development.md#sequence-player) for how it
is built.
