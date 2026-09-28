"""Private, revisioned storage for the administrator's daily plans."""

from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import state_store
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


DEFAULT_PLANS_PATH = Path('/root/hysteria/state/plans/tasks.json')
QUADRANTS = {'important_urgent', 'important', 'urgent', 'later'}
STATUSES = {'todo', 'in_progress', 'done'}


class PlanItem(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    title: str = Field(min_length=1, max_length=160)
    notes: str = Field(default='', max_length=5000)
    quadrant: str
    plan_date: date
    timezone: str = Field(min_length=1, max_length=128)
    start_time: time | None = None
    due_at: datetime | None = None
    estimate_minutes: int = Field(default=30, ge=5, le=1440)
    reminder_at: datetime | None = None
    status: str = 'todo'
    created_at: datetime
    updated_at: datetime

    @field_validator('quadrant')
    @classmethod
    def validate_quadrant(cls, value):
        if value not in QUADRANTS:
            raise ValueError('invalid quadrant')
        return value

    @field_validator('status')
    @classmethod
    def validate_status(cls, value):
        if value not in STATUSES:
            raise ValueError('invalid status')
        return value

    @field_validator('timezone')
    @classmethod
    def validate_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('invalid timezone') from None
        return value

    @field_validator('due_at', 'reminder_at', 'created_at', 'updated_at')
    @classmethod
    def validate_aware_timestamp(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError('timestamp must include timezone')
        return value


class PlanStore:
    def __init__(self, path=DEFAULT_PLANS_PATH):
        self.path = Path(path)

    def _prepare_directory(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)

    @staticmethod
    def validate(items):
        if not isinstance(items, list) or len(items) > 2000:
            raise ValueError('invalid items')
        try:
            values = [PlanItem.model_validate(item).model_dump(mode='json') for item in items]
        except ValidationError as exc:
            raise ValueError('invalid plan item') from exc
        if len({item['id'] for item in values}) != len(values):
            raise ValueError('duplicate ids')
        return values

    @staticmethod
    def _revision(items):
        canonical = json.dumps(items, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        return hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    def read(self):
        raw = state_store.load_json_strict(self.path, {'schema_version': 1, 'items': []})
        if not isinstance(raw, dict) or raw.get('schema_version') != 1:
            raise ValueError('invalid plan storage')
        items = self.validate(raw.get('items'))
        return {'items': items, 'revision': self._revision(items)}

    def replace(self, items, revision):
        values = self.validate(items)
        if not isinstance(revision, str) or len(revision) != 64:
            raise ValueError('invalid revision')
        lock_path = self.path.with_name(self.path.name + '.lock')
        with state_store.file_lock(lock_path, timeout=3):
            if revision != self.read()['revision']:
                raise ValueError('conflict')
            self._prepare_directory()
            state_store.save_json(self.path, {'schema_version': 1, 'items': values})
            self.path.chmod(0o600)
            return {'items': values, 'revision': self._revision(values)}

    def due_reminders(self, now=None):
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError('current time must include timezone')
        due = []
        for item in self.read()['items']:
            reminder = item.get('reminder_at')
            if reminder and item['status'] != 'done' and datetime.fromisoformat(reminder.replace('Z', '+00:00')) <= current:
                due.append({key: item[key] for key in ('id', 'title', 'plan_date', 'reminder_at')})
        return due

    def update_reminder(self, task_id, action, *, now=None):
        if action not in {'dismiss', 'snooze', 'complete'}:
            raise ValueError('invalid reminder action')
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError('current time must include timezone')
        lock_path = self.path.with_name(self.path.name + '.lock')
        with state_store.file_lock(lock_path, timeout=3):
            snapshot = self.read()
            items = snapshot['items']
            match = next((item for item in items if item['id'] == task_id), None)
            if match is None:
                return None
            if action == 'dismiss':
                match['reminder_at'] = None
            elif action == 'snooze':
                match['reminder_at'] = (current + timedelta(minutes=10)).isoformat()
            else:
                match['reminder_at'] = None
                match['status'] = 'done'
            match['updated_at'] = current.isoformat()
            values = self.validate(items)
            self._prepare_directory()
            state_store.save_json(self.path, {'schema_version': 1, 'items': values})
            self.path.chmod(0o600)
            return next(item for item in values if item['id'] == task_id)
