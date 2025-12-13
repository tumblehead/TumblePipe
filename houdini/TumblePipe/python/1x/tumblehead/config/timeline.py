from dataclasses import dataclass

from tumblehead.util.uri import Uri
from tumblehead.api import default_client

api = default_client()

@dataclass(frozen=True)
class BlockRange:
    first_frame: int
    last_frame: int
    step_size: int = 1
    
    def __post_init__(self):
        if self.step_size <= 0:
            raise ValueError(f"step_size must be positive, got {self.step_size}")
        if self.first_frame > self.last_frame:
            raise ValueError(f"first_frame ({self.first_frame}) cannot be greater than last_frame ({self.last_frame})")

    def timecode(self, frame: int) -> float:
        if self.first_frame == self.last_frame: return 1.0
        return (
            (frame - self.first_frame) /
            (self.last_frame - self.first_frame)
        )
    
    def frame(self, timecode: float) -> int:
        assert 0 <= timecode <= 1, f'Invalid timecode: {timecode}'
        frames = list(self)
        index = int((len(frames) - 1) * timecode)
        return frames[index]
    
    def __len__(self):
        return (self.last_frame - self.first_frame + 1) // self.step_size

    def __iter__(self):
        return iter(range(
            self.first_frame,
            self.last_frame + 1,
            self.step_size
        ))
    
    def __contains__(self, obj):
        if isinstance(obj, int):
            if obj < self.first_frame: return False
            if obj > self.last_frame: return False
            if (obj - self.first_frame) % self.step_size != 0: return False
            return True
        if isinstance(obj, BlockRange):
            if self.step_size != obj.step_size: return False
            if obj.first_frame < self.first_frame: return False
            if obj.last_frame > self.last_frame: return False
            return True
        assert False, f'Invalid object: {obj}'
    
    def __str__(self):
        return f'{self.first_frame}-{self.last_frame}x{self.step_size}'
    
    def __eq__(self, other):
        if not isinstance(other, BlockRange): return False
        if self.first_frame != other.first_frame: return False
        if self.last_frame != other.last_frame: return False
        if self.step_size != other.step_size: return False
        return True

@dataclass(frozen=True)
class FrameRange:
    start_frame: int
    end_frame: int
    start_roll: int
    end_roll: int
    step_size: int = 1
    
    def __post_init__(self):
        if self.step_size <= 0:
            raise ValueError(f"step_size must be positive, got {self.step_size}")
        if self.start_frame > self.end_frame:
            raise ValueError(f"start_frame ({self.start_frame}) cannot be greater than end_frame ({self.end_frame})")
        if self.start_roll < 0:
            raise ValueError(f"start_roll must be non-negative, got {self.start_roll}")
        if self.end_roll < 0:
            raise ValueError(f"end_roll must be non-negative, got {self.end_roll}")

    def play_range(self) -> BlockRange:
        return BlockRange(
            self.start_frame,
            self.end_frame,
            self.step_size
        )

    def full_range(self) -> BlockRange:
        first_frame = self.start_frame - self.start_roll
        last_frame = self.end_frame + self.end_roll
        
        if first_frame <= 0:
            raise ValueError(f"full_range first_frame ({first_frame}) must be positive. "
                           f"start_frame={self.start_frame}, start_roll={self.start_roll}")
        if first_frame > last_frame:
            raise ValueError(f"full_range first_frame ({first_frame}) cannot be greater than last_frame ({last_frame}). "
                           f"start_frame={self.start_frame}, end_frame={self.end_frame}, "
                           f"start_roll={self.start_roll}, end_roll={self.end_roll}")
        
        return BlockRange(first_frame, last_frame, self.step_size)
    
    def timecode(self, frame: int) -> float:
        return self.full_range().timecode(frame)
    
    def frame(self, timecode: float) -> int:
        return self.full_range().frame(timecode)
    
    def __len__(self):
        return len(self.full_range())

    def __iter__(self):
        return iter(self.full_range())
    
    def __contains__(self, obj):
        return obj in self.full_range()
    
    def __str__(self):
        return f'{self.start_frame}-{self.end_frame}|{self.start_roll}-{self.end_roll}x{self.step_size}'
    
    def __eq__(self, other):
        if not isinstance(other, FrameRange): return False
        if self.start_frame != other.start_frame: return False
        if self.end_frame != other.end_frame: return False
        if self.start_roll != other.start_roll: return False
        if self.end_roll != other.end_roll: return False
        if self.step_size != other.step_size: return False
        return True

def get_frame_range(uri: Uri) -> FrameRange | None:
    properties = api.config.get_properties(uri)
    if properties is None: return None
    if 'frame_start' not in properties: return None
    if 'frame_end' not in properties: return None
    if 'roll_start' not in properties: return None
    if 'roll_end' not in properties: return None
    return FrameRange(
        properties['frame_start'],
        properties['frame_end'],
        properties['roll_start'],
        properties['roll_end']
    )

def get_fps(uri: Uri | None = None) -> int | None:
    """Get FPS with optional entity override.

    Args:
        uri: Entity URI for entity-specific FPS, or None for project default

    Returns:
        FPS value or None if not configured
    """
    if uri is None:
        uri = Uri.parse_unsafe('config:/project')

    properties = api.config.get_properties(uri)
    if properties is None: return None
    if 'fps' not in properties: return None
    return int(properties['fps'])