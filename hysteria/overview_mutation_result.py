"""Minimal domain outcomes for overview traffic and status operations."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class OverviewMutationResult:
    outcome: Literal['success', 'invalid', 'not_found', 'conflict']
    username: str = ''
    code: str = ''
    day: int | None = None
    disabled_until: str = ''
