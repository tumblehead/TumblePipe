from functools import partial
from pathlib import Path
import datetime as dt

from qtpy.QtCore import Qt, Signal, QEvent
from qtpy.QtGui import QStandardItemModel, QStandardItem, QBrush
from qtpy import QtWidgets
from hou import qt as hqt
import hou

from tumblehead.api import (
    path_str,
    default_client,
    get_user_name
)
from tumblehead.config import (
    BlockRange,
    FrameRange
)
from tumblehead.util.io import (
    load_json,
    store_json
)
import tumblehead.pipe.context as ctx
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini.ui.util import (
    center_all_network_editors,
    vulkan_all_scene_viewers
)
from tumblehead.pipe.houdini import util
from tumblehead.pipe.houdini.lops import (
    build_shot,
    animate,
    export_asset_layer,
    export_kit_layer,
    export_shot_layer,
    export_render_layer,
    import_assets,
    import_asset_layer,
    import_kit_layer,
    import_shot_layer,
    import_render_layer,
    render_vars,
    render_settings,
    lookdev_studio
)
from tumblehead.pipe.houdini.sops import (
    export_rig,
    import_rigs,
    playblast,
    cache
)
from tumblehead.pipe.houdini.cops import (
    build_comp
)
from tumblehead.pipe.paths import (
    list_asset_hip_file_paths,
    list_shot_hip_file_paths,
    list_kit_hip_file_paths,
    get_asset_hip_file_path,
    get_shot_hip_file_path,
    get_kit_hip_file_path,
    latest_asset_hip_file_path,
    latest_shot_hip_file_path,
    latest_kit_hip_file_path,
    next_asset_hip_file_path,
    next_shot_hip_file_path,
    next_kit_hip_file_path,
    latest_asset_export_path,
    latest_shot_export_path,
    latest_kit_export_path,
    get_workfile_context,
    AssetEntity,
    ShotEntity,
    KitEntity,
    AssetContext,
    ShotContext,
    KitContext,
    Context
)

api = default_client()

AUTO_SETTINGS_DEFAULT = dict(
    Asset = dict(
        Save = True,
        Refresh = True,
        Publish = False
    ),
    Shot = dict(
        Save = True,
        Refresh = True,
        Publish = False
    ),
    Kit = dict(
        Save = True,
        Refresh = True,
        Publish = False
    )
)

class Location:
    Workspace = 'Workspace'
    Export = 'Export'
    Texture = 'Texture'

class Section:
    Asset = 'Asset'
    Shot = 'Shot'
    Kit = 'Kit'

class Action:
    Save = 'Save'
    Refresh = 'Refresh'
    Publish = 'Publish'

class FrameRange:
    Padded = 'Padded'
    Full = 'Full'

def _get_context():
    file_path = Path(hou.hipFile.path())
    return get_workfile_context(file_path)

def _latest_asset_context(category_name, asset_name, department_name):
    file_path = latest_asset_hip_file_path(category_name, asset_name, department_name)
    version_name = None if file_path is None else file_path.stem.rsplit('_', 1)[-1]
    return AssetContext(department_name, category_name, asset_name, version_name)

def _latest_shot_context(sequence_name, shot_name, department_name):
    file_path = latest_shot_hip_file_path(sequence_name, shot_name, department_name)
    version_name = None if file_path is None else file_path.stem.rsplit('_', 1)[-1]
    return ShotContext(department_name, sequence_name, shot_name, version_name)

def _latest_kit_context(category_name, kit_name, department_name):
    file_path = latest_kit_hip_file_path(category_name, kit_name, department_name)
    version_name = None if file_path is None else file_path.stem.rsplit('_', 1)[-1]
    return KitContext(department_name, category_name, kit_name, version_name)

def _next_file_path(context):
    match context:
        case AssetContext(department_name, category_name, asset_name, _):
            return next_asset_hip_file_path(category_name, asset_name, department_name)
        case ShotContext(department_name, sequence_name, shot_name, _):
            return next_shot_hip_file_path(sequence_name, shot_name, department_name)
        case KitContext(department_name, category_name, kit_name, _):
            return next_kit_hip_file_path(category_name, kit_name, department_name)
    assert False, f'Invalid context: {context}'

def _latest_file_path(context):
    match context:
        case AssetContext(department_name, category_name, asset_name, _):
            return latest_asset_hip_file_path(category_name, asset_name, department_name)
        case ShotContext(department_name, sequence_name, shot_name, _):
            return latest_shot_hip_file_path(sequence_name, shot_name, department_name)
        case KitContext(department_name, category_name, kit_name, _):
            return latest_kit_hip_file_path(category_name, kit_name, department_name)
    assert False, f'Invalid context: {context}'

def _list_file_paths(context):
    match context:
        case AssetContext(department_name, category_name, asset_name, _):
            return list_asset_hip_file_paths(category_name, asset_name, department_name)
        case ShotContext(department_name, sequence_name, shot_name, _):
            return list_shot_hip_file_paths(sequence_name, shot_name, department_name)
        case KitContext(department_name, category_name, kit_name, _):
            return list_kit_hip_file_paths(category_name, kit_name, department_name)
    assert False, f'Invalid context: {context}'

def _latest_export_path(context):
    match context:
        case AssetContext(department_name, category_name, asset_name, _):
            return latest_asset_export_path(category_name, asset_name, department_name)
        case ShotContext(department_name, sequence_name, shot_name, _):
            return latest_shot_export_path(sequence_name, shot_name, department_name)
        case KitContext(department_name, category_name, kit_name, _):
            return latest_kit_export_path(category_name, kit_name, department_name)
    assert False, f'Invalid context: {context}'

def _section_from_context(context):
    match context:
        case None: return None
        case AssetContext(_, _, _, _): return Section.Asset
        case ShotContext(_, _, _, _): return Section.Shot
        case KitContext(_, _, _, _): return Section.Kit
    assert False, f'Invalid context: {context}'

def _entity_from_path(path):
    match path:
        case None: return None
        case ['Assets', category_name, asset_name, *_]:
            return AssetEntity(category_name, asset_name)
        case ['Shots', sequence_name, shot_name, *_]:
            return ShotEntity(sequence_name, shot_name)
        case ['Kits', category_name, kit_name, *_]:
            return KitEntity(category_name, kit_name)
    assert False, f'Invalid path: {path}'

def _entity_from_context(context):
    match context:
        case None: return None
        case AssetContext(_, category_name, asset_name, _):
            return AssetEntity(category_name, asset_name)
        case ShotContext(_, sequence_name, shot_name, _):
            return ShotEntity(sequence_name, shot_name)
        case KitContext(_, category_name, kit_name, _):
            return KitEntity(category_name, kit_name)
    assert False, f'Invalid context: {context}'

def _path_from_context(context):
    match context:
        case None: return None
        case AssetContext(_, category_name, asset_name, _):
            return ['Assets', category_name, asset_name]
        case ShotContext(_, sequence_name, shot_name, _):
            return ['Shots', sequence_name, shot_name]
        case KitContext(_, category_name, kit_name, _):
            return ['Kits', category_name, kit_name]
    assert False, f'Invalid context: {context}'

def _path_from_entity(entity):
    match entity:
        case None: return None
        case AssetEntity(category_name, asset_name):
            return ['Assets', category_name, asset_name]
        case ShotEntity(sequence_name, shot_name):
            return ['Shots', sequence_name, shot_name]
        case KitEntity(category_name, kit_name):
            return ['Kits', category_name, kit_name]
    assert False, f'Invalid entity: {entity}'

def _file_path_from_context(context):
    match context:
        case None: return None
        case AssetContext(department_name, category_name, asset_name, version_name):
            file_path = get_asset_hip_file_path(category_name, asset_name, department_name, version_name)
        case ShotContext(department_name, sequence_name, shot_name, version_name):
            file_path = get_shot_hip_file_path(sequence_name, shot_name, department_name, version_name)
        case KitContext(department_name, category_name, kit_name, version_name):
            file_path = get_kit_hip_file_path(category_name, kit_name, department_name, version_name)
        case _:
            assert False, f'Invalid context: {context}'
    if not file_path.exists(): return None
    return file_path

def _get_timestamp_from_context(context):
    file_path = _file_path_from_context(context)
    if file_path is None: return None
    return dt.datetime.fromtimestamp(file_path.stat().st_mtime)

def _save_context(target_path, prev_context, next_context):
    def _get_version_name(context):
        if context is None: return 'v0000'
        if context.version_name is None: return 'v0000'
        return context.version_name
    timestamp = _get_timestamp_from_context(next_context)
    prev_version_name = _get_version_name(prev_context)
    next_version_name = _get_version_name(next_context)
    context_path = target_path / '_context' / f'{next_version_name}.json'
    store_json(context_path, dict(
        user = get_user_name(),
        timestamp = '' if timestamp is None else timestamp.isoformat(),
        from_version = prev_version_name,
        to_version = next_version_name,
        houdini_version = hou.applicationVersionString()
    ))

def _create_workspace_model():

    # Create the model
    model = QStandardItemModel()

    # Populate assets
    assets_item = QStandardItem('Assets')
    assets_item.setSelectable(False)
    assets_item.setEditable(False)
    for category_name in api.config.list_category_names():
        category_item = QStandardItem(category_name)
        category_item.setSelectable(False)
        category_item.setEditable(False)
        for asset_name in api.config.list_asset_names(category_name):
            asset_item = QStandardItem(asset_name)
            asset_item.setEditable(False)
            category_item.appendRow(asset_item)
        assets_item.appendRow(category_item)
    model.appendRow(assets_item)

    # Populate shots
    shots_item = QStandardItem('Shots')
    shots_item.setSelectable(False)
    shots_item.setEditable(False)
    for sequence_name in api.config.list_sequence_names():
        sequence_item = QStandardItem(sequence_name)
        sequence_item.setSelectable(False)
        sequence_item.setEditable(False)
        for shot_name in api.config.list_shot_names(sequence_name):
            shot_item = QStandardItem(shot_name)
            shot_item.setEditable(False)
            sequence_item.appendRow(shot_item)
        shots_item.appendRow(sequence_item)
    model.appendRow(shots_item)

    # Populate kits
    kits_item = QStandardItem('Kits')
    kits_item.setSelectable(False)
    kits_item.setEditable(False)
    for category_name in api.config.list_kit_category_names():
        category_item = QStandardItem(category_name)
        category_item.setSelectable(False)
        category_item.setEditable(False)
        for kit_name in api.config.list_kit_names(category_name):
            kit_item = QStandardItem(kit_name)
            kit_item.setEditable(False)
            category_item.appendRow(kit_item)
        kits_item.appendRow(category_item)
    model.appendRow(kits_item)

    # Done
    return model

class ButtonSurface(QtWidgets.QWidget):
    def __init__(self, parent = None):
        super().__init__(parent)
    
    def payload(self):
        raise NotImplementedError()
    
    def overwrite(self, payload):
        raise NotImplementedError()

class State:
    Hover = 'hover'
    Pressed = 'pressed'
    Selected = 'selected'
    Overwritten = 'overwritten'

class ButtonHost(QtWidgets.QWidget):
    clicked = Signal(object)
    state_changed = Signal(str, object, object)

    def __init__(self, surface, parent = None):
        super().__init__(parent)

        # Check if the surface is a button surface
        if not isinstance(surface, ButtonSurface):
            raise ValueError('Surface must be a ButtonSurface')

        # Members
        self._surface = surface

        # Set the layout
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        # Add the surface
        layout.addWidget(surface)

        # Add the mouse press event
        self.setMouseTracking(True)
        self.setProperty(State.Hover, False)
        self.setProperty(State.Pressed, False)
        self.setProperty(State.Selected, False)

        # Set hover highlighting
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            'ButtonHost[hover=true][pressed=false] {'
            '   background-color: #474747;'
            '}'
            'ButtonHost[selected=true][hover=false] {'
            '   background-color: #5e4a8a;'
            '}'
            'ButtonHost[selected=true][overwritten=true][hover=false] {'
            '   background-color: #b01c3c;'
            '}'
            'ButtonHost[selected=true][hover=true] {'
            '   background-color: #58482a;'
            '}'
        )
    
    def surface(self):
        return self._surface
    
    def set_state(self, state, next_value):
        prev_value = self.get_state(state)
        self.setProperty(state, next_value)
        self.setStyle(self.style())
        self.state_changed.emit(state, prev_value, next_value)
    
    def get_state(self, state):
        return self.property(state)

    def set_states(self, **states):
        for state, value in states.items():
            self.setProperty(state, value)
        self.setStyle(self.style())
    
    def get_states(self, *states):
        return {state: self.property(state) for state in states}
    
    def mousePressEvent(self, event):
        if event.type() != QEvent.Type.MouseButtonPress:
            return super().mousePressEvent(event)
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        self.set_state(State.Pressed, True)
        return super().mousePressEvent(event)
    
    def mouseReleaseEvent(self, event):
        if event.type() != QEvent.Type.MouseButtonRelease:
            return super().mouseReleaseEvent(event)
        if not self.get_state(State.Pressed):
            return super().mouseReleaseEvent(event)
        self.clicked.emit(self._surface.payload())
        self.set_state(State.Pressed, False)
        return super().mouseReleaseEvent(event)
    
    def enterEvent(self, event):
        self.set_state(State.Hover, True)
        return super().enterEvent(event)

    def leaveEvent(self, event):
        self.set_state(State.Hover, False)
        return super().leaveEvent(event)
    
    def deleteLater(self):
        self._surface.deleteLater()
        return super().deleteLater()

    def overwrite(self, payload):
        overwritten = self._surface.overwrite(payload)
        self.set_state(State.Overwritten, overwritten)
        return overwritten

class WorkspaceBrowser(QtWidgets.QWidget):
    selection_changed = Signal(object)
    open_location = Signal(object)
    create_entry = Signal(object)
    remove_entry = Signal(object)

    def __init__(self, parent = None):
        super().__init__(parent)

        # Members
        self._selection = None

        # Settings
        self.setMinimumHeight(0)

        # Set the layout
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setLayout(layout)

        # Create the tree view navigation
        self.tree_view = QtWidgets.QTreeView()
        self.tree_view.setHeaderHidden(True)
        self.tree_view.setMinimumHeight(0)
        layout.addWidget(self.tree_view)

        # Emit clicked signal
        self.tree_view.clicked.connect(self._left_clicked)
        self.tree_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self._right_clicked)

        # Initial update
        self.refresh()
    
    def _index_path(self, name_path):
        if name_path is None: return None
        model = self.tree_view.model()
        names = [model.item(index).text() for index in range(model.rowCount())]
        index = names.index(name_path[0])
        item = model.item(index)
        index_path = [index]
        for name in name_path[1:]:
            names = [item.child(index).text() for index in range(item.rowCount())]
            index = names.index(name)
            index_path.append(index)
            item = item.child(index)
        return index_path
    
    def _name_path(self, index_path):
        if index_path is None: return None
        model = self.tree_view.model()
        item = model.item(index_path[0])
        name_path = [item.text()]
        for index in index_path[1:]:
            item = item.child(index)
            name_path.append(item.text())
        return name_path
    
    def select(self, selection):

        cleared_brush = QBrush(Qt.NoBrush)
        parent_brush = QBrush('#5e4a8a', Qt.Dense6Pattern)
        child_brush = QBrush('#5e4a8a', Qt.Dense4Pattern)

        def _set_style(index_path, parent_brush, child_brush):
            model = self.tree_view.model()
            item = model.item(index_path[0])
            item.setBackground(parent_brush)
            for row_index in index_path[1:-1]:
                item = item.child(row_index)
                item.setBackground(parent_brush)
            item = item.child(index_path[-1])
            item.setBackground(child_brush)

        # Check if the index path is the same
        selection_path = _path_from_entity(selection)
        selection_index = self._index_path(selection_path)
        if selection_index == self._selection: return

        # Clear the color of the current path
        if self._selection is not None:
            _set_style(self._selection, cleared_brush, cleared_brush)

        # Set the color of the new path
        if selection_index is not None:
            _set_style(selection_index, parent_brush, child_brush)

        # Set the current path
        self._selection = selection_index
    
    def get_selection(self):
        if self._selection is None: return None
        name_path = self._name_path(self._selection)
        return _entity_from_path(name_path)
    
    def _get_path(self, item):
        name_path = []
        while item is not None:
            name_path.insert(0, item.text())
            item = item.parent()
        return name_path

    def _selection_changed(self, item_selection):

        # Get the selected item
        indices = item_selection.indexes()
        if len(indices) == 1:
            index = indices[0]
            model = self.tree_view.model()
            item = model.itemFromIndex(index)
            name_path = self._get_path(item)
        else:
            name_path = None

        # Emit the selection changed signal
        self.selection_changed.emit(name_path)
    
    def _left_clicked(self, index):
        model = self.tree_view.model()
        item = model.itemFromIndex(index)
        if item.isSelectable(): return
        if self.tree_view.isExpanded(index):
            self.tree_view.collapse(index)
        else:
            self.tree_view.expand(index)
    
    def _right_clicked(self, point):

        # Get item at point
        index = self.tree_view.indexAt(point)
        if not index.isValid(): return
        model = self.tree_view.model()
        item = model.itemFromIndex(index)
        name_path = self._get_path(item)

        # Build the menu
        menu = QtWidgets.QMenu()
        open_location_action = menu.addAction('Open Location')
        create_entry_action = None
        remove_entry_action = None
        match name_path:
            case ['Assets']:
                create_entry_action = menu.addAction('Create Category')
            case ['Assets', _]:
                remove_entry_action = menu.addAction('Remove Category')
                create_entry_action = menu.addAction('Create Asset')
            case ['Assets', _, _]:
                remove_entry_action = menu.addAction('Remove Asset')
            case ['Shots']:
                create_entry_action = menu.addAction('Create Sequence')
            case ['Shots', _]:
                remove_entry_action = menu.addAction('Remove Sequence')
                create_entry_action = menu.addAction('Create Shot')
            case ['Shots', _, _]:
                remove_entry_action = menu.addAction('Remove Shot')
            case ['Kits']:
                create_entry_action = menu.addAction('Create Category')
            case ['Kits', _]:
                remove_entry_action = menu.addAction('Remove Category')
                create_entry_action = menu.addAction('Create Kit')
            case ['Kits', _, _]:
                remove_entry_action = menu.addAction('Remove Kit')
            case _: pass

        # Execute the menu
        selected_action = menu.exec_(self.tree_view.mapToGlobal(point))
        if selected_action is None: return
        if selected_action == open_location_action:
            return self.open_location.emit(name_path)
        if selected_action == create_entry_action:
            return self.create_entry.emit(name_path)
        if selected_action == remove_entry_action:
            return self.remove_entry.emit(name_path)
    
    def _get_tree_state(self):

        # Recursive visit function
        def _visit(item):
            name = item.text()
            expanded = self.tree_view.isExpanded(item.index())
            children = dict(
                _visit(item.child(row_index))
                for row_index in range(item.rowCount())
            )
            return name, dict(
                expanded = expanded,
                children = children
            )

        # Get the tree state
        model = self.tree_view.model()
        if model is None: return dict()
        return dict(
            _visit(model.item(row_index))
            for row_index in range(model.rowCount())
        )
    
    def _set_tree_state(self, state):

        # Recursive visit function
        def _visit(item, data):
            self.tree_view.setExpanded(item.index(), data['expanded'])
            for row_index in range(item.rowCount()):
                child_item = item.child(row_index)
                child_name = child_item.text()
                child_data = data['children'].get(child_name)
                if child_data is None: continue
                _visit(child_item, child_data)

        # Set the tree state
        model = self.tree_view.model()
        for row_index in range(model.rowCount()):
            item = model.item(row_index)
            item_name = item.text()
            item_data = state.get(item_name)
            if item_data is None: continue
            _visit(item, item_data)
    
    def refresh(self):

        # Store the current selection
        selection = self.get_selection()
        state = self._get_tree_state()

        # Destroy any current model
        model = self.tree_view.model()
        if model is not None:
            selection_model = self.tree_view.selectionModel()
            selection_model.selectionChanged.disconnect(self._selection_changed)
            model.deleteLater()
        
        # Create the model
        model = _create_workspace_model()

        # Set the model
        self.tree_view.setModel(model)
        self.tree_view.setUniformRowHeights(True)

        # Set the selection
        self._set_tree_state(state)
        self.select(selection)

        # Connect the item changed signal
        selection_model = self.tree_view.selectionModel()
        selection_model.selectionChanged.connect(self._selection_changed)

class DepartmentButtonSurface(ButtonSurface):
    def __init__(self, context, parent = None):
        super().__init__(parent)
        assert isinstance(context, Context), f'Invalid context: {context}'

        # Members
        self._context = context
        self._overwrite_context = None

        # Create the main layout
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setLayout(layout)

        # Create the content widget
        self._content = QtWidgets.QWidget()
        self._content.setStyleSheet(
            '.QWidget {'
            '   border: 1px solid black;'
            '}'
            'QLabel[timestamp=true] {'
            '   color: #919191;'
            '}'
            'QLabel[text=v0000] {'
            '   color: #616161;'
            '}'
            'QLabel[department=true] {'
            '   font-weight: bold;'
            '}'
        )
        layout.addWidget(self._content)

        # Create the content layout
        self._layout = QtWidgets.QHBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._content.setLayout(self._layout)

        # Set style
        self.setStyleSheet('padding: 5px;')

        # Update the content layout
        self.refresh()

    def payload(self):
        return self._context
    
    def overwrite(self, context):
        if context is None:
            self._overwrite_context = None
        else:
            current_version_name = self._context.version_name
            overwrite_version_name = context.version_name
            version_equal = current_version_name == overwrite_version_name
            self._overwrite_context = None if version_equal else context
        self.refresh()
        return self._overwrite_context is not None

    def refresh(self):

        # Clear the layout
        for index in reversed(range(self._layout.count())):
            item = self._layout.itemAt(index)
            if not item.isEmpty():
                widget = item.widget()
                widget.deleteLater()
            self._layout.removeItem(item)

        # Get the context to display
        overwritten = self._overwrite_context is not None
        context = self._context if not overwritten else self._overwrite_context
        
        # Parameters
        department_name = context.department_name
        version_name = (
            'v0000' if context.version_name is None else
            context.version_name
        )
        timestamp = _get_timestamp_from_context(context)
        date = '' if timestamp is None else timestamp.strftime('%d-%m-%Y')
        time = '' if timestamp is None else timestamp.strftime('%H:%M')

        # Create the version label
        version_label = QtWidgets.QLabel(version_name)
        version_label.setAlignment(Qt.AlignRight)
        self._layout.addWidget(version_label)

        # Create a dummy date label
        dummy_date_label = QtWidgets.QLabel(date)
        dummy_date_label.setStyleSheet('color: transparent;')
        self._layout.addWidget(dummy_date_label)

        # Create a dummer time label
        dummy_time_label = QtWidgets.QLabel(time)
        dummy_time_label.setStyleSheet('color: transparent;')
        self._layout.addWidget(dummy_time_label)

        # Create the department label
        department_label = QtWidgets.QLabel(department_name)
        department_label.setAlignment(Qt.AlignCenter)
        department_label.setProperty('department', True)
        self._layout.addWidget(department_label, 1)

        # Create a dummy version label
        dummy_version_label = QtWidgets.QLabel(version_name)
        dummy_version_label.setStyleSheet('color: transparent;')
        self._layout.addWidget(dummy_version_label)

        # Create the date label
        date_label = QtWidgets.QLabel(date)
        date_label.setAlignment(Qt.AlignRight)
        date_label.setProperty('timestamp', True)
        self._layout.addWidget(date_label)

        # Create the time label
        time_label = QtWidgets.QLabel(time)
        time_label.setAlignment(Qt.AlignRight)
        time_label.setProperty('timestamp', True)
        self._layout.addWidget(time_label)

        # Force layout refresh
        self._content.setLayout(self._layout)

class DepartmentBrowser(QtWidgets.QWidget):
    setting_changed = Signal(object)
    selection_changed = Signal(object)
    open_location = Signal(object)
    reload_scene = Signal(object)
    new_from_current = Signal(object)
    new_from_template = Signal(object)

    def __init__(self, parent = None):
        super().__init__(parent)

        # Members
        self._entity = None
        self._selection = None
        self._buttons = dict()

        # Settings
        self.setMinimumHeight(0)

        # Create the outer layout
        outer_layout = QtWidgets.QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        self.setLayout(outer_layout)

        # Create outer scroll area
        layout = QtWidgets.QVBoxLayout()
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_widget = QtWidgets.QWidget()
        scroll_widget.setLayout(layout)
        scroll_area.setWidget(scroll_widget)
        outer_layout.addWidget(scroll_area)

        # Create the list view navigation
        self._view = QtWidgets.QFrame()
        layout.addWidget(self._view)

        # Create the list layout
        self._layout = QtWidgets.QVBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # Add stretch
        layout.addStretch()

        # Create the settings layout
        settings_widget = QtWidgets.QWidget()
        settings_inner_widget = QtWidgets.QWidget()
        settings_inner_widget.setObjectName('settings_inner_widget')
        settings_inner_widget.setStyleSheet(
            'QWidget#settings_inner_widget {'
            '   border-radius: 5px;'
            '   border: 1px solid black;'
            '   background-color: rgba(0, 0, 0, 0.1);'
            '}'
        )
        outer_settings_layout = QtWidgets.QHBoxLayout()
        outer_settings_layout.setContentsMargins(0, 0, 0, 0)
        outer_settings_layout.setSpacing(0)
        settings_layout = QtWidgets.QGridLayout()
        settings_layout.setContentsMargins(20, 5, 20, 5)
        settings_layout.setVerticalSpacing(5)
        settings_layout.setHorizontalSpacing(10)
        settings_layout.setColumnStretch(0, 1)
        settings_layout.setColumnStretch(4, 1)
        settings_widget.setLayout(outer_settings_layout)
        outer_settings_layout.addStretch()
        outer_settings_layout.addWidget(settings_inner_widget)
        outer_settings_layout.addStretch()
        settings_inner_widget.setLayout(settings_layout)
        layout.addWidget(settings_widget)

        # Create the auto action checkboxes
        auto_save_asset_checkbox = QtWidgets.QCheckBox()
        auto_refresh_asset_checkbox = QtWidgets.QCheckBox()
        auto_publish_asset_checkbox = QtWidgets.QCheckBox()
        auto_save_shot_checkbox = QtWidgets.QCheckBox()
        auto_refresh_shot_checkbox = QtWidgets.QCheckBox()
        auto_publish_shot_checkbox = QtWidgets.QCheckBox()
        auto_save_kit_checkbox = QtWidgets.QCheckBox()
        auto_refresh_kit_checkbox = QtWidgets.QCheckBox()
        auto_publish_kit_checkbox = QtWidgets.QCheckBox()
        auto_save_asset_checkbox.setChecked(
            AUTO_SETTINGS_DEFAULT[Section.Asset][Action.Save]
        )
        auto_refresh_asset_checkbox.setChecked(
            AUTO_SETTINGS_DEFAULT[Section.Asset][Action.Refresh]
        )
        auto_publish_asset_checkbox.setChecked(
            AUTO_SETTINGS_DEFAULT[Section.Asset][Action.Publish]
        )
        auto_save_shot_checkbox.setChecked(
            AUTO_SETTINGS_DEFAULT[Section.Shot][Action.Save]
        )
        auto_refresh_shot_checkbox.setChecked(
            AUTO_SETTINGS_DEFAULT[Section.Shot][Action.Refresh]
        )
        auto_publish_shot_checkbox.setChecked(
            AUTO_SETTINGS_DEFAULT[Section.Shot][Action.Publish]
        )
        auto_save_kit_checkbox.setChecked(
            AUTO_SETTINGS_DEFAULT[Section.Kit][Action.Save]
        )
        auto_refresh_kit_checkbox.setChecked(
            AUTO_SETTINGS_DEFAULT[Section.Kit][Action.Refresh]
        )
        auto_publish_kit_checkbox.setChecked(
            AUTO_SETTINGS_DEFAULT[Section.Kit][Action.Publish]
        )
        auto_save_asset_checkbox.stateChanged.connect(
            lambda: self.setting_changed.emit(
                (Section.Asset, Action.Save, auto_save_asset_checkbox.isChecked())
            )
        )
        auto_refresh_asset_checkbox.stateChanged.connect(
            lambda: self.setting_changed.emit(
                (Section.Asset, Action.Refresh, auto_refresh_asset_checkbox.isChecked())
            )
        )
        auto_publish_asset_checkbox.stateChanged.connect(
            lambda: self.setting_changed.emit(
                (Section.Asset, Action.Publish, auto_publish_asset_checkbox.isChecked())
            )
        )
        auto_save_shot_checkbox.stateChanged.connect(
            lambda: self.setting_changed.emit(
                (Section.Shot, Action.Save, auto_save_shot_checkbox.isChecked())
            )
        )
        auto_refresh_shot_checkbox.stateChanged.connect(
            lambda: self.setting_changed.emit(
                (Section.Shot, Action.Refresh, auto_refresh_shot_checkbox.isChecked())
            )
        )
        auto_publish_shot_checkbox.stateChanged.connect(
            lambda: self.setting_changed.emit(
                (Section.Shot, Action.Publish, auto_publish_shot_checkbox.isChecked())
            )
        )
        auto_save_kit_checkbox.stateChanged.connect(
            lambda: self.setting_changed.emit(
                (Section.Kit, Action.Save, auto_save_kit_checkbox.isChecked())
            )
        )
        auto_refresh_kit_checkbox.stateChanged.connect(
            lambda: self.setting_changed.emit(
                (Section.Kit, Action.Refresh, auto_refresh_kit_checkbox.isChecked())
            )
        )
        auto_publish_kit_checkbox.stateChanged.connect(
            lambda: self.setting_changed.emit(
                (Section.Kit, Action.Publish, auto_publish_kit_checkbox.isChecked())
            )
        )
        settings_layout.addWidget(QtWidgets.QLabel('Auto'), 0, 1)
        settings_layout.addWidget(QtWidgets.QLabel('Save'), 0, 2)
        settings_layout.addWidget(QtWidgets.QLabel('Refresh'), 0, 3)
        settings_layout.addWidget(QtWidgets.QLabel('Publish'), 0, 4)
        settings_layout.addWidget(QtWidgets.QLabel('Asset'), 1, 1)
        settings_layout.addWidget(auto_save_asset_checkbox, 1, 2)
        settings_layout.addWidget(auto_refresh_asset_checkbox, 1, 3)
        settings_layout.addWidget(auto_publish_asset_checkbox, 1, 4)
        settings_layout.addWidget(QtWidgets.QLabel('Shot'), 2, 1)
        settings_layout.addWidget(auto_save_shot_checkbox, 2, 2)
        settings_layout.addWidget(auto_refresh_shot_checkbox, 2, 3)
        settings_layout.addWidget(auto_publish_shot_checkbox, 2, 4)
        settings_layout.addWidget(QtWidgets.QLabel('Kit'), 3, 1)
        settings_layout.addWidget(auto_save_kit_checkbox, 3, 2)
        settings_layout.addWidget(auto_refresh_kit_checkbox, 3, 3)
        settings_layout.addWidget(auto_publish_kit_checkbox, 3, 4)
        settings_layout.setAlignment(auto_save_asset_checkbox, Qt.AlignCenter)
        settings_layout.setAlignment(auto_refresh_asset_checkbox, Qt.AlignCenter)
        settings_layout.setAlignment(auto_publish_asset_checkbox, Qt.AlignCenter)
        settings_layout.setAlignment(auto_save_shot_checkbox, Qt.AlignCenter)
        settings_layout.setAlignment(auto_refresh_shot_checkbox, Qt.AlignCenter)
        settings_layout.setAlignment(auto_publish_shot_checkbox, Qt.AlignCenter)
        settings_layout.setAlignment(auto_save_kit_checkbox, Qt.AlignCenter)
        settings_layout.setAlignment(auto_refresh_kit_checkbox, Qt.AlignCenter)
        settings_layout.setAlignment(auto_publish_kit_checkbox, Qt.AlignCenter)

        # Initial update
        self.refresh()
    
    def set_entity(self, entity):
        self._entity = entity
        self._selection = None
        self.refresh()
    
    def select(self, selection):

        # Get the clicked button
        button = self._buttons.get(selection)
        if button is None: return

        # Clear color on current button
        curr_button = self._buttons.get(self._selection)
        if curr_button is not None:
            curr_button.set_state(State.Selected, False)
            curr_button.overwrite(None)

        # Set the color of the clicked button
        button.set_state(State.Selected, True)

        # Set the selection
        self._selection = selection

    def _left_clicked(self, context):

        # Check if the clicked button is already selected
        if self._selection == context.department_name: return

        # Set the selection
        self.select(context.department_name)

        # Emit the selection changed signal
        self.selection_changed.emit(context)
    
    def _right_clicked(self, point):
        
        # Get the clicked button
        button = self.sender()
        if button is None: return

        # Get the context
        context = button.surface().payload()

        # Build and display the menu
        menu = QtWidgets.QMenu()
        open_location_action = menu.addAction('Open Location')
        reload_scene_action = menu.addAction('Reload Scene')
        new_from_current_action = menu.addAction('New: Current')
        new_from_template_action = menu.addAction('New: Template')
        selected_action = menu.exec_(button.mapToGlobal(point))
        if selected_action is None: return
        if selected_action == open_location_action:
            return self.open_location.emit(context)
        if selected_action == reload_scene_action:
            return self.reload_scene.emit(context)
        if selected_action == new_from_current_action:
            return self.new_from_current.emit(context)
        if selected_action == new_from_template_action:
            return self.new_from_template.emit(context)
        
        # Invalid action
        assert False, f'Invalid action: {selected_action}'
    
    def overwrite(self, context):
        if self._selection is None: return
        button = self._buttons.get(self._selection)
        if button is None: return
        button.overwrite(context)
    
    def refresh(self):

        # Clear the layout
        self._buttons.clear()
        for index in reversed(range(self._layout.count())):
            item = self._layout.itemAt(index)
            if not item.isEmpty():
                widget = item.widget()
                widget.deleteLater()
            self._layout.removeItem(item)

        # Check if the entity is valid
        if self._entity is None: return

        def _latest_department_contexts():
            match self._entity:
                case AssetEntity(category_name, asset_name):
                    department_names = api.config.list_asset_department_names()
                    return [
                        _latest_asset_context(category_name, asset_name, department_name)
                        for department_name in department_names
                    ]
                case ShotEntity(sequence_name, shot_name):
                    department_names = api.config.list_shot_department_names()
                    return [
                        _latest_shot_context(sequence_name, shot_name, department_name)
                        for department_name in department_names
                    ]
                case KitEntity(category_name, kit_name):
                    department_names = api.config.list_kit_department_names()
                    return [
                        _latest_kit_context(category_name, kit_name, department_name)
                        for department_name in department_names
                    ]
                case None:
                    return []

        # Create the new department buttons
        for context in _latest_department_contexts():
            department_button = ButtonHost(DepartmentButtonSurface(context))
            self._layout.addWidget(department_button)
            self._buttons[context.department_name] = department_button
        
        # Connect the department buttons
        for department_button in self._buttons.values():
            department_button.clicked.connect(self._left_clicked)
            department_button.setContextMenuPolicy(Qt.CustomContextMenu)
            department_button.customContextMenuRequested.connect(self._right_clicked)
        
        # Add a stretch
        self._layout.addStretch()

        # refresh the layout
        self._view.setLayout(self._layout)

        # Set the selection
        self.select(self._selection)

class DetailsView(QtWidgets.QWidget):
    save_scene = Signal()
    refresh_scene = Signal()
    publish_scene = Signal()
    open_scene_info = Signal()
    open_location = Signal(object)
    set_frame_range = Signal(object)

    def __init__(self, parent = None):
        super().__init__(parent)

        # Members
        self._context = None

        # Settings
        self.setMinimumHeight(0)

        # Create the outer layout
        outer_layout = QtWidgets.QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(10)
        self.setLayout(outer_layout)

        # Create the scroll area
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        scroll_widget.setLayout(layout)
        scroll_area.setWidget(scroll_widget)
        outer_layout.addWidget(scroll_area)

        # Create the top button layout
        top_button_layout = QtWidgets.QGridLayout()
        top_button_layout.setColumnStretch(0, 1)
        top_button_layout.setSpacing(0)
        layout.addLayout(top_button_layout)

        # Create the save scene button
        self.save_scene_button = QtWidgets.QPushButton('Save')
        self.save_scene_button.setIcon(hqt.Icon('DESKTOP_hip'))
        self.save_scene_button.clicked.connect(self._save)
        top_button_layout.addWidget(self.save_scene_button, 0, 0)

        # Create the open workspace location button
        self.open_workspace_location_button = QtWidgets.QPushButton()
        self.open_workspace_location_button.setIcon(hqt.Icon('BUTTONS_folder'))
        self.open_workspace_location_button.clicked.connect(
            partial(self._open_location, Location.Workspace)
        )
        top_button_layout.addWidget(self.open_workspace_location_button, 0, 1)

        # Create the refresh scene button
        self.refresh_scene_button = QtWidgets.QPushButton('Refresh')
        self.refresh_scene_button.setIcon(hqt.Icon('NETVIEW_reload_needsupdate'))
        self.refresh_scene_button.clicked.connect(self._refresh)
        top_button_layout.addWidget(self.refresh_scene_button, 1, 0)

        # Create the scene info button
        self.scene_info_button = QtWidgets.QPushButton()
        self.scene_info_button.setIcon(hqt.Icon('BUTTONS_list_info'))
        self.scene_info_button.clicked.connect(self._scene_info)
        top_button_layout.addWidget(self.scene_info_button, 1, 1)

        # Create the publish scene button
        self.publish_scene_button = QtWidgets.QPushButton('Publish')
        self.publish_scene_button.setIcon(hqt.Icon('NETVIEW_export_flag'))
        self.publish_scene_button.clicked.connect(self._publish)
        top_button_layout.addWidget(self.publish_scene_button, 2, 0)

        # Create the open export location button
        self.open_export_location_button = QtWidgets.QPushButton()
        self.open_export_location_button.setIcon(hqt.Icon('BUTTONS_folder'))
        self.open_export_location_button.clicked.connect(
            partial(self._open_location, Location.Export)
        )
        top_button_layout.addWidget(self.open_export_location_button, 2, 1)

        # Create the open texture location button
        self.open_texture_location_button = QtWidgets.QPushButton()
        self.open_texture_location_button.setIcon(hqt.Icon('BUTTONS_folder'))
        self.open_texture_location_button.clicked.connect(
            partial(self._open_location, Location.Texture)
        )
        top_button_layout.addWidget(
            QtWidgets.QLabel("Open texture location: "), 3, 0,
            alignment = Qt.AlignRight
        )
        top_button_layout.addWidget(self.open_texture_location_button, 3, 1)

        # Create the details grid layout
        details_layout = QtWidgets.QVBoxLayout()
        layout.addLayout(details_layout)

        # Sections layout
        sections_layout = QtWidgets.QHBoxLayout()
        sections_layout.setContentsMargins(5, 5, 5, 5)
        sections_layout.setSpacing(5)
        details_layout.addLayout(sections_layout)

        # Create the workspace section
        self.workspace_section = QtWidgets.QFrame()
        self.workspace_section.setObjectName('workspace_section')
        self.workspace_section.setStyleSheet(
            'QFrame#workspace_section {'
            '   border: 1px solid black;'
            '   border-radius: 5px;'
            '   background-color: rgba(0, 0, 0, 0.1);'
            '}'
        )
        workspace_section_layout = QtWidgets.QVBoxLayout()
        workspace_section_layout.setContentsMargins(5, 5, 5, 5)
        workspace_section_layout.setSpacing(10)
        self.workspace_section.setLayout(workspace_section_layout)
        sections_layout.addWidget(self.workspace_section)

        # Create the workspace section headline
        workspace_section_headline = QtWidgets.QLabel('Current Workspace:')
        workspace_section_headline.setAlignment(Qt.AlignCenter)
        workspace_section_layout.addWidget(workspace_section_headline)

        # Create the workspace section details layout
        self.workspace_details_layout = QtWidgets.QGridLayout()
        self.workspace_details_layout.setColumnStretch(1, 1)
        self.workspace_details_layout.setSpacing(5)
        workspace_section_layout.addLayout(self.workspace_details_layout)

        # Create the workspace section details version entry
        self.workspace_version_label = QtWidgets.QLabel()
        self.workspace_details_layout.addWidget(QtWidgets.QLabel('Version:'), 0, 0)
        self.workspace_details_layout.addWidget(self.workspace_version_label, 0, 1)

        # Create the workspace section details timestamp entry
        self.workspace_timestamp_label = QtWidgets.QLabel()
        self.workspace_details_layout.addWidget(QtWidgets.QLabel('Time:'), 1, 0)
        self.workspace_details_layout.addWidget(self.workspace_timestamp_label, 1, 1)

        # Create the workspace section details user entry
        self.workspace_user_label = QtWidgets.QLabel()
        self.workspace_details_layout.addWidget(QtWidgets.QLabel('User:'), 2, 0)
        self.workspace_details_layout.addWidget(self.workspace_user_label, 2, 1)

        # Create the export section
        self.export_section = QtWidgets.QFrame()
        self.export_section.setObjectName('export_section')
        self.export_section.setStyleSheet(
            'QFrame#export_section {'
            '   border: 1px solid black;'
            '   border-radius: 5px;'
            '   background-color: rgba(0, 0, 0, 0.1);'
            '}'
        )
        export_section_layout = QtWidgets.QVBoxLayout()
        export_section_layout.setContentsMargins(5, 5, 5, 5)
        export_section_layout.setSpacing(10)
        self.export_section.setLayout(export_section_layout)
        sections_layout.addWidget(self.export_section)

        # Create the export section headline
        export_section_headline = QtWidgets.QLabel('Latest Export:')
        export_section_headline.setAlignment(Qt.AlignCenter)
        export_section_layout.addWidget(export_section_headline)

        # Create the export section details layout
        self.export_details_layout = QtWidgets.QGridLayout()
        self.export_details_layout.setColumnStretch(1, 1)
        self.export_details_layout.setSpacing(5)
        export_section_layout.addLayout(self.export_details_layout)

        # Create the export section details version entry
        self.export_version_label = QtWidgets.QLabel()
        self.export_details_layout.addWidget(QtWidgets.QLabel('Version:'), 0, 0)
        self.export_details_layout.addWidget(self.export_version_label, 0, 1)

        # Create the export section details timestamp entry
        self.export_timestamp_label = QtWidgets.QLabel()
        self.export_details_layout.addWidget(QtWidgets.QLabel('Time:'), 1, 0)
        self.export_details_layout.addWidget(self.export_timestamp_label, 1, 1)

        # Create the export section details user entry
        self.export_user_label = QtWidgets.QLabel()
        self.export_details_layout.addWidget(QtWidgets.QLabel('User:'), 2, 0)
        self.export_details_layout.addWidget(self.export_user_label, 2, 1)

        # Create a spacer
        layout.addStretch()

        # Create the frame range layout
        frame_range_section = QtWidgets.QFrame()
        frame_range_section.setObjectName('frame_range_section')
        frame_range_section.setStyleSheet(
            'QFrame#frame_range_section {'
            '   border: 1px solid black;'
            '   border-radius: 5px;'
            '   background-color: rgba(0, 0, 0, 0.1);'
            '}'
        )
        self.frame_range_layout = QtWidgets.QHBoxLayout()
        self.frame_range_layout.setContentsMargins(5, 5, 5, 5)
        self.frame_range_layout.setSpacing(5)
        frame_range_section.setLayout(self.frame_range_layout)
        outer_frame_range_layout = QtWidgets.QHBoxLayout()
        outer_frame_range_layout.setContentsMargins(5, 5, 5, 5)
        outer_frame_range_layout.setSpacing(5)
        outer_frame_range_layout.addWidget(frame_range_section)
        layout.addLayout(outer_frame_range_layout)

        # Create the frame range label
        self.frame_range_label = QtWidgets.QLabel('Frame Range:')
        self.frame_range_layout.addWidget(self.frame_range_label)

        # Add a spacer
        self.frame_range_layout.addStretch()

        # Create the padded frame range button
        self.padded_frame_range_button = QtWidgets.QPushButton('Padded')
        self.padded_frame_range_button.clicked.connect(
            partial(self._set_frame_range, FrameRange.Padded)
        )
        self.frame_range_layout.addWidget(self.padded_frame_range_button)

        # Create the full frame range button
        self.full_frame_range_button = QtWidgets.QPushButton('Full')
        self.full_frame_range_button.clicked.connect(
            partial(self._set_frame_range, FrameRange.Full)
        )
        self.frame_range_layout.addWidget(self.full_frame_range_button)

        # Initial update
        self.refresh()
    
    def set_context(self, context):
        self._context = context
        self.refresh()

    def refresh(self):

        def _set_workspace_details():

            # Get the workspace details
            file_path = _file_path_from_context(self._context)
            version_name = file_path.stem.split('_')[-1]
            timestamp = dt.datetime.fromtimestamp(file_path.stat().st_mtime)
            user_name = get_user_name()

            # Set the workspace details
            self.workspace_version_label.setText(version_name)
            self.workspace_timestamp_label.setText(timestamp.strftime('%Y/%m/%d (%H:%M)'))
            self.workspace_user_label.setText(user_name)
        
        def _set_export_details():

            def _find_output(context, context_data):
                if context is None: return dict()
                if context_data is None: return dict()
                match context:
                    case AssetContext(_, category_name, asset_name, _):
                        return ctx.find_output(
                            context_data,
                            context = 'asset',
                            category = category_name,
                            asset = asset_name
                        )
                    case ShotContext(_, sequence_name, shot_name, _):
                        return ctx.find_output(
                            context_data,
                            context = 'shot',
                            sequence = sequence_name,
                            shot = shot_name
                        )
                    case KitContext(_, category_name, kit_name, _):
                        return ctx.find_output(
                            context_data,
                            context = 'kit',
                            category = category_name,
                            kit = kit_name
                        )
            
            def _get_context_path(context):
                export_path = _latest_export_path(context)
                if export_path is None: return None
                context_path = export_path / 'context.json'
                if not context_path.exists(): return None
                return context_path
            
            # Get the export details
            context_path = _get_context_path(self._context)
            if context_path is None:

                # Set the export details to N/A
                self.export_version_label.setText('N/A')
                self.export_timestamp_label.setText('N/A')
                self.export_user_label.setText('N/A')
            
            else:

                # Load the context data
                context_data = load_json(context_path)
                export_info = _find_output(self._context, context_data)
                timestamp = (
                    dt.datetime.fromisoformat(export_info['timestamp'])
                    if 'timestamp' in export_info else
                    dt.datetime.fromtimestamp(context_path.stat().st_mtime)
                )
                version_name = export_info['version'] if 'version' in export_info else export_path.stem
                user_name = export_info['user'] if 'user' in export_info else ''

                # Set the export details
                self.export_version_label.setText(version_name)
                self.export_timestamp_label.setText(timestamp.strftime('%Y/%m/%d (%H:%M)'))
                self.export_user_label.setText(user_name)

        if self._context is None:

            # Disable the buttons
            self.save_scene_button.setEnabled(False)
            self.refresh_scene_button.setEnabled(False)
            self.publish_scene_button.setEnabled(False)
            self.scene_info_button.setEnabled(False)
            self.open_workspace_location_button.setEnabled(False)
            self.open_export_location_button.setEnabled(False)
            self.open_texture_location_button.setEnabled(False)

            # Clear the workspace details
            self.workspace_version_label.setText('')
            self.workspace_timestamp_label.setText('')
            self.workspace_user_label.setText('')

            # Clear the export details
            self.export_version_label.setText('')
            self.export_timestamp_label.setText('')
            self.export_user_label.setText('')
        else:

            # Enable the buttons
            self.save_scene_button.setEnabled(True)
            self.refresh_scene_button.setEnabled(True)
            self.publish_scene_button.setEnabled(True)
            self.scene_info_button.setEnabled(True)
            self.open_workspace_location_button.setEnabled(True)
            self.open_export_location_button.setEnabled(True)
            self.open_texture_location_button.setEnabled(True)

            # Set the details
            _set_workspace_details()
            _set_export_details()

    def _save(self):
        if self._context is None: return
        self.save_scene.emit()
    
    def _refresh(self):
        if self._context is None: return
        self.refresh_scene.emit()

    def _publish(self):
        if self._context is None: return
        self.publish_scene.emit()
    
    def _scene_info(self):
        if self._context is None: return
        self.open_scene_info.emit()
    
    def _open_location(self, location):
        if self._context is None: return
        self.open_location.emit(location)
    
    def _set_frame_range(self, frame_range):
        if self._context is None: return
        self.set_frame_range.emit(frame_range)

class VersionButtonSurface(ButtonSurface):
    def __init__(self, context, parent=None):
        super().__init__(parent)

        # Members
        self._context = context

        # Settings
        self.setMinimumHeight(0)

        # Create the main layout
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setLayout(layout)

        # Create the content widget
        self._content = QtWidgets.QWidget()
        self._content.setStyleSheet(
            '.QWidget {'
            '   border: 1px solid black;'
            '}'
            'QLabel[timestamp=true] {'
            '   color: #919191;'
            '}'
            'QLabel[text=v0000] {'
            '   color: #616161;'
            '}'
        )
        layout.addWidget(self._content)

        # Create the content layout
        self._layout = QtWidgets.QHBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._content.setLayout(self._layout)

        # Set style
        self.setStyleSheet('padding: 5px;')

        # Update the content layout
        self.refresh()
    
    def payload(self):
        return self._context
    
    def overwrite(self, context):
        return False

    def refresh(self):

        # Clear the layout
        for index in reversed(range(self._layout.count())):
            item = self._layout.itemAt(index)
            if not item.isEmpty():
                widget = item.widget()
                widget.deleteLater()
            self._layout.removeItem(item)
        
        # Parameters
        version_name = (
            'v0000' if self._context.version_name is None else
            self._context.version_name
        )
        timestamp = _get_timestamp_from_context(self._context)
        date = '' if timestamp is None else timestamp.strftime('%d-%m-%Y')
        time = '' if timestamp is None else timestamp.strftime('%H:%M')
        
        # Create the version label
        version_label = QtWidgets.QLabel(version_name)
        version_label.setAlignment(Qt.AlignLeft)
        self._layout.addWidget(version_label, 1)

        # Create the date label
        date_label = QtWidgets.QLabel(date)
        date_label.setAlignment(Qt.AlignRight)
        date_label.setProperty('timestamp', True)
        self._layout.addWidget(date_label)

        # Create the time label
        time_label = QtWidgets.QLabel(time)
        time_label.setAlignment(Qt.AlignRight)
        time_label.setProperty('timestamp', True)
        self._layout.addWidget(time_label)

        # Force layout refresh
        self._content.setLayout(self._layout)

class VersionView(QtWidgets.QWidget):
    open_location = Signal(object)
    open_version = Signal(object)
    revive_version = Signal(object)

    def __init__(self, parent = None):
        super().__init__(parent)

        # Members
        self._context = None
        self._selection = None
        self._buttons = dict()

        # Settings
        self.setMinimumHeight(0)

        # Set the layout
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setLayout(layout)

        # Set the main scroll area
        self._scroll_area = QtWidgets.QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        layout.addWidget(self._scroll_area)

        # Set the main scroll area widget
        self._view = QtWidgets.QFrame()
        self._scroll_area.setWidget(self._view)
        self._scroll_area.setWidgetResizable(True)

        # Set the main scroll area widget layout
        self._layout = QtWidgets.QVBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._view.setLayout(self._layout)

        # Initial update
        self.refresh()
    
    def set_context(self, context):
        self._context = context
        self._selection = None
        self.refresh()
    
    def select(self, selection):

        # Get the clicked button
        button = self._buttons.get(selection)
        if button is None: return

        # Clear color on current button
        curr_button = self._buttons.get(self._selection)
        if curr_button is not None:
            curr_button.set_state(State.Selected, False)

        # Set the color of the clicked button
        button.set_state(State.Selected, True)

        # Set the selection
        self._selection = selection

    def _left_clicked(self, context):

        # Check if the clicked button is already selected
        if self._selection == context.version_name: return

        # Update the color of the clicked button
        self.select(context.version_name)

        # Emit the open version signal
        self.open_version.emit(context)
    
    def _right_clicked(self, point):
        
        # Get the clicked button
        button = self.sender()
        if button is None: return

        # Get the context
        context = button.surface().payload()

        # Build and display the menu
        menu = QtWidgets.QMenu()
        open_location_action = menu.addAction('Open Location')
        revive_version_action = menu.addAction('Revive')
        selected_action = menu.exec_(button.mapToGlobal(point))
        if selected_action is None: return
        if selected_action == open_location_action:
            return self.open_location.emit(context)
        if selected_action == revive_version_action:
            return self.revive_version.emit(context)
    
        # Invalid action
        assert False, f'Invalid action: {selected_action}'
    
    def refresh(self):

        # Clear the layout
        self._buttons.clear()
        for index in reversed(range(self._layout.count())):
            item = self._layout.itemAt(index)
            if not item.isEmpty():
                widget = item.widget()
                widget.deleteLater()
            self._layout.removeItem(item)

        # Get the version contexts
        version_contexts = [] if self._context is None else list(map(
            get_workfile_context,
            _list_file_paths(self._context)
        ))

        # Create the version buttons
        for context in reversed(version_contexts):
            version_button = ButtonHost(VersionButtonSurface(context))
            self._layout.addWidget(version_button)
            self._buttons[context.version_name] = version_button
        
        # Connect the version buttons
        for version_button in self._buttons.values():
            version_button.clicked.connect(self._left_clicked)
            version_button.setContextMenuPolicy(Qt.CustomContextMenu)
            version_button.customContextMenuRequested.connect(self._right_clicked)
        
        # Add a stretch
        self._layout.addStretch()

        # Refresh the layout
        self._view.setLayout(self._layout)

class ProjectBrowser(QtWidgets.QWidget):
    def __init__(self, parent = None):
        super().__init__(parent)

        # Members
        self._context = None
        self._selected_workspace = None
        self._selected_department = None
        self._auto_settings = AUTO_SETTINGS_DEFAULT.copy()

        # Settings
        self.setMinimumHeight(0)

        # Set the grid layout
        layout = QtWidgets.QGridLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        self.setLayout(layout)

        # Equally stretch the columns
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 1)

        # Create the workspace label
        workspace_label = QtWidgets.QLabel('Workspace')
        workspace_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(workspace_label, 0, 0)

        # Create the workspace browser
        self._workspace_browser = WorkspaceBrowser()
        layout.addWidget(self._workspace_browser, 1, 0)

        # Create the department label
        department_label = QtWidgets.QLabel('Department')
        department_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(department_label, 0, 1)

        # Create the department browser
        self._department_browser = DepartmentBrowser()
        layout.addWidget(self._department_browser, 1, 1)

        # Create the tabbed view
        self._tabbed_view = QtWidgets.QTabWidget()
        self._tabbed_view.setStyleSheet('QTabWidget::pane { border: 0; }')
        layout.addWidget(self._tabbed_view, 0, 2, 2, 1)

        # Create the details view
        self._details_view = DetailsView()
        self._tabbed_view.addTab(self._details_view, 'Details')

        # Create the version view
        self._version_view = VersionView()
        self._tabbed_view.addTab(self._version_view, 'Versions')

        # Connect the signals
        self._workspace_browser.selection_changed.connect(self._workspace_changed)
        self._workspace_browser.open_location.connect(self._workspace_open_path)
        self._workspace_browser.create_entry.connect(self._create_entry)
        self._workspace_browser.remove_entry.connect(self._remove_entry)
        self._department_browser.setting_changed.connect(self._setting_changed)
        self._department_browser.selection_changed.connect(self._department_changed)
        self._department_browser.open_location.connect(self._department_open_location)
        self._department_browser.reload_scene.connect(self._department_reload_scene)
        self._department_browser.new_from_current.connect(self._department_new_from_current)
        self._department_browser.new_from_template.connect(self._department_new_from_template)
        self._details_view.save_scene.connect(self._save_scene)
        self._details_view.refresh_scene.connect(self._refresh_scene)
        self._details_view.publish_scene.connect(self._publish_scene_clicked)
        self._details_view.open_scene_info.connect(self._open_scene_info)
        self._details_view.open_location.connect(self._open_location)
        self._details_view.set_frame_range.connect(self._set_frame_range)
        self._version_view.open_location.connect(self._open_workspace_location)
        self._version_view.open_version.connect(self._open_version)
        self._version_view.revive_version.connect(self._revive_version)

        # Register file path changed callback
        hou.hipFile.addEventCallback(self._file_path_changed)
    
    def refresh(self):
        self._details_view.refresh()
        self._version_view.refresh()
        self._department_browser.refresh()
        self._workspace_browser.refresh()
    
    def _selection(self):
        if self._selected_workspace is None: return None
        if self._selected_department is None: return None
        workspace_name, *remain = self._selected_workspace
        department_name, version_name = self._selected_department
        match workspace_name:
            case 'Assets':
                category_name, asset_name = remain
                return AssetContext(
                    department_name = department_name,
                    category_name = category_name,
                    asset_name = asset_name,
                    version_name = version_name
                )
            case 'Shots':
                sequence_name, shot_name = remain
                return ShotContext(
                    department_name = department_name,
                    sequence_name = sequence_name,
                    shot_name = shot_name,
                    version_name = version_name
                )
            case 'Kits':
                category_name, kit_name = remain
                return KitContext(
                    department_name = department_name,
                    category_name = category_name,
                    kit_name = kit_name,
                    version_name = version_name
                )
    
    def _select(self, context):
        self._selected_workspace = _path_from_context(context)
        self._selected_department = (
            context.department_name,
            context.version_name
        )

    def _file_path_changed(self, event_type):
        return
        match event_type:
            case hou.hipFileEventType.AfterClear:
                self._context = None
                self.refresh()
            case hou.hipFileEventType.AfterLoad:
                context = _get_context()
                if self._context == context: return
                self._context = context
                self._update_scene()
                self.refresh()

    def _update_scene(self):

        # Refresh the scene
        match self._context:
            case AssetContext(_, _, _):
                if self._auto_settings[Section.Asset][Action.Refresh]: self._refresh_scene()
            case ShotContext(_, _, _):
                if self._auto_settings[Section.Shot][Action.Refresh]: self._refresh_scene()
            case KitContext(_, _, _):
                if self._auto_settings[Section.Kit][Action.Refresh]: self._refresh_scene()
            case None: return

        # Set the frame range
        self._set_frame_range(FrameRange.Padded)

        # Set the render gallery path
        stage = hou.node("/stage")
        stage.parm("rendergallerysource").set("$HIP/galleries/rendergallery.db")

    def _workspace_changed(self, selected_path):
        self._selected_workspace = selected_path
        selected_entity = _entity_from_path(selected_path)
        self._department_browser.set_entity(selected_entity)
        current_path = _path_from_context(self._context)
        if selected_path != current_path: return
        if self._context is None: return
        self._department_browser.select(self._context.department_name)
    
    def _workspace_open_path(self, selected_path):
        if len(selected_path) == 0: return
        uri = '/'.join(selected_path[1:])
        match selected_path[0]:
            case 'Assets': location_path = api.storage.resolve(f'assets:/{uri}')
            case 'Shots': location_path = api.storage.resolve(f'shots:/{uri}')
            case 'Kits': location_path = api.storage.resolve(f'kits:/{uri}')
        location_path.mkdir(parents = True, exist_ok = True)
        self._open_location_path(location_path)
    
    def _create_entry(self, selected_path):
        
        def _create_asset_category():
            
            # Prompt the user for the category name
            category_name, accepted = QtWidgets.QInputDialog.getText(
                self,
                'Create Asset Category',
                'Enter the category name:'
            )
            if not accepted: return
            if len(category_name) == 0: return

            # Create the asset category
            api.config.add_category_name(category_name)

            # Update the UI
            self.refresh()
        
        def _create_asset(category_name):
            
            # Prompt the user for the asset name
            asset_name, accepted = QtWidgets.QInputDialog.getText(
                self,
                'Create Asset',
                'Enter the asset name:'
            )
            if not accepted: return
            if len(asset_name) == 0: return

            # Create the asset
            api.config.add_asset_name(category_name, asset_name)

            # Update the UI
            self.refresh()

        def _create_sequence():
            
            # Prompt the user for the sequence name
            sequence_name, accepted = QtWidgets.QInputDialog.getText(
                self,
                'Create Sequence',
                'Enter the sequence name:'
            )
            if not accepted: return
            if len(sequence_name) == 0: return

            # Create the sequence
            api.config.add_sequence_name(sequence_name)

            # Update the UI
            self.refresh()

        def _create_shot(sequence_name):
            
            # Prompt the user for the shot name
            shot_name, accepted = QtWidgets.QInputDialog.getText(
                self,
                'Create Shot',
                'Enter the shot name:'
            )
            if not accepted: return
            if len(shot_name) == 0: return

            # Create the shot
            api.config.add_shot_name(sequence_name, shot_name)

            # Update the UI
            self.refresh()

        def _create_kit_category():
            
            # Prompt the user for the category name
            category_name, accepted = QtWidgets.QInputDialog.getText(
                self,
                'Create Kit Category',
                'Enter the category name:'
            )
            if not accepted: return
            if len(category_name) == 0: return

            # Create the kit category
            api.config.add_kit_category_name(category_name)

            # Update the UI
            self.refresh()

        def _create_kit(category_name):
            
            # Prompt the user for the kit name
            kit_name, accepted = QtWidgets.QInputDialog.getText(
                self,
                'Create Kit',
                'Enter the kit name:'
            )
            if not accepted: return
            if len(kit_name) == 0: return

            # Create the kit
            api.config.add_kit_name(category_name, kit_name)

            # Update the UI
            self.refresh()

        # Identify the selected path
        match selected_path:
            case ['Assets']:
                _create_asset_category()
            case ['Assets', category_name]:
                _create_asset(category_name)
            case ['Shots']:
                _create_sequence()
            case ['Shots', sequence_name]:
                _create_shot(sequence_name)
            case ['Kits']:
                _create_kit_category()
            case ['Kits', category_name]:
                _create_kit(category_name)

    def _remove_entry(self, selected_path):
        
        def _remove_asset_category(category_name):
            
            # Prompt the user to confirm the removal
            result = QtWidgets.QMessageBox.question(
                self,
                'Remove Asset Category',
                f'Are you sure you want to remove the asset category: {category_name}?',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if result != QtWidgets.QMessageBox.Yes: return

            # Remove the asset category
            api.config.remove_category_name(category_name)

            # Update the UI
            self.refresh()

        def _remove_asset(category_name, asset_name):
            
            # Prompt the user to confirm the removal
            result = QtWidgets.QMessageBox.question(
                self,
                'Remove Asset',
                f'Are you sure you want to remove the asset: {category_name}/{asset_name}?',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if result != QtWidgets.QMessageBox.Yes: return

            # Remove the asset
            api.config.remove_asset_name(category_name, asset_name)

            # Update the UI
            self.refresh()

        def _remove_sequence(sequence_name):
            
            # Prompt the user to confirm the removal
            result = QtWidgets.QMessageBox.question(
                self,
                'Remove Sequence',
                f'Are you sure you want to remove the sequence: {sequence_name}?',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if result != QtWidgets.QMessageBox.Yes: return

            # Remove the sequence
            api.config.remove_sequence_name(sequence_name)

            # Update the UI
            self.refresh()

        def _remove_shot(sequence_name, shot_name):
            
            # Prompt the user to confirm the removal
            result = QtWidgets.QMessageBox.question(
                self,
                'Remove Shot',
                f'Are you sure you want to remove the shot: {sequence_name}/{shot_name}?',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if result != QtWidgets.QMessageBox.Yes: return

            # Remove the shot
            api.config.remove_shot_name(sequence_name, shot_name)

            # Update the UI
            self.refresh()

        def _remove_kit_category(category_name):
            
            # Prompt the user to confirm the removal
            result = QtWidgets.QMessageBox.question(
                self,
                'Remove Kit Category',
                f'Are you sure you want to remove the kit category: {category_name}?',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if result != QtWidgets.QMessageBox.Yes: return

            # Remove the kit category
            api.config.remove_kit_category_name(category_name)

            # Update the UI
            self.refresh()

        def _remove_kit(category_name, kit_name):
            
            # Prompt the user to confirm the removal
            result = QtWidgets.QMessageBox.question(
                self,
                'Remove Kit',
                f'Are you sure you want to remove the kit: {category_name}/{kit_name}?',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if result != QtWidgets.QMessageBox.Yes: return

            # Remove the kit
            api.config.remove_kit_name(category_name, kit_name)

            # Update the UI
            self.refresh()

        # Identify the selected path
        match selected_path:
            case ['Assets', category_name]:
                _remove_asset_category(category_name)
            case ['Assets', category_name, asset_name]:
                _remove_asset(category_name, asset_name)
            case ['Shots', sequence_name]:
                _remove_sequence(sequence_name)
            case ['Shots', sequence_name, shot_name]:
                _remove_shot(sequence_name, shot_name)
            case ['Kits', category_name]:
                _remove_kit_category(category_name)
            case ['Kits', category_name, kit_name]:
                _remove_kit(category_name, kit_name)
    
    def _department_changed(self, context):
        self._selected_department = (
            context.department_name,
            context.version_name
        )
        self._open_scene()
    
    def _department_open_location(self, context):
        if self._selected_workspace is None: return
        workspace, *remain = self._selected_workspace
        department_name = context.department_name
        match workspace:
            case 'Assets':
                category_name, asset_name = remain
                location_path = api.storage.resolve(f'assets:/{category_name}/{asset_name}/{department_name}')
            case 'Shots':
                sequence_name, shot_name = remain
                location_path = api.storage.resolve(f'shots:/{sequence_name}/{shot_name}/{department_name}')
            case 'Kits':
                category_name, kit_name = remain
                location_path = api.storage.resolve(f'kits:/{category_name}/{kit_name}/{department_name}')
        location_path.mkdir(parents = True, exist_ok = True)
        self._open_location_path(location_path)

    def _department_reload_scene(self, _context):
        if self._selected_workspace is None: return
        self._open_scene(True)
    
    def _department_new_from_current(self, context):

        # Find the selected context
        self._selected_department = (
            context.department_name,
            context.version_name
        )
        selected_context = self._selection()
        if selected_context is None: return

        # Maybe save changes
        success = self._save_changes()
        if not success: return

        # Save the current scene
        file_path = _next_file_path(selected_context)
        file_path.parent.mkdir(parents = True, exist_ok = True)
        hou.hipFile.save(path_str(file_path))

        # Update selected context
        self._context = get_workfile_context(file_path)
        _save_context(file_path.parent, None, self._context)
        self._update_scene()

        # Update the UI
        entity = _entity_from_context(self._context)
        self._department_browser.set_entity(entity)
        self._department_browser.select(self._context.department_name)
        self._details_view.set_context(self._context)
        self._version_view.set_context(self._context)
        self._version_view.select(self._context.version_name)

    def _department_new_from_template(self, context):
        
        # Find the selected context
        self._selected_department = (
            context.department_name,
            context.version_name
        )
        selected_context = self._selection()
        if selected_context is None: return

        # Maybe save changes
        success = self._save_changes()
        if not success: return

        # Create the new scene
        file_path = _next_file_path(selected_context)
        file_path.parent.mkdir(parents = True, exist_ok = True)
        hou.hipFile.clear(suppress_save_prompt=True)
        hou.hipFile.save(path_str(file_path))
        
        # Update current context
        self._context = get_workfile_context(file_path)
        _save_context(file_path.parent, None, self._context)
        self._initialize_scene()
        self._update_scene()

        # Update the UI
        entity = _entity_from_context(self._context)
        self._department_browser.set_entity(entity)
        self._department_browser.select(self._context.department_name)
        self._details_view.set_context(self._context)
        self._version_view.set_context(self._context)
        self._version_view.select(self._context.version_name)
    
    def _publish_scene_clicked(self):
        self._save_scene()
        self._publish_scene()
    
    def _save_changes(self):

        # Check if current scene has unsaved changes
        if not hou.hipFile.hasUnsavedChanges(): return True
                
        # Save the current scene
        if hou.hipFile.isNewFile():

            # Ask the user if they want to save the current non-pipe scene
            message = 'The current scene has unsaved changes.\nDo you want to save it?'
            result = QtWidgets.QMessageBox.question(self, 'Save Scene', message, QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel)
            if result == QtWidgets.QMessageBox.Yes:

                file_path = hou.ui.selectFile(
                    title = 'Choose the file path to save the scene',
                    start_directory = path_str(Path.home()),
                    file_type = hou.fileType.Hip,
                    chooser_mode = hou.fileChooserMode.Write
                )
                if len(file_path) == 0: return False
                hou.hipFile.save(file_path)
            
            elif result == QtWidgets.QMessageBox.No:
                return True
            
            elif result == QtWidgets.QMessageBox.Cancel:
                return False
        else:

            # Get the section
            section = _section_from_context(self._context)

            # Save the scene
            if not self._auto_settings[section][Action.Save]: return True
            file_path = _next_file_path(self._context)
            hou.hipFile.save(path_str(file_path))

            # Update current context
            prev_context = self._context
            self._context = get_workfile_context(file_path)
            _save_context(file_path.parent, prev_context, self._context)
        
            # Publish the scene
            if not self._auto_settings[section][Action.Publish]: return True
            self._publish_scene(True)

        # Return success
        return True

    def _open_scene(self, should_reload = False):

        # Check if we have a valid workspace and department
        selected_context = self._selection()
        if selected_context is None: return

        # If we are opening the same scene, do nothing
        if self._context == selected_context and not should_reload: return
        
        # Maybe save changes
        success = self._save_changes()
        if not success: return
        
        # Get the file path
        file_path = (
            _next_file_path(selected_context)
            if selected_context.version_name is None else
            _file_path_from_context(selected_context)
        )

        # Set the update mode to manual
        with util.update_mode(hou.updateMode.Manual):
            
            # Load the file path if it exists, otherwise create it
            if file_path.exists():
                hou.hipFile.load(
                    path_str(file_path),
                    suppress_save_prompt = True,
                    ignore_load_warnings = True
                )
                context = get_workfile_context(file_path)
                assert context is not None, (
                    f'Failed to get context from file path: {file_path}'
                )
                self._context = context
            else:
                file_path.parent.mkdir(parents = True, exist_ok = True)
                hou.hipFile.clear(suppress_save_prompt=True)
                hou.hipFile.save(path_str(file_path))
                context = get_workfile_context(file_path)
                assert context is not None, (
                    f'Failed to get context from file path: {file_path}'
                )
                self._context = context
                _save_context(file_path.parent, None, self._context)
                self._initialize_scene()
            
            # Find a build shot node
            build_shot_nodes = list(map(
                build_shot.BuildShot,
                ns.list_by_node_type('build_shot', 'Lop')
            ))
            if len(build_shot_nodes) > 0:
                build_shot_node = build_shot_nodes[0]
                build_shot_node.setDisplayFlag(True)

            # Update the dependencies
            self._update_scene()

            # Update the details and versions view
            entity = _entity_from_context(self._context)
            self._workspace_browser.select(entity)
            self._department_browser.set_entity(entity)
            self._department_browser.select(self._context.department_name)
            self._details_view.set_context(self._context)
            self._version_view.set_context(self._context)
            self._version_view.select(self._context.version_name)

            # Center the network editor view
            center_all_network_editors()

            # Set the viewport to the vulkan renderer
            vulkan_all_scene_viewers()
    
    def _initialize_scene(self):

        # Check if we have a valid workspace and department
        if self._context is None: return

        # Prepare to initialize the scene
        scene_node = hou.node('/stage')

        # Initialize based on the workspace
        def _initialize_asset_scene(category_name, asset_name, department_name):
            match department_name:
                case 'model':

                    # Create the SOP create node
                    sop_node = scene_node.createNode('sopcreate', 'create_model')
                    sop_node.parm('pathprefix').set(f'/{category_name}/{asset_name}/geo/')
                    
                    # Create the export node
                    export_node = export_asset_layer.create(scene_node, 'export_model')
                    export_node.native().setInput(0, sop_node)
                
                case 'blendshape':

                    # Create the import model node
                    import_model_node = import_asset_layer.create(scene_node, 'import_model')
                    import_model_node.set_department_name('model')

                    # Create the SOP create node
                    sop_node = scene_node.createNode('sopcreate', 'create_blendshapes')
                    sop_node.parm('pathprefix').set(f'/{category_name}/{asset_name}/blshp/')
                    sop_dive_node = sop_node.node('sopnet/create')

                    # Create the SOP import model node
                    sop_import_model_node = sop_dive_node.createNode('lopimport', 'import_model')
                    sop_import_model_node.parm('loppath').set(sop_import_model_node.relativePathTo(import_model_node))
                    sop_import_model_node.parm('primpattern').set(f'/{category_name}/{asset_name}/geo')
                    sop_import_model_node.parm('timesample').set(0)

                    # Create the SOP unpackusd node
                    sop_unpack_model_node = sop_dive_node.createNode('unpackusd', 'unpack_model')
                    sop_unpack_model_node.setInput(0, sop_import_model_node)

                    # Create the SOP model anchor
                    sop_model_anchor_node = sop_dive_node.createNode('null', 'IN_model')
                    sop_model_anchor_node.setInput(0, sop_unpack_model_node)

                    # Create the SOP GOZ import node
                    sop_goz_import_node = sop_dive_node.createNode('goz_import', 'goz_import')

                    # Create the SOP cache node
                    sop_cache_node = cache.create(sop_dive_node, 'cache')
                    sop_cache_node.native().setInput(0, sop_goz_import_node)

                    # Create the SOP name node
                    sop_name_node = sop_dive_node.createNode('name', 'name')
                    sop_name_node.parm('name1').set('$OS')
                    sop_name_node.setInput(0, sop_cache_node.native())

                    # Create the SOP merge node
                    sop_merge_node = sop_dive_node.createNode('merge', 'merge')
                    sop_merge_node.setInput(0, sop_name_node)

                    # Create the SOP output node
                    sop_output_node = sop_dive_node.createNode('output', 'output')
                    sop_output_node.setInput(0, sop_merge_node)

                    # Layout the dive nodes
                    sop_dive_node.layoutChildren()

                    # Create the export node
                    export_node = export_asset_layer.create(scene_node, 'export_blendshapes')
                    export_node.native().setInput(0, sop_node)

                case 'lookdev':

                    # Create the import model node
                    import_node = import_asset_layer.create(scene_node, 'import_model')
                    import_node.set_department_name('model')

                    # Create the lookdev studio node
                    studio_node = lookdev_studio.create(scene_node, 'lookdev_studio')
                    studio_node.parm('primpattern').set(f'/{category_name}/{asset_name}')
                    studio_node.setInput(0, import_node.native())
                    
                    # Create the export node
                    export_node = export_asset_layer.create(scene_node, 'export_lookdev')
                    export_node.native().setInput(0, studio_node.native(), 1)

                case 'rig':
                    
                    # Create the model import node
                    import_model_node = import_asset_layer.create(scene_node, 'import_model')
                    import_model_node.set_department_name('model')
                    
                    # Create the blendshape import node
                    import_blendshapes_node = import_asset_layer.create(scene_node, 'import_blendshapes')
                    import_blendshapes_node.set_department_name('blendshape')

                    # Create the SOP modify node
                    sop_node = scene_node.createNode('sopmodify', 'rigging')
                    sop_node.parm('primpattern').set(f'/{category_name}/{asset_name}/geo')
                    sop_node.parm('unpacktopolygons').set(1)
                    sop_node.setInput(0, import_model_node.native())

                case _: return

        def _initialize_shot_scene(sequence_name, shot_name, department_name):
            match department_name:
                case 'layout':
                    import_node = import_assets.create(scene_node, 'import_assets')
                    export_node = export_shot_layer.create(scene_node, 'export_shot')
                    export_node.native().setInput(0, import_node.native())

                case 'environment':
                    import_node = build_shot.create(scene_node, 'import_shot')
                    export_node = export_shot_layer.create(scene_node, 'export_shot')
                    export_node.native().setInput(0, import_node.native())

                case 'animation':
                    
                    # Import the assets
                    import_node = build_shot.create(scene_node, 'import_shot')
                    import_node.execute()
                    
                    # Scrape the assets
                    root = import_node.native().stage().GetPseudoRoot()
                    assets = [
                        (asset_info['category'], asset_info['asset'])
                        for asset_info in util.list_assets(root)
                    ]

                    # Create the animation node
                    animate_node = animate.create(scene_node, 'animate_shot')
                    animate_node.native().setInput(0, import_node.native())
                    inner_animate_node = animate_node.native().node('anim/sopnet/create')
                    output_animate_node = inner_animate_node.node('output0')

                    # Import the rigs
                    rigs_node = import_rigs.create(inner_animate_node, 'import_rigs')
                    for category_name, asset_name in assets:
                        rigs_node.inc_asset_entry(category_name, asset_name)
                    rigs_node.execute()

                    # Create the scene animate nodes
                    scene_animate_node = inner_animate_node.createNode('apex::sceneanimate', 'scene_animate')
                    scene_animate_node.setInput(0, rigs_node.native())
                    invoke_scene_node = inner_animate_node.createNode('apex::sceneinvoke', 'scene_invoke')
                    invoke_scene_node.setInput(0, scene_animate_node)
                    invoke_scene_node.parm('outputallshapes').pressButton()
                    output_animate_node.setInput(0, invoke_scene_node)

                    # Create the playblast node
                    playblast_node = playblast.create(inner_animate_node, 'playblast')
                    playblast_node.native().setInput(0, invoke_scene_node)

                    # Layout the dive nodes
                    inner_animate_node.layoutChildren()

                    # Create the export node
                    export_node = export_shot_layer.create(scene_node, 'export_shot')
                    export_node.native().setInput(0, animate_node.native())

                case 'crowd':
                    import_node = build_shot.create(scene_node, 'import_shot')
                    export_node = export_shot_layer.create(scene_node, 'export_shot')
                    export_node.native().setInput(0, import_node.native())

                case 'effects':
                    import_node = build_shot.create(scene_node, 'import_shot')
                    export_node = export_shot_layer.create(scene_node, 'export_shot')
                    export_node.native().setInput(0, import_node.native())

                case 'cfx':
                    import_node = build_shot.create(scene_node, 'import_shot')
                    export_node = export_shot_layer.create(scene_node, 'export_shot')
                    export_node.native().setInput(0, import_node.native())

                case 'light':
                    import_node = build_shot.create(scene_node, 'import_shot')
                    render_vars_node = render_vars.create(scene_node, 'render_vars')
                    render_settings_node = render_settings.create(scene_node, 'render_settings')
                    export_node = export_shot_layer.create(scene_node, 'export_shot')
                    render_vars_node.setInput(0, import_node.native())
                    render_settings_node.native().setInput(0, render_vars_node)
                    export_node.native().setInput(0, render_settings_node.native())
                    render_layer_names = api.config.list_render_layer_names(sequence_name, shot_name)
                    for render_layer_name in render_layer_names:
                        render_layer_node = export_render_layer.create(scene_node, f'export_{render_layer_name}')
                        render_layer_node.native().setInput(0, export_node.native())

                case 'composite':
                    cop_node = scene_node.createNode('copnet', 'composite_shot')
                    comp_node = build_comp.create(cop_node, 'composite_shot')
                    
                case _: return

        def _initialize_kit_scene(category_name, kit_name, department_name):
            match department_name:
                case 'general':
                    export_node = export_kit_layer.create(scene_node, 'export_general')
                case _: return
        
        # Initialize the scene
        match self._context:
            case AssetContext(department_name, category_name, asset_name):
                try: _initialize_asset_scene(category_name, asset_name, department_name)
                except: pass
            case ShotContext(department_name, sequence_name, shot_name):
                try: _initialize_shot_scene(sequence_name, shot_name, department_name)
                except: pass
            case KitContext(department_name, category_name, kit_name):
                try: _initialize_kit_scene(category_name, kit_name, department_name)
                except: pass
            case None: pass
        
        # Layout the nodes
        scene_node.layoutChildren()
        
        # Save the changes
        file_path = _latest_file_path(self._context)
        assert file_path is not None, (
            f'Failed to get file path from context: {self._context}'
        )
        hou.hipFile.save(path_str(file_path))
    
    def _save_scene(self):

        # Check if we have a valid workspace and department
        if self._context is None: return
        prev_context = self._context

        # Save the file path
        file_path = _next_file_path(self._context)
        hou.hipFile.save(path_str(file_path))

        # Set the new context
        self._context = get_workfile_context(file_path)
        _save_context(file_path.parent, prev_context, self._context)

        # Update details
        entity = _entity_from_context(self._context)
        self._department_browser.set_entity(entity)
        self._department_browser.select(self._context.department_name)
        self._details_view.set_context(self._context)
        self._version_view.set_context(self._context)
        self._version_view.select(self._context.version_name)
    
    def _publish_scene(self, ignore_missing_export = False):

        # Check if we have a valid workspace and department
        if self._context is None: return

        def _is_asset_export_correct(node):
            match self._context:
                case AssetContext(department_name, category_name, asset_name):
                    if node.get_department_name() != department_name: return False
                    if node.get_category_name() != category_name: return False
                    if node.get_asset_name() != asset_name: return False
                    return True
                case _:
                    return False
        
        def _is_rig_export_correct(node):
            match self._context:
                case AssetContext(department_name, category_name, asset_name):
                    if department_name != 'rig': return False
                    if node.get_category_name() != category_name: return False
                    if node.get_asset_name() != asset_name: return False
                    return True
                case _:
                    return False
    
        def _is_shot_export_correct(node):
            match self._context:
                case ShotContext(department_name, sequence_name, shot_name):
                    if node.get_department_name() != department_name: return False
                    if node.get_sequence_name() != sequence_name: return False
                    if node.get_shot_name() != shot_name: return False
                    return True
                case _:
                    return False
    
        def _is_render_layer_export_correct(node):
            match self._context:
                case ShotContext(department_name, sequence_name, shot_name):
                    if node.get_department_name() != department_name: return False
                    if node.get_sequence_name() != sequence_name: return False
                    if node.get_shot_name() != shot_name: return False
                    return True
                case _:
                    return False
    
        def _is_kit_export_correct(node):
            match self._context:
                case KitContext(department_name, category_name, kit_name):
                    if node.get_department_name() != department_name: return False
                    if node.get_category_name() != category_name: return False
                    if node.get_kit_name() != kit_name: return False
                    return True
                case _:
                    return False

        # Find the export node with the correct workspace and department
        match self._context:
            case AssetContext('rig', _, _):

                # Find the export nodes
                rig_export_nodes = list(filter(
                    _is_rig_export_correct,
                    map(
                        export_rig.ExportRig,
                        ns.list_by_node_type('export_rig', 'Sop')
                    )
                ))

                # Check if we have any export nodes, report if not
                if len(rig_export_nodes) == 0:
                    if ignore_missing_export: return
                    hou.ui.displayMessage(
                        'No rig export nodes found for the current asset.',
                        severity = hou.severityType.Warning
                    )
                    return
                
                # Check if there are more than one export nodes, report if so
                if len(rig_export_nodes) > 1:
                    hou.ui.displayMessage(
                        'More than one rig export node found for the current asset.',
                        severity = hou.severityType.Warning
                    )
                    return
            
                # Execute the export node
                rig_export_node = rig_export_nodes[0]
                rig_export_node.execute()
            
            case AssetContext(_, _, _):
                
                # Find the export nodes
                asset_export_nodes = list(filter(
                    _is_asset_export_correct,
                    map(
                        export_asset_layer.ExportAssetLayer,
                        ns.list_by_node_type('export_asset_layer', 'Lop')
                    )
                ))

                # Check if we have any export nodes, report if not
                if len(asset_export_nodes) == 0:
                    if ignore_missing_export: return
                    hou.ui.displayMessage(
                        'No export nodes found for the current asset.',
                        severity = hou.severityType.Warning
                    )
                    return

                # Check if there are more than one export nodes, report if so
                if len(asset_export_nodes) > 1:
                    hou.ui.displayMessage(
                        'More than one export node found for the current asset.',
                        severity = hou.severityType.Warning
                    )
                    return
            
                # Execute the export node
                asset_export_node = asset_export_nodes[0]
                asset_export_node.execute()

            case ShotContext(_, _, _):

                # Find any build shot nodes
                build_shot_nodes = list(map(
                    build_shot.BuildShot,
                    ns.list_by_node_type('build_shot', 'Lop')
                ))
                
                # Temporarily disable procedurals
                build_node_include_procedurals = {
                    build_shot_node.path(): build_shot_node.get_include_procedurals()
                    for build_shot_node in build_shot_nodes 
                }
                for build_shot_node in build_shot_nodes:
                    build_shot_node.set_include_procedurals(False)

                # Find the export nodes
                shot_export_nodes = list(filter(
                    _is_shot_export_correct,
                    map(
                        export_shot_layer.ExportShotLayer,
                        ns.list_by_node_type('export_shot_layer', 'Lop')
                    )
                ))
                render_layer_export_nodes = list(filter(
                    _is_render_layer_export_correct,
                    map(
                        export_render_layer.ExportRenderLayer,
                        ns.list_by_node_type('export_render_layer', 'Lop')
                    )
                ))

                # Check if we have any export nodes, report if not
                if len(shot_export_nodes) == 0:
                    if ignore_missing_export: return
                    hou.ui.displayMessage(
                        'No export nodes found for the current shot.',
                        severity = hou.severityType.Warning
                    )
                    return
            
                # Check if there are more than one export nodes, report if so
                if len(shot_export_nodes) > 1:
                    hou.ui.displayMessage(
                        'More than one export node found for the current shot.',
                        severity = hou.severityType.Warning
                    )
                    return
            
                # Execute the export node
                shot_export_node = shot_export_nodes[0]
                shot_export_node.execute()

                # Export the render layers
                for render_layer_export_node in render_layer_export_nodes:
                    render_layer_export_node.execute()
                
                # Re-enable procedurals
                for build_shot_node in build_shot_nodes:
                    include_procedurals = build_node_include_procedurals[build_shot_node.path()]
                    build_shot_node.set_include_procedurals(include_procedurals)
            
            case KitContext(_, _, _):

                # Find the export nodes
                kit_export_nodes = list(filter(
                    _is_kit_export_correct,
                    map(
                        export_kit_layer.ExportKitLayer,
                        ns.list_by_node_type('export_kit_layer', 'Lop')
                    )
                ))

                # Check if we have any export nodes, report if not
                if len(kit_export_nodes) == 0:
                    if ignore_missing_export: return
                    hou.ui.displayMessage(
                        'No export nodes found for the current kit.',
                        severity = hou.severityType.Warning
                    )
                    return
                
                # Check if there are more than one export nodes, report if so
                if len(kit_export_nodes) > 1:
                    hou.ui.displayMessage(
                        'More than one export node found for the current kit.',
                        severity = hou.severityType.Warning
                    )
                    return
                
                # Execute the export node
                kit_export_node = kit_export_nodes[0]
                kit_export_node.execute()
            
            case None:
                assert False, 'Invalid workspace'
        
        # Update details
        self._details_view.set_context(self._context)
    
    def _refresh_scene(self):

        # Find shot build nodes
        build_shot_nodes = list(map(
            build_shot.BuildShot,
            ns.list_by_node_type('build_shot', 'Lop')
        ))

        # Find import asset nodes
        import_assets_nodes = list(map(
            import_assets.ImportAssets,
            ns.list_by_node_type('import_assets', 'Lop')
        ))

        # Find the import asset layer nodes
        import_asset_layer_nodes = list(map(
            import_asset_layer.ImportAssetLayer,
            ns.list_by_node_type('import_asset_layer', 'Lop')
        ))

        # Find the import kit layer nodes
        import_kit_layer_nodes = list(map(
            import_kit_layer.ImportKitLayer,
            ns.list_by_node_type('import_kit_layer', 'Lop')
        ))

        # Find the import shot layer nodes
        import_shot_layer_nodes = list(map(
            import_shot_layer.ImportShotLayer,
            ns.list_by_node_type('import_shot_layer', 'Lop')
        ))

        # Find the import render layer nodes
        import_render_layer_nodes = list(map(
            import_render_layer.ImportRenderLayer,
            ns.list_by_node_type('import_render_layer', 'Lop')
        ))

        # Find the import rigs nodes
        import_rig_nodes = list(map(
            import_rigs.ImportRigs,
            ns.list_by_node_type('import_rigs', 'Sop')
        ))

        # Find the build comp nodes
        build_comp_nodes = list(map(
            build_comp.BuildComp,
            ns.list_by_node_type('build_comp', 'Cop')
        ))

        # Clear the caches
        import_asset_layer.clear_cache()
        import_kit_layer.clear_cache()
        import_shot_layer.clear_cache()
        import_render_layer.clear_cache()
        import_rigs.clear_cache()

        # Import latest shot builds
        for build_shot_node in build_shot_nodes:
            if not build_shot_node.is_valid(): continue
            build_shot_node.execute()

        # Import latest assets
        for import_assets_node in import_assets_nodes:
            if not import_assets_node.is_valid(): continue
            import_assets_node.execute()

        # Import latest asset layers
        for import_node in import_asset_layer_nodes:
            if not import_node.is_valid(): continue
            import_node.latest()
            import_node.execute()
        
        # Import latest kit layers
        for import_node in import_kit_layer_nodes:
            if not import_node.is_valid(): continue
            import_node.latest()
            import_node.execute()
        
         # Import latest shot layers
        for import_node in import_shot_layer_nodes:
            if not import_node.is_valid(): continue
            import_node.latest()
            import_node.execute()
        
        # Import latest render layers
        for import_node in import_render_layer_nodes:
            if not import_node.is_valid(): continue
            import_node.latest()
            import_node.execute()
        
        # Import latest rigs
        for import_node in import_rig_nodes:
            if not import_node.is_valid(): continue
            import_node.execute()
        
        # Import latest comps
        for build_node in build_comp_nodes:
            if not build_node.is_valid(): continue
            build_node.update()
    
    def _open_version(self, context):
        self._select(context)
        self._open_scene()
        self._department_browser.overwrite(context)
        self._tabbed_view.setCurrentWidget(self._details_view)

    def _revive_version(self, context):
        self._select(context)
        self._open_scene()
        self._save_scene()
        self._tabbed_view.setCurrentWidget(self._details_view)

    def _open_scene_info(self):
        pass

    def _setting_changed(self, auto):
        entity, action, value = auto
        self._auto_settings[entity][action] = value

    def _open_location(self, location):
        match location:
            case Location.Workspace: self._open_workspace_location(self._context)
            case Location.Export: self._open_export_location(self._context)
            case Location.Texture: self._open_texture_location(self._context)

    def _open_workspace_location(self, context):
        file_path = _file_path_from_context(context)
        if file_path is None: return
        self._open_location_path(file_path)
    
    def _open_export_location(self, context):
        if context is None: return
        export_path = _latest_export_path(context)
        if export_path is None: return
        self._open_location_path(export_path)
    
    def _open_texture_location(self, context):
        file_path = _file_path_from_context(context)
        if file_path is None: return
        texture_path = file_path.parent.parent / 'texture'
        texture_path.mkdir(parents = True, exist_ok = True)
        self._open_location_path(texture_path)

    def _open_location_path(self, file_path):
        hou.ui.showInFileBrowser(
            path_str(file_path) + '/' if file_path.is_dir() else path_str(file_path)
        )
    
    def _set_frame_range(self, mode):

        # Check if we have a valid workspace and department
        if self._context is None: return

        # Set the frame range based on workspace
        match self._context:
            case ShotContext(_, sequence_name, shot_name):
                frame_range = api.config.get_frame_range(sequence_name, shot_name)
                match mode:
                    case FrameRange.Padded: util.set_block_range(frame_range.full_range())
                    case FrameRange.Full: util.set_block_range(frame_range.play_range())
            case _:
                util.set_block_range(BlockRange(1001, 1200))

def create():
    widget = ProjectBrowser()
    return widget