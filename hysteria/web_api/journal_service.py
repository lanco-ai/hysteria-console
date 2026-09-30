"""Independent, private storage for the administrator's life and learning journal."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import state_store
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


DEFAULT_JOURNAL_PATH = Path('/root/hysteria/state/journal/entries.json')
JournalKind = Literal['life', 'ai_storage', 'paper', 'ielts', 'thought', 'daily_review', 'weekly_review']
IeltsSkill = Literal['listening', 'speaking', 'reading', 'writing']
TEXT_FIELDS = ('title', 'body', 'question', 'explanation', 'source', 'uncertainty', 'next_check',
               'material', 'raw_result', 'correction', 'previous_view', 'trigger_evidence',
               'current_view', 'learning_state', 'evidence', 'takeaway')
CONTENT_FIELDS = {
    'life': (),
    'ai_storage': ('question', 'explanation', 'source', 'uncertainty', 'next_check'),
    'paper': ('question', 'explanation', 'source', 'uncertainty', 'next_check'),
    'ielts': ('material', 'raw_result', 'correction', 'next_check'),
    'thought': ('previous_view', 'trigger_evidence', 'current_view', 'next_check'),
    'daily_review': ('learning_state', 'explanation', 'evidence', 'takeaway', 'next_check'),
    'weekly_review': ('evidence', 'takeaway', 'next_check'),
}


class JournalInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    kind: JournalKind
    occurred_at: datetime
    ended_at: datetime | None = None
    timezone: str = Field(min_length=1, max_length=128)
    title: str = Field(default='', max_length=160)
    body: str = Field(default='', max_length=12000)
    question: str = Field(default='', max_length=2000)
    explanation: str = Field(default='', max_length=4000)
    source: str = Field(default='', max_length=2000)
    uncertainty: str = Field(default='', max_length=2000)
    next_check: str = Field(default='', max_length=2000)
    skill: IeltsSkill | None = None
    material: str = Field(default='', max_length=2000)
    raw_result: str = Field(default='', max_length=4000)
    correction: str = Field(default='', max_length=4000)
    previous_view: str = Field(default='', max_length=2000)
    trigger_evidence: str = Field(default='', max_length=2000)
    current_view: str = Field(default='', max_length=2000)
    learning_state: str = Field(default='', max_length=1000)
    evidence: str = Field(default='', max_length=4000)
    takeaway: str = Field(default='', max_length=2000)

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('invalid timezone') from None
        return value

    @field_validator('occurred_at', 'ended_at')
    @classmethod
    def aware_timestamp(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError('timestamp must include timezone')
        return value

    @model_validator(mode='after')
    def ordered_times(self):
        if self.ended_at is not None and self.ended_at < self.occurred_at:
            raise ValueError('end must follow start')
        if self.ended_at is not None and self.ended_at - self.occurred_at > timedelta(days=7):
            raise ValueError('duration too long')
        if self.kind != 'ielts' and self.skill is not None:
            raise ValueError('skill only applies to IELTS')
        if not self.body and not any(getattr(self, field) for field in CONTENT_FIELDS[self.kind]):
            raise ValueError('record content required')
        return self


class JournalUpdate(JournalInput):
    revision: int = Field(ge=1)


class JournalRecord(JournalInput):
    chat_source: dict[str, str] | None = None
    id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    local_date: date
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)

    @field_validator('created_at', 'updated_at')
    @classmethod
    def aware_metadata(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('timestamp must include timezone')
        return value

    @model_validator(mode='after')
    def correct_local_date(self):
        if self.local_date != self.occurred_at.astimezone(ZoneInfo(self.timezone)).date():
            raise ValueError('invalid local date')
        return self


class JournalStore:
    def __init__(self, path=DEFAULT_JOURNAL_PATH):
        self.path = Path(path)

    def _prepare_directory(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)

    def _lock_path(self):
        self._prepare_directory()
        lock = self.path.with_name(self.path.name + '.lock')
        # file_lock opens an existing lock file; establish its private mode first.
        import os
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        lock.chmod(0o600)
        return lock

    def _all(self):
        raw = state_store.load_json_strict(self.path, {'schema_version': 1, 'items': []})
        if not isinstance(raw, dict) or raw.get('schema_version') != 1 or not isinstance(raw.get('items'), list):
            raise ValueError('invalid journal storage')
        if len(raw['items']) > 20000:
            raise ValueError('journal too large')
        try:
            records = [JournalRecord.model_validate(item).model_dump(mode='json') for item in raw['items']]
        except ValidationError as exc:
            raise ValueError('invalid journal storage') from exc
        if len({item['id'] for item in records}) != len(records):
            raise ValueError('duplicate journal ids')
        return records

    def _save(self, records):
        self._prepare_directory()
        state_store.save_json(self.path, {'schema_version': 1, 'items': records})
        self.path.chmod(0o600)

    def read(self, *, date=None, kind=None, q=None, start=None, end=None):
        if date is not None:
            date = date_from_iso(date)
        if start is not None:
            start = date_from_iso(start)
        if end is not None:
            end = date_from_iso(end)
        if kind is not None and kind not in JournalKind.__args__:
            raise ValueError('invalid kind')
        if q is not None and (not isinstance(q, str) or len(q) > 200):
            raise ValueError('invalid search')
        needle = (q or '').casefold()
        records = []
        for item in self._all():
            local_day = date_from_iso(item['local_date'])
            if date is not None and local_day != date or start is not None and local_day < start or end is not None and local_day > end:
                continue
            if kind is not None and item['kind'] != kind:
                continue
            if needle and not any(needle in str(item.get(field) or '').casefold() for field in TEXT_FIELDS):
                continue
            records.append(item)
        records.sort(key=lambda item: (datetime.fromisoformat(item['occurred_at']).timestamp(), item['id']), reverse=True)
        return {'items': records}

    def create(self, values, idempotency_key=None):
        return self._create(values, idempotency_key=idempotency_key)

    def create_from_chat(self, values, provenance):
        return self._create(values, provenance)

    def _create(self, values, provenance=None, idempotency_key=None):
        item = JournalInput.model_validate(values)
        record_id = str(uuid4())
        if idempotency_key is not None:
            key = UUID(idempotency_key)
            if key.version != 4 or str(key) != idempotency_key:
                raise ValueError('invalid create key')
            record_id = str(key)
        now = datetime.now(timezone.utc).isoformat()
        record = {
            **item.model_dump(mode='json'), 'id': record_id,
            'local_date': item.occurred_at.astimezone(ZoneInfo(item.timezone)).date().isoformat(),
            'created_at': now, 'updated_at': now, 'revision': 1,
        }
        if provenance:
            record['chat_source'] = provenance
        with state_store.file_lock(self._lock_path(), timeout=3):
            records = self._all()
            # A lost response must not create another autosaved entry on retry.
            if idempotency_key is not None:
                for old in records:
                    if old['id'] == record_id:
                        return {'item': old}
            if provenance:
                for old in records:
                    source = old.get('chat_source') or {}
                    if source.get('conversation_id') == provenance['conversation_id'] and source.get('message_id') == provenance['message_id']:
                        return {'item': old, 'already_saved': True}
            records.append(record)
            self._save(records)
        return {'item': record}

    def update(self, record_id, values):
        update = JournalUpdate.model_validate(values)
        with state_store.file_lock(self._lock_path(), timeout=3):
            records = self._all()
            for index, old in enumerate(records):
                if old['id'] == record_id:
                    if update.revision != old['revision']:
                        raise ValueError('conflict')
                    payload = update.model_dump(mode='json', exclude={'revision'})
                    record = {
                        **payload, 'id': record_id,
                        'local_date': update.occurred_at.astimezone(ZoneInfo(update.timezone)).date().isoformat(),
                        'created_at': old['created_at'], 'updated_at': datetime.now(timezone.utc).isoformat(),
                        'revision': old['revision'] + 1,
                    }
                    if old.get('chat_source'):
                        record['chat_source'] = old['chat_source']
                    records[index] = record
                    self._save(records)
                    return {'item': record}
        raise KeyError(record_id)

    def delete(self, record_id, revision):
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError('invalid revision')
        with state_store.file_lock(self._lock_path(), timeout=3):
            records = self._all()
            for index, old in enumerate(records):
                if old['id'] == record_id:
                    if revision != old['revision']:
                        raise ValueError('conflict')
                    del records[index]
                    self._save(records)
                    return {'deleted': record_id}
        raise KeyError(record_id)

    def week_summary(self, week_start):
        first = date_from_iso(week_start)
        if first.weekday() != 0:
            raise ValueError('week must start Monday')
        last = first + timedelta(days=6)
        records = self.read(start=first.isoformat(), end=last.isoformat())['items']
        counts = {}
        for item in records:
            counts[item['kind']] = counts.get(item['kind'], 0) + 1
        return {'week_start': first.isoformat(), 'week_end': last.isoformat(), 'counts': counts,
                'total': len(records), 'items': records}


def date_from_iso(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError('invalid date')
    return date.fromisoformat(value)
