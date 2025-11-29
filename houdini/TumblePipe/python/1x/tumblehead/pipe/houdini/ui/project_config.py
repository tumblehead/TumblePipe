from enum import Enum

from qtpy import QtWidgets

from tumblehead.api import default_client

api = default_client()

class Department(Enum):
    Asset = 'Asset'
    Shot = 'Shot'
    Preset = 'Preset'
    Render = 'Render'

class DepartmentDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(DepartmentDialog, self).__init__(parent)

        # Set up the layout
        self.layout = QtWidgets.QVBoxLayout()
        self.setLayout(self.layout)

        # Asset department label and editable list
        self.asset_department_label = QtWidgets.QLabel('Asset Departments')
        self.asset_department_list = QtWidgets.QListWidget()
        self.layout.addWidget(self.asset_department_label)
        self.layout.addWidget(self.asset_department_list)

        # Asset department list buttons
        self.asset_department_add_button = QtWidgets.QPushButton('Add')
        self.asset_department_remove_button = QtWidgets.QPushButton('Remove')
        self.asset_department_add_button.clicked.connect(lambda: self.add_department(Department.Asset))
        self.asset_department_remove_button.clicked.connect(lambda: self.remove_department(Department.Asset))

        # Asset department list button layout
        asset_department_button_layout = QtWidgets.QHBoxLayout()
        self.layout.addLayout(asset_department_button_layout)
        asset_department_button_layout.addWidget(self.asset_department_add_button)
        asset_department_button_layout.addWidget(self.asset_department_remove_button)
        asset_department_button_layout.addStretch()

        # Shot department label and editable list
        self.shot_department_label = QtWidgets.QLabel('Shot Departments')
        self.shot_department_list = QtWidgets.QListWidget()
        self.layout.addWidget(self.shot_department_label)
        self.layout.addWidget(self.shot_department_list)

        # Shot department list buttons
        self.shot_department_add_button = QtWidgets.QPushButton('Add')
        self.shot_department_remove_button = QtWidgets.QPushButton('Remove')
        self.shot_department_add_button.clicked.connect(lambda: self.add_department(Department.Shot))
        self.shot_department_remove_button.clicked.connect(lambda: self.remove_department(Department.Shot))

        # Shot department list button layout
        shot_department_button_layout = QtWidgets.QHBoxLayout()
        self.layout.addLayout(shot_department_button_layout)
        shot_department_button_layout.addWidget(self.shot_department_add_button)
        shot_department_button_layout.addWidget(self.shot_department_remove_button)
        shot_department_button_layout.addStretch()

        # Render department label and editable list
        self.render_department_label = QtWidgets.QLabel('Render Departments')
        self.render_department_list = QtWidgets.QListWidget()
        self.layout.addWidget(self.render_department_label)
        self.layout.addWidget(self.render_department_list)

        # Render department list buttons
        self.render_department_add_button = QtWidgets.QPushButton('Add')
        self.render_department_remove_button = QtWidgets.QPushButton('Remove')
        self.render_department_add_button.clicked.connect(lambda: self.add_department(Department.Render))
        self.render_department_remove_button.clicked.connect(lambda: self.remove_department(Department.Render))

        # Render department list button layout
        render_department_button_layout = QtWidgets.QHBoxLayout()
        self.layout.addLayout(render_department_button_layout)
        render_department_button_layout.addWidget(self.render_department_add_button)
        render_department_button_layout.addWidget(self.render_department_remove_button)
        render_department_button_layout.addStretch()

        # Add save and cancel buttons
        self.save_button = QtWidgets.QPushButton('Save')
        self.cancel_button = QtWidgets.QPushButton('Cancel')
        self.save_button.clicked.connect(self.save)
        self.cancel_button.clicked.connect(self.cancel)

        # Add buttons to layout
        button_layout = QtWidgets.QHBoxLayout()
        self.layout.addLayout(button_layout)
        button_layout.addStretch()
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.cancel_button)

        # Add stretch
        self.layout.addStretch()

        # Load the departments
        self.load()
    
    def load(self):
        pass
    
    def save(self):
        pass

    def cancel(self):
        self.close()

    def add_department(self, department):
        pass

    def remove_department(self, department):
        pass

class AssetDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(AssetDialog, self).__init__(parent)

class ShotDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(ShotDialog, self).__init__(parent)

class PresetDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(PresetDialog, self).__init__(parent)

class DefaultsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(DefaultsDialog, self).__init__(parent)

class ProjectConfig(QtWidgets.QWidget):
    def __init__(self):
        super(ProjectConfig, self).__init__()

        self.layout = QtWidgets.QVBoxLayout()
        self.setLayout(self.layout)

        # Edit departments button
        self.edit_departments_button = QtWidgets.QPushButton('Edit Departments')
        self.layout.addWidget(self.edit_departments_button)
        self.edit_departments_button.clicked.connect(self.edit_departments)

        # Edit assets button
        self.edit_assets_button = QtWidgets.QPushButton('Edit Assets')
        self.layout.addWidget(self.edit_assets_button)
        self.edit_assets_button.clicked.connect(self.edit_assets)

        # Edit shots button
        self.edit_shots_button = QtWidgets.QPushButton('Edit Shots')
        self.layout.addWidget(self.edit_shots_button)
        self.edit_shots_button.clicked.connect(self.edit_shots)

        # Edit defaults button
        self.edit_defaults_button = QtWidgets.QPushButton('Edit Defaults')
        self.layout.addWidget(self.edit_defaults_button)
        self.edit_defaults_button.clicked.connect(self.edit_defaults)

        # Add stretch
        self.layout.addStretch()
    
    def edit_departments(self):
        department_dialog = DepartmentDialog()
        department_dialog.exec_()

    def edit_assets(self):
        asset_dialog = AssetDialog()
        asset_dialog.exec_()

    def edit_shots(self):
        shot_dialog = ShotDialog()
        shot_dialog.exec_()

    def edit_defaults(self):
        defaults_dialog = DefaultsDialog()
        defaults_dialog.exec_()

def create():
    widget = ProjectConfig()
    return widget