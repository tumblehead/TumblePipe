from typing import Optional
from pathlib import Path
import json

def load_json(path: Path) -> Optional[dict]:
    if not path.exists(): return None
    if not path.is_file(): return None
    if path.suffix != '.json': return None
    with path.open('r') as file:
        return json.load(file)

def store_json(path: Path, data: dict):
    path.parent.mkdir(parents = True, exist_ok = True)
    with path.open('w') as file:
        json.dump(data, file, indent = 4)