from dataclasses import dataclass

@dataclass
class Asset:
    name: str
    category: str
    version: str

    @staticmethod
    def to_json(asset):
        return {
            'name': asset.name,
            'category': asset.category,
            'version': asset.version
        }

    @staticmethod
    def from_json(data):
        return Asset(
            name = data['name'],
            category = data['category'],
            version = data['version']
        )
    
    def __hash__(self):
        return hash((
            'name', self.name,
            'category', self.category,
            'version', self.version
        ))