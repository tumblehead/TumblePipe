from pathlib import Path

from tumblehead.api import path_str, to_windows_path
from tumblehead.apps import app

def _find_renderman_denoiser():
    pixar_path = Path('/mnt/c/Program Files/Pixar')
    for dir_path in pixar_path.iterdir():
        if not dir_path.is_dir(): continue
        if not dir_path.name.startswith('RenderManProServer-'): continue
        denoiser_path = dir_path / 'bin' / 'denoise_batch.exe'
        if not denoiser_path.exists(): return None
        return denoiser_path
    return None

class Denoiser:
    def __init__(self):
        
        # Find the denoiser executable
        self._denoiser = _find_renderman_denoiser()
        assert self._denoiser is not None, 'RenderMan denoiser not found'

    def run(self, config_path):
        return app.run([
            path_str(self._denoiser), '-cf', '-t',
            '-j', path_str(to_windows_path(config_path))
        ])