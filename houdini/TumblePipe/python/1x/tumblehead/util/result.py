from typing import Generic, TypeVar
from dataclasses import dataclass

T = TypeVar('T')

class Result: pass

@dataclass
class Value(Generic[T]):
    value: T

@dataclass
class Error:
    message: str