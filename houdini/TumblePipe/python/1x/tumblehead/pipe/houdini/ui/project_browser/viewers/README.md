# USD Viewer Integration

This module provides integration for external USD viewers in the Tumblehead pipeline, allowing artists to view USD files outside of Houdini using artist-friendly tools.

## Overview

The USD Viewer integration adds support for launching external USD viewing applications like:
- **3D-Info** - Lightweight, artist-friendly viewer (Recommended for animators/artists)
- **USD Manager** - File browser and editor from DreamWorks (Recommended for TDs)
- **usdview** - Pixar's official USD viewer

## Features

### 1. Automatic USD File Detection
- When you click "Open Location" on a USD file in the Project Browser, it automatically launches in your configured viewer
- Falls back to file browser if no viewer is configured

### 2. Context Menu Integration
- Right-click on USD files in the Version view
- "View USD in..." submenu appears with configured viewers
- Quick access to viewer configuration

### 3. Settings Management
- Configure viewer executable paths in Project Browser → Settings
- Choose your preferred default viewer
- Test viewer configurations

### 4. Shelf Tools
Three new shelf tools in the TumblePIPE shelf:
- **View in 3D-Info** - Launch selected node's USD output in 3D-Info
- **View in USD Manager** - Launch selected node's USD output in USD Manager
- **Configure USD Viewers** - Open viewer settings dialog

## Setup Instructions

### Step 1: Download USD Viewers

#### 3D-Info (Recommended for Artists)
1. Visit: https://gitlab.com/3d-info/3d-info/-/releases
2. Download the latest version for your platform (Windows/Linux)
3. Extract and note the executable path
   - Windows: `3d-info.exe`
   - Linux: `3d-info` or `3d-info.AppImage`

**Why 3D-Info?**
- Specifically designed for non-technical artists
- Simple, clean interface
- Animation playback support
- Same rendering quality as usdview (Storm engine)
- No dependencies required

#### USD Manager (Recommended for TDs)
1. Visit: https://github.com/dreamworksanimation/usdmanager
2. Clone or download the repository
3. Note the path to the main executable/script
   - Usually: `usdmanager.py` or packaged executable

**Why USD Manager?**
- Excellent for browsing USD layer hierarchies
- File reference navigation
- Edit capabilities
- Perfect for texture artists tracking references
- Pipeline integration hooks available

#### usdview (If Already Installed)
- Usually included with USD installation
- Often bundled with Houdini
- Check: `where usdview` (Windows) or `which usdview` (Linux)

### Step 2: Auto-Detection (No Configuration Needed!)

**usdview (Default Viewer):**

The pipeline automatically detects usdview from Houdini's bundled tools using `hou.getenv('HFS')`. This works automatically when running inside Houdini. **No configuration needed!**

The viewer is located at:
- **Windows:** `$HFS/bin/usdview.cmd` (batch script that calls hython)
- **Linux:** `$HFS/bin/usdview`
- **macOS:** `$HFS/bin/usdview`

The "View Latest Export" button will use usdview by default. Just select a shot and click the button!

**To override:** Set a custom usdview path in Settings → Configure USD Viewers if you want to use a different usdview installation.

**3D-Info (Alternative Viewer):**

If you prefer 3D-Info, it can be auto-detected via the `TH_BIN_PATH` environment variable:

Windows (in Houdini launcher script):
```batch
set TH_BIN_PATH=W:\_pipeline\bin
```

Linux/macOS:
```bash
export TH_BIN_PATH=/path/to/pipeline/bin
```

Expected directory structure:
```
W:\_pipeline\bin\
└── cst_3dinfo-v0.4.4\
    ├── cst_3dinfo.exe       (Windows)
    ├── bin\
    ├── plugins\
    ├── resources\
    └── ...
```

To use 3D-Info as your default viewer, change the preference in Settings → Configure USD Viewers.

### Step 3: Manual Configuration (Alternative)

If `TH_BIN_PATH` is not set or you want to override the default path:

#### Method 1: Via Project Browser Settings
1. Open Houdini and the Project Browser panel
2. Click the **Settings** tab
3. In the "USD Viewers" section, click **"Configure USD Viewers..."**
4. Click "Browse..." next to each viewer you downloaded
5. Select the executable file
6. Choose your preferred default viewer from the dropdown
7. Click **"Test Viewers"** to verify configuration
8. Click **"Save"**

#### Method 2: Via Shelf Tool
1. Click the **"Configure USD Viewers"** tool in the TumblePIPE shelf
2. Follow the same configuration steps as above

### Step 4: Verify Installation

1. In Project Browser, navigate to a USD file in the Version view
2. Right-click on the USD file
3. You should see "View USD in..." with your configured viewers
4. Select a viewer to test the launch

## Usage

### From Project Browser (Automatic)

1. Navigate to a USD file in any browser view
2. Click **"Open Location"**
3. If it's a USD file, it will automatically open in your preferred viewer
4. If not configured, it falls back to the file browser

### From Context Menu (Manual Selection)

1. Right-click on a USD file in the Version view
2. Hover over **"View USD in..."**
3. Select which viewer to use:
   - 3D-Info (Artist-Friendly)
   - USD Manager (File Browser)
   - usdview (Pixar Official)
   - Configure Viewers... (opens settings)

### From Shelf Tools

#### View in 3D-Info
1. Select a node with USD output (e.g., USD ROP, Export Layer)
2. Click **"View in 3D-Info"** shelf tool
3. The node's USD output file opens in 3D-Info

Works with parameters: `lopoutput`, `usdfile`, `file`

#### View in USD Manager
Same as above but opens in USD Manager instead

### From Python/Script

```python
from pathlib import Path
from tumblehead.pipe.houdini.ui.project_browser.viewers.usd_viewer import (
    USDViewerLauncher,
    USDViewerType,
    launch_usd_viewer
)

# Quick launch with default viewer
file_path = Path("/path/to/file.usd")
launch_usd_viewer(file_path)

# Launch specific viewer
launcher = USDViewerLauncher()
launcher.launch_viewer(file_path, USDViewerType.THREE_D_INFO)

# Check if viewer is configured
if launcher.is_viewer_configured(USDViewerType.USD_MANAGER):
    launcher.launch_usd_manager(file_path)
```

## Troubleshooting

### "Viewer not configured" Message
**Solution:** Configure the viewer path in Settings → Configure USD Viewers

### "Viewer executable not found" Error
**Possible causes:**
1. Wrong path configured
2. Viewer was moved or deleted
3. Permissions issue

**Solution:**
1. Open Configure USD Viewers
2. Click "Test Viewers" to see which ones are working
3. Browse to correct executable path
4. Save and retry

### Shelf Tool Says "No USD Output"
**Possible causes:**
1. Selected node doesn't have USD output parameter
2. Parameter is empty
3. Parameter name is non-standard

**Solution:**
1. Check node has `lopoutput`, `usdfile`, or `file` parameter
2. Make sure parameter contains a valid USD file path
3. Render/cook the node first if needed

### USD File Opens in File Browser Instead of Viewer
**Possible causes:**
1. No viewer configured
2. Viewer path is wrong
3. Not actually a USD file

**Solution:**
1. Check Settings → USD Viewers
2. Configure at least one viewer
3. Set preferred default viewer

### Linux: Permission Denied
**Solution:**
```bash
chmod +x /path/to/3d-info
# or
chmod +x /path/to/usdview
```

## Recommended Workflows

### For Animators
1. Use **3D-Info** as default viewer (set in preferences)
2. Export animation from Houdini
3. Right-click USD file → "Open Location" (auto-launches 3D-Info)
4. Review animation playback
5. Return to Houdini for adjustments

### For Lighters
1. Use **3D-Info** for quick lighting reviews
2. Export lighting layer from Houdini
3. Use shelf tool: Select ROP → "View in 3D-Info"
4. Review lighting in viewer
5. Iterate in Houdini

### For Technical Directors
1. Use **USD Manager** as default (more technical features)
2. Export USD from Houdini
3. Right-click → "View USD in..." → USD Manager
4. Inspect layer hierarchy and references
5. Navigate to texture files and dependencies

### For Texture Artists
1. Use **USD Manager** (best for reference navigation)
2. Right-click on USD file → View in USD Manager
3. Browse to texture references
4. Check texture paths and file structure
5. Open referenced texture files directly

## Architecture

### Module Structure
```
viewers/
├── __init__.py           # Package exports
├── usd_viewer.py         # Core launcher and viewer types
└── README.md             # This file

dialogs/
└── usd_viewer_settings.py  # Settings dialog UI

widgets/
└── usd_viewer_menu.py      # Context menu helper
```

### Key Classes

#### `USDViewerLauncher`
Main class for launching USD viewers
- Manages viewer configuration via QSettings
- Handles process launching
- Emits signals on success/failure
- Detects USD file types

#### `USDViewerType` (Enum)
Viewer type enumeration:
- `THREE_D_INFO` - 3D-Info viewer
- `USD_MANAGER` - USD Manager
- `USDVIEW` - Pixar's usdview
- `AUTO` - Use preferred viewer from settings

#### `USDViewerSettingsDialog`
Qt dialog for configuring viewers:
- Browse for executable paths
- Set preferred default viewer
- Test viewer configurations
- Help text with download links

#### `USDViewerContextMenu`
Helper for adding USD viewer options to context menus:
- `add_usd_menu_actions()` - Adds menu items
- `handle_usd_menu_action()` - Handles selection

### Integration Points

1. **main.py** - Modified `_open_location_path()` to auto-launch USD viewers
2. **version.py** - Added context menu integration for USD files
3. **settings.py** - Added "Configure USD Viewers" button
4. **tumblehead.shelf** - Added three new shelf tools

### Settings Storage

Settings are stored using Qt's QSettings:
- Organization: "Tumblehead"
- Application: "ProjectBrowser"

Keys:
- `usd_viewer/preferred` - Default viewer type
- `usd_viewer/3d-info_path` - 3D-Info executable path
- `usd_viewer/usd-manager_path` - USD Manager path
- `usd_viewer/usdview_path` - usdview path

## Future Enhancements

Possible future additions:
- Support for additional USD viewers (e.g., NVIDIA Omniverse)
- Frame range passing for animation playback
- Render settings passthrough
- Multiple file selection support
- Recent files list in viewer
- Viewer-specific launch arguments
- Custom environment variables per viewer

## Support

For issues or questions:
1. Check this README's Troubleshooting section
2. Verify viewer installation and configuration
3. Test viewers using the "Test Viewers" button
4. Check Houdini console for error messages

## Credits

**Research Sources:**
- [3D-Info GitLab](https://gitlab.com/3d-info/3d-info)
- [USD Manager GitHub](https://github.com/dreamworksanimation/usdmanager)
- [CG Channel - 3D-Info Article](https://www.cgchannel.com/2024/09/3d-info-is-a-lightweight-open-source-viewer-for-usd-scenes/)
- [OpenUSD Documentation](https://openusd.org/)

**Developed for:** Tumblehead Pipeline
**Module Version:** 1.0
**Last Updated:** December 2025
