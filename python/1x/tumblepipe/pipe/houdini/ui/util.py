import hou

def open_project_browser():

    # Get current pane
    pane = hou.ui.paneUnderCursor()
    assert pane is not None, 'No pane found!'
    
    # Check if the node panel is already open
    panel = pane.tabOfType(hou.paneTabType.PythonPanel)
    pipe_interface = hou.pypanel.interfaceByName('tumblehead_project_browser')
    if panel is not None:
        curr_interface = panel.activeInterface()
        if curr_interface == pipe_interface:
            panel.setIsCurrentTab()
            return
    
    # Create a new node panel
    panel = pane.createTab(hou.paneTabType.PythonPanel)
    panel.setActiveInterface(pipe_interface)
    panel.showToolbar(False)

def open_project_config():

    # Get current pane
    pane = hou.ui.paneUnderCursor()
    assert pane is not None, 'No pane found!'
    
    # Check if the node panel is already open
    panel = pane.tabOfType(hou.paneTabType.PythonPanel)
    pipe_interface = hou.pypanel.interfaceByName('tumblehead_project_config')
    if panel is not None:
        curr_interface = panel.activeInterface()
        if curr_interface == pipe_interface:
            panel.setIsCurrentTab()
            return
    
    # Create a new node panel
    panel = pane.createTab(hou.paneTabType.PythonPanel)
    panel.setActiveInterface(pipe_interface)
    panel.showToolbar(False)

def open_render_submit():

    # Get current pane
    pane = hou.ui.paneUnderCursor()
    assert pane is not None, 'No pane found!'
    
    # Check if the node panel is already open
    panel = pane.tabOfType(hou.paneTabType.PythonPanel)
    pipe_interface = hou.pypanel.interfaceByName('tumblehead_render_submit')
    if panel is not None:
        curr_interface = panel.activeInterface()
        if curr_interface == pipe_interface:
            panel.setIsCurrentTab()
            return
    
    # Create a new node panel
    panel = pane.createTab(hou.paneTabType.PythonPanel)
    panel.setActiveInterface(pipe_interface)
    panel.showToolbar(False)

def center_all_network_editors():
    desktop = hou.ui.curDesktop()
    for pane_tab in desktop.paneTabs():
        if pane_tab.type() != hou.paneTabType.NetworkEditor: continue
        pane_tab.requestZoomReset()
        pane_tab.redraw()

def vulkan_all_scene_viewers():

    def _current_renderer(pane_tab):
        try: return pane_tab.currentHydraRenderer()
        except: return None
    
    desktop = hou.ui.curDesktop()
    for pane_tab in desktop.paneTabs():
        if pane_tab.type() != hou.paneTabType.SceneViewer: continue
        current_renderer = _current_renderer(pane_tab)
        if current_renderer is None: continue
        if current_renderer == 'Houdini VK': continue
        pane_tab.setHydraRenderer('Houdini VK')