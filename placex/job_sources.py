"""Provider adapters for real job data; this module never scrapes web pages."""
from __future__ import annotations

import logging
import json
from datetime import datetime, timezone
from typing import Literal, Protocol
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError

logger = logging.getLogger(__name__)
MAX_JOB_SOURCE_RESPONSE_BYTES = 2 * 1024 * 1024


class JobProviderError(RuntimeError):
    """A configured job source failed or returned invalid data."""


class JobSearchFilters(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    remote: bool | None = None
    experience_level: str | None = Field(default=None, max_length=100)
    skill: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=20, ge=1, le=50)


class JobListing(BaseModel):
    model_config = ConfigDict(extra="ignore")

    external_id: str = Field(min_length=1, max_length=200)
    source_url: HttpUrl
    company_name: str = Field(min_length=1, max_length=200)
    role_title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=50_000)
    location: str | None = Field(default=None, max_length=300)
    employment_type: str | None = Field(default=None, max_length=100)
    experience_level: str | None = Field(default=None, max_length=100)
    experience_requirements: str | None = Field(default=None, max_length=2000)
    education_requirements: str | None = Field(default=None, max_length=2000)
    skills: list[str] = Field(default_factory=list, max_length=100)
    is_remote: bool = False
    is_verified: bool = False
    status: Literal["ACTIVE", "EXPIRED", "UNAVAILABLE"] = "ACTIVE"
    expires_at: datetime | None = None


class JobProvider(Protocol):
    name: str

    def search(self, filters: JobSearchFilters) -> list[JobListing]:
        """Return source-provided listings without synthesizing or scraping data."""

    def close(self) -> None:
        """Release provider resources."""


class ConfiguredJSONJobProvider:
    """Adapter for an explicitly configured, permitted JSON job API or feed."""

    def __init__(
        self,
        *,
        name: str,
        endpoint: str,
        api_key: str | None = None,
        timeout_s: float = 10.0,
    ):
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("job provider URL must be an absolute HTTP(S) URL")
        if timeout_s <= 0:
            raise ValueError("job provider timeout must be positive")
        if api_key and parsed.scheme != "https":
            raise ValueError("job provider API keys require an HTTPS endpoint")
        self.name = name.strip()
        if not self.name:
            raise ValueError("job provider name must not be blank")
        self.endpoint = endpoint
        headers = {"Accept": "application/json"}
        if api_key and api_key.strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"
        self._client = httpx.Client(
            headers=headers,
            timeout=httpx.Timeout(timeout_s),
            follow_redirects=False,
        )

    def search(self, filters: JobSearchFilters) -> list[JobListing]:
        params = filters.model_dump(exclude_none=True)
        try:
            with self._client.stream("GET", self.endpoint, params=params) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_JOB_SOURCE_RESPONSE_BYTES:
                    raise JobProviderError("configured job source response is too large")
                chunks = []
                total_bytes = 0
                for chunk in response.iter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > MAX_JOB_SOURCE_RESPONSE_BYTES:
                        raise JobProviderError("configured job source response is too large")
                    chunks.append(chunk)
            body = json.loads(b"".join(chunks))
        except httpx.TimeoutException as error:
            raise JobProviderError("configured job source timed out") from error
        except httpx.HTTPStatusError as error:
            raise JobProviderError(
                f"configured job source returned HTTP {error.response.status_code}"
            ) from error
        except httpx.NetworkError as error:
            raise JobProviderError("could not connect to configured job source") from error
        except httpx.HTTPError as error:
            raise JobProviderError("configured job source request failed") from error
        except ValueError as error:
            raise JobProviderError("configured job source returned invalid JSON") from error

        records = body.get("jobs") if isinstance(body, dict) else None
        if not isinstance(records, list):
            raise JobProviderError("configured job source response must contain a jobs array")
        listings = []
        for index, record in enumerate(records[: filters.limit]):
            try:
                listing = JobListing.model_validate(record)
            except ValidationError as error:
                logger.warning(
                    "Configured job source %s returned an invalid listing at index %d",
                    self.name,
                    index,
                )
                raise JobProviderError(
                    f"configured job source returned an invalid listing at index {index}"
                ) from error
            listings.append(listing)
        return listings

    def close(self) -> None:
        self._client.close()


def create_job_provider(cfg) -> JobProvider | None:
    if not cfg.job_provider_url:
        return None
    return ConfiguredJSONJobProvider(
        name=cfg.job_provider_name,
        endpoint=cfg.job_provider_url,
        api_key=cfg.job_provider_api_key,
        timeout_s=cfg.job_provider_timeout_s,
    )


def listing_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
