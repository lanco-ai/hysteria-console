"""Explicit public models for administrator residential-egress state."""

from pydantic import StrictBool, StrictStr

from .models import PublicModel


class LandingHealthResponse(PublicModel):
    status: StrictStr | None = None
    observed_ip: StrictStr | None = None
    checked_at: StrictStr | None = None
    error_code: StrictStr | None = None


class LandingNodeResponse(PublicModel):
    id: StrictStr
    name: StrictStr
    exit_ip: StrictStr
    isp: StrictStr
    region: StrictStr
    enabled: StrictBool
    health: LandingHealthResponse | None = None


class LandingAccessResponse(PublicModel):
    user: StrictStr
    revision: StrictStr
    allowed_ids: list[StrictStr]


class AdminLandingResponse(PublicModel):
    ts: StrictStr
    revision: StrictStr
    nodes: list[LandingNodeResponse]
    users: list[LandingAccessResponse]
