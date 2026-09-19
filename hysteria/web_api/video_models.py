"""Small, provider-neutral value objects for the video workflow API."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class VideoSettings:
    base_url: str
    api_key: str
    provider: str = 'grok'


@dataclass(frozen=True, slots=True)
class ImageRequest:
    prompt: str
    model: str
    width: int | None = None
    height: int | None = None
    aspect_ratio: str | None = None


@dataclass(frozen=True, slots=True)
class VideoRequest:
    prompt: str
    model: str
    image_url: str | None = None
    first_frame_url: str | None = None
    last_frame_url: str | None = None
    duration: int | None = None
    aspect_ratio: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderJob:
    provider_job_id: str
    state: str = 'queued'
    asset_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderJobStatus:
    provider_job_id: str
    state: str
    asset_url: str | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class Capability:
    supported: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Capabilities:
    image_models: list[str] = field(default_factory=list)
    video_models: list[str] = field(default_factory=list)
    first_last_frame: Capability = field(default_factory=lambda: Capability(False, 'unverified'))
    video_composition: Capability = field(default_factory=lambda: Capability(False, 'unverified'))


@dataclass(frozen=True, slots=True)
class CancelResult:
    status: str


@dataclass(frozen=True, slots=True)
class ValidatedWorkflow:
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    order: list[str]
