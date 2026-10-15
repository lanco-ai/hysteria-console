"""Private storage for the administrator's day schedule.

A schedule has two parts that are saved independently, each with its own
revision so that editing one day never conflicts with editing the routine:

* the routine: recurring time blocks (sleep, meals, work hours...) applied by
  weekday, plus the ideal minutes per category the owner wants each day;
* days: the blocks added for one date, and the done/skipped marks on that
  date's routine blocks.
"""

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import state_store
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


DEFAULT_SCHEDULE_PATH = Path('/root/hysteria/state/plans/schedule.json')
CATEGORIES = ('rest', 'meal', 'work', 'study', 'think', 'connect', 'exercise', 'life')
BLOCK_STATUSES = ('planned', 'done', 'skipped')
MAX_ROUTINE_BLOCKS = 60
MAX_DAY_BLOCKS = 80
MAX_DAYS = 3660


def _minutes(value):
    return int(value[:2]) * 60 + int(value[3:])


def _valid_clock(value):
    if len(value) != 5 or value[2] != ':' or not value[:2].isdigit() or not value[3:].isdigit():
        raise ValueError('invalid clock time')
    if int(value[:2]) > 23 or int(value[3:]) > 59:
        raise ValueError('invalid clock time')
    return value


class _Block(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    title: str = Field(min_length=1, max_length=80)
    category: str
    start: str
    end: str

    @field_validator('category')
    @classmethod
    def validate_category(cls, value):
        if value not in CATEGORIES:
            raise ValueError('invalid category')
        return value

    @field_validator('start', 'end')
    @classmethod
    def validate_clock(cls, value):
        return _valid_clock(value)

    @model_validator(mode='after')
    def validate_span(self):
        # An end earlier than the start wraps past midnight (sleep 23:00-07:00).
        if self.start == self.end:
            raise ValueError('block must have a duration')
        return self


class RoutineBlock(_Block):
    weekdays: list[int] = Field(min_length=1, max_length=7)

    @field_validator('weekdays')
    @classmethod
    def validate_weekdays(cls, value):
        if any(day < 0 or day > 6 for day in value) or len(set(value)) != len(value):
            raise ValueError('invalid weekdays')
        return sorted(value)


class DayBlock(_Block):
    notes: str = Field(default='', max_length=500)
    status: str = 'planned'

    @field_validator('status')
    @classmethod
    def validate_status(cls, value):
        if value not in BLOCK_STATUSES:
            raise ValueError('invalid status')
        return value


class Routine(BaseModel):
    model_config = ConfigDict(extra='forbid')

    blocks: list[RoutineBlock] = Field(default_factory=list, max_length=MAX_ROUTINE_BLOCKS)
    targets: dict[str, int] = Field(default_factory=dict)

    @field_validator('targets')
    @classmethod
    def validate_targets(cls, value):
        for key, minutes in value.items():
            if key not in CATEGORIES or minutes < 0 or minutes > 1440:
                raise ValueError('invalid target')
        if sum(value.values()) > 1440:
            raise ValueError('targets exceed one day')
        return {key: value[key] for key in CATEGORIES if value.get(key)}

    @model_validator(mode='after')
    def validate_unique_ids(self):
        if len({block.id for block in self.blocks}) != len(self.blocks):
            raise ValueError('duplicate ids')
        return self


class Day(BaseModel):
    model_config = ConfigDict(extra='forbid')

    blocks: list[DayBlock] = Field(default_factory=list, max_length=MAX_DAY_BLOCKS)
    routine_status: dict[str, str] = Field(default_factory=dict)
    note: str = Field(default='', max_length=2000)

    @field_validator('routine_status')
    @classmethod
    def validate_routine_status(cls, value):
        if len(value) > MAX_ROUTINE_BLOCKS:
            raise ValueError('too many routine marks')
        for key, status in value.items():
            if not isinstance(key, str) or not key or len(key) > 64 or status not in ('done', 'skipped'):
                raise ValueError('invalid routine mark')
        return value

    @model_validator(mode='after')
    def validate_unique_ids(self):
        if len({block.id for block in self.blocks}) != len(self.blocks):
            raise ValueError('duplicate ids')
        return self


def _revision(value):
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _validated(model, value):
    try:
        return model.model_validate(value).model_dump(mode='json')
    except ValidationError as exc:
        raise ValueError('invalid schedule') from exc


def _day_key(value):
    if not isinstance(value, str):
        raise ValueError('invalid date')
    return date.fromisoformat(value).isoformat()


def _empty_day():
    return {'blocks': [], 'routine_status': {}, 'note': ''}


def _is_empty_day(day):
    return not day['blocks'] and not day['routine_status'] and not day['note']


def _carried_over(days, day_key):
    """The part of the previous day that runs past midnight into ``day_key``.

    Its overnight blocks and routine marks let the day show the morning part
    of, say, the previous night's sleep. Read-only: they are edited on the
    day they start.
    """
    try:
        previous = days.get((date.fromisoformat(day_key) - timedelta(days=1)).isoformat())
    except OverflowError:
        previous = None
    if not previous:
        return {'blocks': [], 'routine_status': {}}
    return {
        'blocks': [
            dict(block) for block in previous['blocks']
            if 0 < _minutes(block['end']) < _minutes(block['start'])
        ],
        'routine_status': dict(previous['routine_status']),
    }


class ScheduleStore:
    def __init__(self, path=DEFAULT_SCHEDULE_PATH):
        self.path = Path(path)

    @property
    def _lock_path(self):
        return self.path.with_name(self.path.name + '.lock')

    def _load(self):
        raw = state_store.load_json_strict(self.path, {'schema_version': 1, 'routine': {}, 'days': {}})
        if not isinstance(raw, dict) or raw.get('schema_version') != 1 or not isinstance(raw.get('days', {}), dict):
            raise ValueError('invalid schedule storage')
        routine = _validated(Routine, raw.get('routine') or {})
        days = {_day_key(key): _validated(Day, value) for key, value in raw.get('days', {}).items()}
        return routine, days

    def _save(self, routine, days):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        state_store.save_json(self.path, {'schema_version': 1, 'routine': routine, 'days': dict(sorted(days.items()))})
        self.path.chmod(0o600)

    @staticmethod
    def _view(day_key, routine, days):
        day = days.get(day_key) or _empty_day()
        return {
            'date': day_key,
            'routine': routine,
            'routine_revision': _revision(routine),
            'day': day,
            'day_revision': _revision(day),
            'previous_day': _carried_over(days, day_key),
        }

    def read(self, day_value):
        key = _day_key(day_value)
        routine, days = self._load()
        return self._view(key, routine, days)

    @staticmethod
    def _check_revision(revision, current):
        if not isinstance(revision, str) or len(revision) != 64:
            raise ValueError('invalid revision')
        if revision != _revision(current):
            raise ValueError('conflict')

    def replace_day(self, day_value, value, revision):
        key = _day_key(day_value)
        day = _validated(Day, value)
        with state_store.file_lock(self._lock_path, timeout=3):
            routine, days = self._load()
            self._check_revision(revision, days.get(key) or _empty_day())
            if _is_empty_day(day):
                days.pop(key, None)
            else:
                days[key] = day
            if len(days) > MAX_DAYS:
                raise ValueError('too many days')
            self._save(routine, days)
            return self._view(key, routine, days)

    def replace_routine(self, day_value, value, revision):
        key = _day_key(day_value)
        routine = _validated(Routine, value)
        with state_store.file_lock(self._lock_path, timeout=3):
            current, days = self._load()
            self._check_revision(revision, current)
            self._save(routine, days)
            return self._view(key, routine, days)

