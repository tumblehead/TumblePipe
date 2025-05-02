from dataclasses import dataclass

WORD_ALPHABET = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_')

@dataclass(frozen=True)
class BlockRange:
    first_frame: int
    last_frame: int

    def timecode(self, frame_index: int) -> float:
        if self.first_frame == self.last_frame: return 1.0
        return (frame_index - self.first_frame) / (self.last_frame - self.first_frame)
    
    def __len__(self):
        return self.last_frame - self.first_frame + 1

    def __iter__(self):
        return iter(range(self.first_frame, self.last_frame + 1))
    
    def __contains__(self, obj):
        if isinstance(obj, int):
            return self.first_frame <= obj <= self.last_frame
        if isinstance(obj, BlockRange):
            if obj.first_frame < self.first_frame: return False
            if obj.last_frame > self.last_frame: return False
            return True
        assert False, f'Invalid object: {obj}'
    
    def __str__(self):
        return f'{self.first_frame}-{self.last_frame}'
    
    def __eq__(self, other):
        if not isinstance(other, BlockRange): return False
        if self.first_frame != other.first_frame: return False
        if self.last_frame != other.last_frame: return False
        return True

@dataclass(frozen=True)
class FrameRange:
    start_frame: int
    end_frame: int
    start_roll: int
    end_roll: int

    def play_range(self) -> BlockRange:
        return BlockRange(self.start_frame, self.end_frame)

    def full_range(self) -> BlockRange:
        first_frame = self.start_frame - self.start_roll
        last_frame = self.end_frame + self.end_roll
        return BlockRange(first_frame, last_frame)
    
    def timecode(self, frame_index: int) -> float:
        return self.full_range().timecode(frame_index)
    
    def __len__(self):
        return len(self.full_range())

    def __iter__(self):
        return iter(self.full_range())
    
    def __contains__(self, obj):
        return obj in self.full_range()
    
    def __str__(self):
        return f'{self.start_frame}-{self.end_frame}|{self.start_roll}-{self.end_roll}'
    
    def __eq__(self, other):
        if not isinstance(other, FrameRange): return False
        if self.start_frame != other.start_frame: return False
        if self.end_frame != other.end_frame: return False
        if self.start_roll != other.start_roll: return False
        if self.end_roll != other.end_roll: return False
        return True

class ConfigConvention:
    def list_sequence_names(self):
        raise NotImplementedError()
    
    def list_shot_names(self, sequence_name):
        raise NotImplementedError()
    
    def list_category_names(self):
        raise NotImplementedError()
    
    def list_asset_names(self, category_name):
        raise NotImplementedError()
    
    def list_kit_category_names(self):
        raise NotImplementedError()

    def list_kit_names(self):
        raise NotImplementedError()
    
    def list_asset_department_names(self):
        raise NotImplementedError()
    
    def list_shot_department_names(self):
        raise NotImplementedError()
    
    def list_kit_department_names(self):
        raise NotImplementedError()
    
    def list_render_department_names(self):
        raise NotImplementedError()
    
    def list_render_layer_names(self, sequence_name, shot_name):
        raise NotImplementedError()
    
    def get_frame_range(self, sequence_name, shot_name) -> FrameRange:
        raise NotImplementedError()
    
    def list_asset_procedural_names(self, category, asset):
        raise NotImplementedError()

    def list_kit_procedural_names(self, category, kit):
        raise NotImplementedError()
    
    def list_shot_asset_procedural_names(
        self,
        sequence_name,
        shot_name,
        category,
        asset
        ):
        raise NotImplementedError()
    
    def list_shot_kit_procedural_names(
        self,
        sequence_name,
        shot_name,
        category,
        kit
        ):
        raise NotImplementedError()
    
    def add_sequence_name(self, sequence_name):
        raise NotImplementedError()

    def add_shot_name(self, sequence_name, shot_name):
        raise NotImplementedError()
    
    def add_category_name(self, category_name):
        raise NotImplementedError()
    
    def add_asset_name(self, category_name, asset_name):
        raise NotImplementedError()
    
    def add_kit_category_name(self, kit_category_name):
        raise NotImplementedError()
    
    def add_kit_name(self, kit_category_name, kit_name):
        raise NotImplementedError()
    
    def remove_sequence_name(self, sequence_name):
        raise NotImplementedError()
    
    def remove_shot_name(self, sequence_name, shot_name):
        raise NotImplementedError()
    
    def remove_category_name(self, category_name):
        raise NotImplementedError()
    
    def remove_asset_name(self, category_name, asset_name):
        raise NotImplementedError()
    
    def remove_kit_category_name(self, kit_category_name):
        raise NotImplementedError()
    
    def remove_kit_name(self, kit_category_name, kit_name):
        raise NotImplementedError()

    def is_valid_path(self, path):
        if ':' not in path: return False
        purpose, resource = path.split(':', 1)
        if not set(purpose).issubset(WORD_ALPHABET): return False
        if ':' in resource: return False
        if not resource.startswith('/'): return False
        for part in filter(lambda part: len(part) > 0, resource.split('/')[1:]):
            if not set(part).issubset(WORD_ALPHABET): return False
        return True
    
    def parse_path(self, path):
        purpose, path = path.split(':', 1)
        return purpose, path.split('/')[1:]
    
    def resolve(self, path):
        raise NotImplementedError()