"""Provider-backed web research with source attribution and database caching."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_SEARCH_RESPONSE_BYTES = 2 * 1024 * 1024


class ResearchProviderError(RuntimeError):
    """A configured research provider failed or returned invalid data."""


class ResearchProvider(Protocol):
    name: str

    def search(self, query: str, limit: int) -> list[dict]:
        """Return source-attributed search results."""

    def close(self) -> None:
        """Release provider resources."""


class BraveSearchProvider:
    name = "brave"

    def __init__(self, api_key: str, timeout_s: float = 10.0):
        if not api_key or not api_key.strip():
            raise ValueError("BRAVE_SEARCH_API_KEY is required for Brave search")
        if timeout_s <= 0:
            raise ValueError("research timeout must be positive")
        self._client = httpx.Client(
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key.strip(),
            },
            timeout=httpx.Timeout(timeout_s),
            follow_redirects=False,
        )

    def search(self, query: str, limit: int) -> list[dict]:
        try:
            with self._client.stream(
                "GET",
                BRAVE_SEARCH_URL,
                params={
                    "q": query,
                    "count": max(1, min(int(limit), 20)),
                    "safesearch": "moderate",
                },
            ) as response:
                if response.status_code == 429:
                    raise ResearchProviderError("search provider rate limit reached")
                if response.status_code >= 500:
                    raise ResearchProviderError("search provider is temporarily unavailable")
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_SEARCH_RESPONSE_BYTES:
                    raise ResearchProviderError("search provider response is too large")
                chunks = []
                total_bytes = 0
                for chunk in response.iter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > MAX_SEARCH_RESPONSE_BYTES:
                        raise ResearchProviderError("search provider response is too large")
                    chunks.append(chunk)
            body = json.loads(b"".join(chunks))
        except ResearchProviderError:
            raise
        except httpx.TimeoutException as error:
            raise ResearchProviderError("search provider request timed out") from error
        except httpx.HTTPStatusError as error:
            raise ResearchProviderError(
                f"search provider returned HTTP {error.response.status_code}"
            ) from error
        except httpx.NetworkError as error:
            raise ResearchProviderError("could not connect to search provider") from error
        except httpx.HTTPError as error:
            raise ResearchProviderError("search provider request failed") from error
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise ResearchProviderError("search provider returned invalid JSON") from error

        web = body.get("web") if isinstance(body, dict) else None
        results = web.get("results") if isinstance(web, dict) else None
        if not isinstance(results, list):
            raise ResearchProviderError("search provider response has no web results")
        retrieved_at = datetime.now(timezone.utc).isoformat()
        normalized = []
        seen_urls = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            url = item.get("url")
            snippet = item.get("description")
            if not all(isinstance(value, str) and value.strip() for value in (title, url, snippet)):
                continue
            parsed = urlparse(url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                continue
            canonical_url = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
            if canonical_url in seen_urls:
                continue
            seen_urls.add(canonical_url)
            normalized.append({
                "title": title.strip()[:500],
                "url": url,
                "snippet": snippet.strip()[:3000],
                "source_domain": parsed.hostname.lower(),
                "published_at": _published_at(item.get("page_age") or item.get("age")),
                "retrieved_at": retrieved_at,
                "source_classification": "secondary_source",
                "evidence_type": "search_provider_snippet",
            })
            if len(normalized) >= limit:
                break
        return normalized

    def close(self) -> None:
        self._client.close()


def _published_at(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return value.strip()[:80]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _source_classification(domain: str, company_name: str | None) -> str:
    host_parts = set(re.findall(r"[a-z0-9]+", domain.lower()))
    company_parts = set(re.findall(r"[a-z0-9]+", (company_name or "").lower()))
    company_parts -= {"inc", "llc", "ltd", "limited", "corporation", "corp", "company", "co"}
    if company_parts and company_parts.issubset(host_parts):
        return "company_domain_candidate"
    if any(part in host_parts for part in {"glassdoor", "interviewbit", "leetcode", "reddit"}):
        return "community_or_interview_source"
    return "secondary_source"


def _rank_and_deduplicate(results: list[dict], company_name: str | None) -> list[dict]:
    unique = {}
    for result in results:
        url = result.get("url", "")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        canonical = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
        if canonical in unique:
            continue
        row = dict(result)
        row["source_domain"] = parsed.hostname.lower()
        row["source_classification"] = _source_classification(
            row["source_domain"], company_name
        )
        unique[canonical] = row
    priority = {
        "company_domain_candidate": 0,
        "secondary_source": 1,
        "community_or_interview_source": 2,
    }
    return sorted(
        unique.values(),
        key=lambda item: (
            priority.get(item["source_classification"], 3),
            item.get("published_at") is None,
            item.get("published_at") or "",
        ),
    )


class ResearchService:
    def __init__(
        self,
        provider: ResearchProvider | None,
        repo=None,
        cache_ttl_seconds: int = 86400,
        result_limit: int = 8,
    ):
        self.provider = provider
        self.repo = repo
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.result_limit = max(1, min(int(result_limit), 20))

    def search(self, query: str, *, company_name: str | None = None) -> dict:
        safe_query = " ".join((query or "").split())[:500]
        if not safe_query:
            return self._unavailable("empty_query")
        if self.provider is None:
            return self._unavailable("not_configured")

        cache_key = hashlib.sha256(
            f"{self.provider.name}\0{safe_query.casefold()}".encode("utf-8")
        ).hexdigest()
        cached = None
        if self.repo is not None:
            try:
                cached = self.repo.get_research_cache(cache_key, include_expired=True)
            except Exception:
                logger.warning("research_cache_read_failed", exc_info=True)
        now = datetime.now(timezone.utc)
        if cached and cached["expires_at"] > now:
            results = _rank_and_deduplicate(
                cached["results"], company_name
            )[:self.result_limit]
            return self._result(
                safe_query, results, "cached", cached["retrieved_at"], True, False
            )

        try:
            raw_results = self.provider.search(safe_query, self.result_limit)
        except Exception as error:
            if cached:
                results = _rank_and_deduplicate(
                    cached["results"], company_name
                )[:self.result_limit]
                logger.warning(
                    "web_research_unavailable_using_stale_cache exception_class=%s",
                    type(error).__name__,
                )
                return self._result(
                    safe_query, results, "stale_cache", cached["retrieved_at"], True, False
                )
            logger.warning(
                "web_research_unavailable exception_class=%s",
                type(error).__name__,
            )
            return self._unavailable("provider_unavailable", safe_query)

        results = _rank_and_deduplicate(
            raw_results, company_name
        )[:self.result_limit]
        retrieved_at = now
        expires_at = now.timestamp() + self.cache_ttl_seconds
        if self.repo is not None and self.cache_ttl_seconds:
            try:
                self.repo.save_research_cache(
                    cache_key,
                    self.provider.name,
                    safe_query,
                    results,
                    retrieved_at,
                    datetime.fromtimestamp(expires_at, timezone.utc),
                )
            except Exception:
                logger.warning("research_cache_write_failed", exc_info=True)
        return self._result(
            safe_query, results, "live_search", retrieved_at, False, True
        )

    def _result(self, query, sources, status, retrieved_at, cached, searched):
        return {
            "status": status,
            "provider_configured": True,
            "provider": self.provider.name,
            "query": query,
            "searched_for_this_request": searched,
            "cache_hit": cached,
            "retrieved_at": (
                retrieved_at.isoformat()
                if hasattr(retrieved_at, "isoformat")
                else retrieved_at
            ),
            "sources": sources,
            "notice": (
                "Search results are source-attributed but are not independently verified. "
                "Snippets are provider-supplied; PlaceX does not crawl arbitrary pages."
            ),
        }

    @staticmethod
    def _unavailable(status, query=""):
        notice = (
            "Live web research is not configured. This result uses internal database "
            "context and general AI guidance only."
            if status == "not_configured"
            else "Live web research is unavailable; this result uses internal database "
            "context and general AI guidance only."
        )
        return {
            "status": status,
            "provider_configured": status != "not_configured",
            "provider": None,
            "query": query,
            "searched_for_this_request": False,
            "cache_hit": False,
            "retrieved_at": None,
            "sources": [],
            "notice": notice,
        }

    def close(self) -> None:
        if self.provider is not None:
            self.provider.close()


def create_research_service(cfg, repo=None) -> ResearchService:
    provider_name = (cfg.research_provider or "none").strip().lower()
    if provider_name in {"", "none", "disabled"}:
        provider = None
    elif provider_name == "brave":
        if not cfg.brave_search_api_key:
            raise ValueError(
                "BRAVE_SEARCH_API_KEY is required when PLACEX_RESEARCH_PROVIDER=brave"
            )
        provider = BraveSearchProvider(
            cfg.brave_search_api_key, timeout_s=cfg.research_timeout_s
        )
    else:
        raise ValueError(
            f"unsupported PLACEX_RESEARCH_PROVIDER: {provider_name}"
        )
    return ResearchService(
        provider,
        repo=repo,
        cache_ttl_seconds=cfg.research_cache_ttl_seconds,
        result_limit=cfg.research_result_limit,
    )
