"""What the SOP and LOP th::playblast nodes share: the Camera parm, the
labels, View Latest and Open Location.

Each node says where its stage is (``_get_stage_root``); the rest reads the
node's shot and department the way EntityNode resolves them.
"""

import os

import hou

from tumblepipe.api import path_str
from tumblepipe.pipe.houdini import prim_queries
from tumblepipe.pipe.paths import get_latest_playblast_path, get_next_playblast_path

#: The playblast size when the shot configures no render resolution.
DEFAULT_RES = (1280, 720)


def resolve_camera_path(camera_name: str, camera_paths: list[str]) -> str | None:
    """The stage camera a playblast's Camera parm names, or None.

    Camera is a menu whose value starts empty, and Houdini shows an empty
    menu parm as its first entry: the artist sees a camera picked and
    presses Playblast (Magnus: "just try to click playblast, there is a
    python error"). Empty is therefore the camera the menu shows, the first
    one listed.
    """
    camera_names = [path.rsplit('/', 1)[-1] for path in camera_paths]
    if camera_name == '':
        return camera_paths[0] if camera_paths else None
    if camera_name not in camera_names:
        return None
    return camera_paths[camera_names.index(camera_name)]


def camera_error(camera_name: str, camera_names: list[str]) -> str:
    if not camera_names:
        return 'there is no camera on the stage: the shot has no camera published'
    return (
        f"camera '{camera_name}' is not a camera on the stage "
        f"(cameras: {', '.join(camera_names)})"
    )


class PlayblastNodeMixin:
    """For an EntityNode that playblasts its shot's camera."""

    def _get_stage_root(self):
        """The pseudo-root of the stage the node reads its cameras from;
        None while there is none."""
        raise NotImplementedError

    # Camera

    def list_camera_paths(self):
        root = self._get_stage_root()
        if root is None: return []
        return prim_queries.list_stage_cameras(root)

    def list_camera_names(self):
        return [path.rsplit('/', 1)[-1] for path in self.list_camera_paths()]

    def get_camera_path(self):
        """The camera the Camera parm names; empty is the first listed (see
        resolve_camera_path)."""
        camera_path = resolve_camera_path(
            self.parm('camera').eval(), self.list_camera_paths()
        )
        if camera_path is None:
            raise self._error(camera_error(
                self.parm('camera').eval(), self.list_camera_names()
            ))
        return camera_path

    def set_camera_name(self, camera_name):
        camera_names = self.list_camera_names()
        if camera_name not in camera_names:
            raise self._error(camera_error(camera_name, camera_names))
        self.parm('camera').set(camera_name)
        self._update_labels()

    # Labels

    def _update_labels(self):
        """Show what 'from_context' resolved to, as import_shot does."""
        self.parm('entity_label').set(self._entity_label())
        self.parm('department_label').set(self._department_label())

    # The movies

    def view_latest(self):
        """Open the newest playblast, or say there is none."""
        entity_uri = self.require_entity_uri()
        department_name = self.require_department_name()
        latest_path = get_latest_playblast_path(entity_uri, department_name)
        if latest_path is None or not latest_path.exists():
            return hou.ui.displayMessage(
                f'No playblast found for {entity_uri}.\n{latest_path}',
                title='Playblast',
                severity=hou.severityType.Message
            )
        os.startfile(path_str(latest_path))

    def open_location(self):
        """Show the folder of the playblasts, made when there is none yet."""
        entity_uri = self.require_entity_uri()
        department_name = self.require_department_name()
        latest_path = get_latest_playblast_path(entity_uri, department_name)
        folder = (
            get_next_playblast_path(entity_uri, department_name).parent
            if latest_path is None else latest_path.parent
        )
        folder.mkdir(parents=True, exist_ok=True)
        hou.ui.showInFileBrowser(path_str(folder))
