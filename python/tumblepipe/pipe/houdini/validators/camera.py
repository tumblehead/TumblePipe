"""Camera validation for USD render stages."""

from tumblepipe.pipe.houdini import util
from .base import ValidationResult


def validate_cameras(root) -> ValidationResult:
    """Validate that cameras exist and are properly configured.

    Checks:
    - At least one Camera prim exists in the stage
    - Render camera (from RenderSettings) exists and is valid
    - Camera clipping range is valid (near < far)
    - Camera has focal length attribute
    """
    result = ValidationResult()
    stage = root.GetStage()
    if stage is None:
        result.add_warning("No stage available for validation")
        return result

    # Find all cameras in the stage
    camera_paths = util.list_cameras(root)

    if not camera_paths:
        result.add_error(
            "No Camera prims found in stage",
            suggestion=(
                "Add a camera under /cameras/ via a Camera LOP, or sublayer in "
                "the layout/animation department where the shot camera lives."
            ),
        )
        return result

    # Check render camera from RenderSettings
    settings_prim = stage.GetPrimAtPath('/Render/rendersettings')
    if settings_prim.IsValid():
        camera_rel = settings_prim.GetRelationship('camera')
        if camera_rel.IsValid():
            camera_targets = camera_rel.GetTargets()
            if camera_targets:
                render_camera_path = str(camera_targets[0])
                if render_camera_path not in camera_paths:
                    result.add_error(
                        f"Render camera path not found in stage: {render_camera_path}",
                        '/Render/rendersettings',
                        suggestion=(
                            "Update the Camera Path on the Render Settings LOP "
                            "to point at an existing camera, or add the missing "
                            "camera to the stage."
                        ),
                    )

    # Validate each camera's attributes
    for camera_path in camera_paths:
        camera_prim = stage.GetPrimAtPath(camera_path)
        if not camera_prim.IsValid():
            continue

        # Check clipping range
        clipping_attr = camera_prim.GetAttribute('clippingRange')
        if clipping_attr.IsValid():
            clipping_range = clipping_attr.Get()
            if clipping_range is not None:
                near, far = clipping_range[0], clipping_range[1]
                if near >= far:
                    result.add_warning(
                        f"Camera has invalid clipping range: near ({near}) >= far ({far})",
                        camera_path,
                        suggestion=(
                            "Set near < far on the camera LOP (typical: near=0.1, "
                            "far=10000)."
                        ),
                    )
                if near <= 0:
                    result.add_warning(
                        f"Camera has non-positive near clipping plane: {near}",
                        camera_path,
                        suggestion=(
                            "Near clip must be > 0 (USD requirement). Set the "
                            "near plane on the camera LOP — typical value 0.1."
                        ),
                    )

        # Check focal length (warning if missing)
        focal_attr = camera_prim.GetAttribute('focalLength')
        if not focal_attr.IsValid():
            result.add_warning(
                "Camera missing 'focalLength' attribute",
                camera_path,
                suggestion=(
                    "Set Focal Length on the camera LOP (default 50mm; typical "
                    "cinematic 35–85mm)."
                ),
            )
        else:
            focal_length = focal_attr.Get()
            if focal_length is not None and focal_length <= 0:
                result.add_warning(
                    f"Camera has invalid focal length: {focal_length}",
                    camera_path,
                    suggestion=(
                        "Focal Length must be > 0. Set a sensible value on the "
                        "camera LOP (typical: 35–85mm)."
                    ),
                )

    return result
