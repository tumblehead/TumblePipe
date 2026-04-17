from dataclasses import dataclass, field

@dataclass
class Asset:
    name: str
    category: str
    version: str
    variants: list[str] = field(default_factory=list)  # Custom variants (default is implicit)

    @staticmethod
    def to_json(asset):
        result = {
            'name': asset.name,
            'category': asset.category,
            'version': asset.version
        }
        if asset.variants:
            result['variants'] = asset.variants
        return result

    @staticmethod
    def from_json(data):
        return Asset(
            name = data['name'],
            category = data['category'],
            version = data['version'],
            variants = data.get('variants', [])
        )

    def __hash__(self):
        return hash((
            'name', self.name,
            'category', self.category,
            'version', self.version,
            'variants', tuple(self.variants)
        ))