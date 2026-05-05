from dataclasses import dataclass

@dataclass
class Project:
    name: str

    # Assets
    categories: list[str]
    assets: dict[str, list[str]]

    # Shots
    sequences: list[str]
    shots: dict[str, list[str]]

    @staticmethod
    def to_json(project):
        return {
            'name': project.name,
            'categories': project.categories,
            'assets': project.assets,
            'sequences': project.sequences,
            'shots': project.shots
        }
    
    @staticmethod
    def from_json(data):
        return Project(
            name = data['name'],
            categories = data['categories'],
            assets = data['assets'],
            sequences = data['sequences'],
            shots = data['shots']
        )

    def __hash__(self):
        return hash((
            'name', self.name,
            'categories', hash(self.categories),
            'assets', hash(self.assets),
            'sequences', hash(self.sequences),
            'shots', hash(self.shots)
        ))