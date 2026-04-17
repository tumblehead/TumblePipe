from dataclasses import dataclass

from .asset import Asset

@dataclass
class Instance:
    name: str
    asset: Asset
    muted: bool

    @staticmethod
    def to_json(instance):
        return {
            'name': instance.name,
            'asset': Asset.to_json(instance.asset),
            'muted': instance.muted
        }

    @staticmethod
    def from_json(data):
        return Instance(
            name = data['name'],
            asset = Asset.from_json(data['asset']),
            muted = data['muted']
        )
    
    def __hash__(self):
        return hash((
            'name', self.name,
            'asset', hash(self.asset),
            'muted', self.muted
        ))

@dataclass
class Division:
    name: str
    assets: list[Instance]

    @staticmethod
    def to_json(division):
        return {
            'name': division.name,
            'assets': [
                Instance.to_json(asset)
                for asset in division.assets
            ]
        }
    
    @staticmethod
    def from_json(data):
        return Division(
            name = data['name'],
            assets = [
                Instance.from_json(asset)
                for asset in data['assets']
            ]
        )
    
    def __hash__(self):
        return hash((
            'name', self.name,
            'assets', hash(tuple(self.assets))
        ))

@dataclass
class Shot:
    sequence: str
    shot: str
    version: str
    frame_range: tuple[int, int]
    scene_layers: list[str]
    variants: list[str]  # Renamed from render_layers
    divisions: list[Division]

    @staticmethod
    def to_json(shot):
        return {
            'sequence': shot.sequence,
            'shot': shot.shot,
            'version': shot.version,
            'frame_range': shot.frame_range,
            'scene_layers': shot.scene_layers,
            'variants': shot.variants,
            'divisions': [
                Division.to_json(division)
                for division in shot.divisions
            ]
        }

    @staticmethod
    def from_json(data):
        return Shot(
            sequence = data['sequence'],
            shot = data['shot'],
            version = data['version'],
            frame_range = data['frame_range'],
            scene_layers = data.get('scene_layers', []),
            variants = data.get('variants', []),
            divisions = [
                Division.from_json(division)
                for division in data['divisions']
            ]
        )

    def __hash__(self):
        return hash((
            'sequence', self.sequence,
            'shot', self.shot,
            'version', self.version,
            'frame_range', self.frame_range,
            'scene_layers', tuple(self.scene_layers),
            'variants', tuple(self.variants),
            'divisions', hash(tuple(self.divisions))
        ))