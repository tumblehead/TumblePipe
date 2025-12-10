"""Preset management for job submission dialog."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
import os

from tumblehead.util.io import load_json, store_json


@dataclass
class PresetInfo:
    """Information about a preset."""
    name: str
    job_type: str
    path: Path


class PresetManager:
    """Manages saving and loading job submission presets."""

    def __init__(self, presets_dir: Optional[Path] = None):
        """Initialize preset manager.

        Args:
            presets_dir: Directory for storing presets. If None, uses
                         {TH_CONFIG_PATH}/presets/job_submission/
        """
        if presets_dir is None:
            config_path = os.environ.get('TH_CONFIG_PATH', '')
            if config_path:
                presets_dir = Path(config_path) / 'presets' / 'job_submission'
            else:
                # Fallback to user home
                presets_dir = Path.home() / '.tumblehead' / 'presets' / 'job_submission'

        self._presets_dir = presets_dir

    def _ensure_dir(self, job_type: str) -> Path:
        """Ensure preset directory exists for job type."""
        dir_path = self._presets_dir / job_type
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path

    def list_presets(self, job_type: Optional[str] = None) -> list[PresetInfo]:
        """List available presets.

        Args:
            job_type: Filter by job type. If None, returns all presets.

        Returns:
            List of PresetInfo objects.
        """
        presets = []

        if job_type:
            job_types = [job_type]
        else:
            # List all job type directories
            if not self._presets_dir.exists():
                return []
            job_types = [d.name for d in self._presets_dir.iterdir() if d.is_dir()]

        for jt in job_types:
            job_dir = self._presets_dir / jt
            if not job_dir.exists():
                continue

            for path in job_dir.glob("*.json"):
                try:
                    data = load_json(path)
                    if data:
                        presets.append(PresetInfo(
                            name=data.get('name', path.stem),
                            job_type=jt,
                            path=path
                        ))
                except Exception:
                    continue

        return sorted(presets, key=lambda p: (p.job_type, p.name))

    def load_preset(self, job_type: str, name: str) -> Optional[dict[str, Any]]:
        """Load a preset by name.

        Args:
            job_type: The job type.
            name: Preset name.

        Returns:
            Preset data dict with 'name', 'job_type', and 'global_defaults' keys,
            or None if not found.
        """
        path = self._presets_dir / job_type / f"{name}.json"
        if not path.exists():
            return None

        try:
            return load_json(path)
        except Exception:
            return None

    def save_preset(self, job_type: str, name: str, defaults: dict[str, Any]) -> bool:
        """Save a preset.

        Args:
            job_type: The job type.
            name: Preset name.
            defaults: Dict of column_key -> default_value.

        Returns:
            True if saved successfully.
        """
        try:
            dir_path = self._ensure_dir(job_type)
            path = dir_path / f"{name}.json"

            data = {
                'name': name,
                'job_type': job_type,
                'version': 1,
                'global_defaults': defaults
            }

            store_json(path, data)
            return True
        except Exception:
            return False

    def delete_preset(self, job_type: str, name: str) -> bool:
        """Delete a preset.

        Args:
            job_type: The job type.
            name: Preset name.

        Returns:
            True if deleted successfully.
        """
        path = self._presets_dir / job_type / f"{name}.json"
        if path.exists():
            try:
                path.unlink()
                return True
            except Exception:
                return False
        return False

    def rename_preset(self, job_type: str, old_name: str, new_name: str) -> bool:
        """Rename a preset.

        Args:
            job_type: The job type.
            old_name: Current preset name.
            new_name: New preset name.

        Returns:
            True if renamed successfully.
        """
        old_path = self._presets_dir / job_type / f"{old_name}.json"
        new_path = self._presets_dir / job_type / f"{new_name}.json"

        if not old_path.exists() or new_path.exists():
            return False

        try:
            data = load_json(old_path)
            if data:
                data['name'] = new_name
                store_json(new_path, data)
                old_path.unlink()
                return True
        except Exception:
            pass

        return False

    def get_preset_defaults(self, job_type: str, name: str) -> dict[str, Any]:
        """Get just the defaults dict from a preset.

        Args:
            job_type: The job type.
            name: Preset name.

        Returns:
            Dict of column_key -> default_value, or empty dict if not found.
        """
        data = self.load_preset(job_type, name)
        if data:
            return data.get('global_defaults', {})
        return {}


# Global instance
_preset_manager: Optional[PresetManager] = None


def get_preset_manager() -> PresetManager:
    """Get the global preset manager instance."""
    global _preset_manager
    if _preset_manager is None:
        _preset_manager = PresetManager()
    return _preset_manager
