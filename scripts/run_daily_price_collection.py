"""Sequential orchestrator for the bounded daily-price API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlsplit

RETRYABLE_HTTP_STATUSES = {429, 502, 503, 504}
AUTH_HTTP_STATUSES = {401, 403}
D2_PREVIEW_API_HOST = (
    "alphachain-git-phase-d2-daily-prices-juns-projects-2bb09b36.vercel.app"
)
D2_PREVIEW_API_PATH = "/api/v1/cron/prices/batch"
REQUIRED_RESPONSE_FIELDS = {
    "status",
    "target_trade_date",
    "processed",
    "remaining",
    "has_more",
    "active_count",
    "completed_count",
    "missing_count",
    "completion_rate",
    "failed_count",
    "retry_waiting_count",
}


class CollectionError(RuntimeError):
    pass


class AuthenticationError(CollectionError):
    pass


class ResponseValidationError(CollectionError):
    pass


class RetryableHttpError(CollectionError):
    def __init__(self, status_code: int):
        super().__init__(f"retryable HTTP status {status_code}")
        self.status_code = status_code


class Outcome(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    LOCKED = "LOCKED"


EXIT_CODES = {
    Outcome.COMPLETE: 0,
    Outcome.FAILED: 1,
    Outcome.PARTIAL: 2,
    Outcome.LOCKED: 3,
}


@dataclass(frozen=True)
class Config:
    api_url: str
    token: str
    batch_size: int = 100
    max_calls: int = 40
    request_timeout: float = 60.0
    total_timeout: float = 1800.0
    max_http_retries: int = 3
    max_locked_retries: int = 3
    base_backoff: float = 2.0
    max_stalled_responses: int = 2
    bypass_secret: str = ""

    def validate(self) -> None:
        if not self.api_url:
            raise CollectionError("API URL is required")
        if not self.token:
            raise CollectionError("API token is required")
        validate_api_url(self.api_url)
        if not 1 <= self.batch_size <= 100:
            raise CollectionError("batch size must be between 1 and 100")
        if self.max_calls < 1:
            raise CollectionError("max calls must be positive")
        if self.request_timeout <= 0 or self.total_timeout <= 0:
            raise CollectionError("timeouts must be positive")


def validate_api_url(api_url: str) -> None:
    try:
        parsed_url = urlsplit(api_url)
        port = parsed_url.port
    except ValueError as exc:
        raise CollectionError("D-2 Preview API URL is invalid") from exc
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != D2_PREVIEW_API_HOST
        or parsed_url.path != D2_PREVIEW_API_PATH
        or port not in {None, 443}
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise CollectionError("D-2 Preview API URL is not approved")


class ApprovedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        parsed_url = urlsplit(new_url)
        if parsed_url.scheme != "https" or parsed_url.hostname != D2_PREVIEW_API_HOST:
            raise CollectionError("redirect target is not approved")
        return super().redirect_request(
            request, file_pointer, code, message, headers, new_url
        )

@dataclass(frozen=True)
class RunResult:
    outcome: Outcome
    calls: int
    completion_rate: float | None
    missing_count: int | None
    reason: str

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.outcome]


Transport = Callable[[Config], dict[str, Any]]
Logger = Callable[[str], None]


def decode_response(payload: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResponseValidationError("API returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise ResponseValidationError("API response must be a JSON object")
    return decoded


def post_batch(config: Config) -> dict[str, Any]:
    config.validate()
    body = json.dumps({"batch_size": config.batch_size}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {config.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "alpha-chain-price-orchestrator/1.0",
    }
    if config.bypass_secret:
        headers["x-vercel-protection-bypass"] = config.bypass_secret
    request = urllib.request.Request(
        config.api_url,
        data=body,
        method="POST",
        headers=headers,
    )
    opener = urllib.request.build_opener(ApprovedRedirectHandler())
    try:
        with opener.open(request, timeout=config.request_timeout) as response:
            return decode_response(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in AUTH_HTTP_STATUSES:
            raise AuthenticationError(f"authentication failed with HTTP {exc.code}") from None
        if exc.code in RETRYABLE_HTTP_STATUSES:
            raise RetryableHttpError(exc.code) from None
        raise CollectionError(f"API request failed with HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CollectionError(f"network request failed: {type(exc).__name__}") from None


def validate_response(response: dict[str, Any]) -> None:
    missing_fields = REQUIRED_RESPONSE_FIELDS - response.keys()
    if missing_fields:
        raise ResponseValidationError(
            f"API response missing fields: {', '.join(sorted(missing_fields))}"
        )
    if response["status"] not in {"COMPLETE", "PARTIAL", "LOCKED"}:
        raise ResponseValidationError("API response has an invalid status")
    count_fields = (
        "processed",
        "remaining",
        "active_count",
        "completed_count",
        "missing_count",
        "failed_count",
        "retry_waiting_count",
    )
    for field in count_fields:
        value = response[field]
        if response["status"] == "LOCKED" and value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ResponseValidationError(f"API response field {field} must be non-negative")
    completion_rate = response["completion_rate"]
    if not (
        response["status"] == "LOCKED" and completion_rate is None
    ) and (
        not isinstance(completion_rate, (int, float))
        or isinstance(completion_rate, bool)
        or not 0 <= completion_rate <= 100
    ):
        raise ResponseValidationError("API response field completion_rate must be 0-100")
    if response["has_more"] is not None and not isinstance(response["has_more"], bool):
        raise ResponseValidationError("API response field has_more must be boolean")


def _fingerprint(response: dict[str, Any]) -> tuple[Any, ...]:
    return (
        response["status"],
        response["target_trade_date"],
        response["processed"],
        response["remaining"],
        response["has_more"],
        response["completed_count"],
        response["missing_count"],
    )


def _result(outcome: Outcome, calls: int, response: dict[str, Any] | None, reason: str) -> RunResult:
    completion_rate = response.get("completion_rate") if response else None
    missing_count = response.get("missing_count") if response else None
    return RunResult(outcome, calls, completion_rate, missing_count, reason)


def run_collection(
    config: Config,
    *,
    transport: Transport = post_batch,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    logger: Logger = print,
) -> RunResult:
    config.validate()
    started_at = clock()
    calls = 0
    http_retries = 0
    locked_retries = 0
    stalled_responses = 0
    previous_fingerprint = None
    last_response = None

    def timed_out() -> bool:
        return clock() - started_at >= config.total_timeout

    while calls < config.max_calls:
        if timed_out():
            outcome = Outcome.PARTIAL if last_response else Outcome.FAILED
            return _result(outcome, calls, last_response, "total timeout reached")
        try:
            calls += 1
            response = transport(config)
            validate_response(response)
        except AuthenticationError as exc:
            logger(f"FAILED: {exc}")
            return _result(Outcome.FAILED, calls, last_response, str(exc))
        except RetryableHttpError as exc:
            if http_retries >= config.max_http_retries:
                logger(f"FAILED: retry limit reached after HTTP {exc.status_code}")
                return _result(Outcome.FAILED, calls, last_response, "HTTP retry limit reached")
            delay = config.base_backoff * (2 ** http_retries)
            http_retries += 1
            if clock() - started_at + delay >= config.total_timeout:
                return _result(Outcome.FAILED, calls, last_response, "total timeout reached")
            logger(f"RETRY: HTTP {exc.status_code}; waiting {delay:g}s")
            sleep(delay)
            continue
        except CollectionError as exc:
            logger(f"FAILED: {exc}")
            return _result(Outcome.FAILED, calls, last_response, str(exc))

        http_retries = 0
        if response["status"] == "LOCKED":
            if locked_retries >= config.max_locked_retries:
                logger("LOCKED: retry limit reached")
                return _result(Outcome.LOCKED, calls, last_response, "lock retry limit reached")
            delay = config.base_backoff * (2 ** locked_retries)
            locked_retries += 1
            if clock() - started_at + delay >= config.total_timeout:
                return _result(Outcome.LOCKED, calls, last_response, "total timeout reached while locked")
            logger(f"LOCKED: waiting {delay:g}s before retry")
            sleep(delay)
            continue

        locked_retries = 0
        last_response = response
        fingerprint = _fingerprint(response)
        if fingerprint == previous_fingerprint:
            stalled_responses += 1
        else:
            stalled_responses = 0
        previous_fingerprint = fingerprint
        if stalled_responses >= config.max_stalled_responses:
            logger("FAILED: collection progress stalled")
            return _result(Outcome.FAILED, calls, response, "identical response repeated")

        logger(
            "PROGRESS: "
            f"calls={calls} processed={response['processed']} "
            f"remaining={response['remaining']} missing={response['missing_count']} "
            f"completion={response['completion_rate']}%"
        )
        if response["status"] == "COMPLETE" and response["missing_count"] == 0:
            logger("COMPLETE: missing=0")
            return _result(Outcome.COMPLETE, calls, response, "collection complete")
        if response["has_more"] is False:
            logger(f"PARTIAL: no eligible candidates; missing={response['missing_count']}")
            return _result(Outcome.PARTIAL, calls, response, "no eligible candidates remain")

    outcome = Outcome.PARTIAL if last_response else Outcome.FAILED
    logger(f"{outcome.value}: maximum call count reached")
    return _result(outcome, calls, last_response, "maximum call count reached")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.getenv("PRICE_COLLECTION_API_URL", ""))
    parser.add_argument("--token", default=os.getenv("PRICE_COLLECTION_CRON_SECRET", ""))
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-calls", type=int, default=40)
    parser.add_argument("--request-timeout", type=float, default=60.0)
    parser.add_argument("--total-timeout", type=float, default=1800.0)
    parser.add_argument("--max-http-retries", type=int, default=3)
    parser.add_argument("--max-locked-retries", type=int, default=3)
    parser.add_argument("--base-backoff", type=float, default=2.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = Config(
        api_url=args.api_url,
        token=args.token,
        bypass_secret=os.getenv("VERCEL_AUTOMATION_BYPASS_SECRET", ""),
        batch_size=args.batch_size,
        max_calls=args.max_calls,
        request_timeout=args.request_timeout,
        total_timeout=args.total_timeout,
        max_http_retries=args.max_http_retries,
        max_locked_retries=args.max_locked_retries,
        base_backoff=args.base_backoff,
    )
    try:
        result = run_collection(config)
    except CollectionError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return EXIT_CODES[Outcome.FAILED]
    print(
        f"SUMMARY: outcome={result.outcome.value} calls={result.calls} "
        f"completion={result.completion_rate} missing={result.missing_count}"
    )
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
