import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.run_daily_price_collection import (
    ApprovedRedirectHandler,
    AuthenticationError,
    CollectionError,
    Config,
    Outcome,
    ResponseValidationError,
    RetryableHttpError,
    decode_response,
    post_batch,
    run_collection,
)


def response(*, status="PARTIAL", processed=100, remaining=1, has_more=True,
             active=2688, completed=100, missing=2588):
    return {
        "status": status,
        "target_trade_date": "2026-10-27",
        "processed": processed,
        "remaining": remaining,
        "has_more": has_more,
        "active_count": active,
        "completed_count": completed,
        "missing_count": missing,
        "completion_rate": None if completed is None else round(completed / active * 100, 2),
        "failed_count": 0,
        "retry_waiting_count": 0,
    }


def config(**overrides):
    values = {
        "api_url": (
            "https://alphachain-git-phase-d2-daily-prices-"
            "juns-projects-2bb09b36.vercel.app/api/v1/cron/prices/batch"
        ),
        "token": "top-secret",
    }
    values.update(overrides)
    return Config(**values)


def test_processes_twenty_eight_batches_sequentially():
    responses = [response(completed=index * 90, missing=2688 - index * 90, remaining=28 - index)
                 for index in range(1, 28)]
    responses.append(response(status="COMPLETE", processed=0, remaining=0, has_more=False,
                              completed=2688, missing=0))
    calls = []

    result = run_collection(config(), transport=lambda cfg: calls.append(cfg.batch_size) or responses.pop(0),
                            sleep=lambda seconds: None, logger=lambda message: None)

    assert result.outcome is Outcome.COMPLETE
    assert result.calls == 28
    assert calls == [100] * 28


def test_stops_when_has_more_is_false_with_partial_result():
    result = run_collection(
        config(),
        transport=lambda cfg: response(processed=0, remaining=0, has_more=False,
                                       completed=2683, missing=5),
        logger=lambda message: None,
    )

    assert result.outcome is Outcome.PARTIAL
    assert result.exit_code == 2
    assert result.missing_count == 5


def test_locked_response_is_retried_then_completes():
    locked = response(status="LOCKED", processed=0, remaining=None, has_more=None,
                      completed=None, missing=None)
    responses = [locked, locked, response(status="COMPLETE", processed=1, remaining=0,
                                          has_more=False, completed=2688, missing=0)]
    sleeps = []

    result = run_collection(config(base_backoff=1), transport=lambda cfg: responses.pop(0),
                            sleep=sleeps.append, logger=lambda message: None)

    assert result.outcome is Outcome.COMPLETE
    assert sleeps == [1, 2]


@pytest.mark.parametrize("status_code", [429, 503])
def test_retryable_http_status_uses_exponential_backoff(status_code):
    attempts = 0
    sleeps = []

    def transport(cfg):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RetryableHttpError(status_code)
        return response(status="COMPLETE", processed=1, remaining=0, has_more=False,
                        completed=2688, missing=0)

    result = run_collection(config(base_backoff=1), transport=transport,
                            sleep=sleeps.append, logger=lambda message: None)

    assert result.outcome is Outcome.COMPLETE
    assert sleeps == [1, 2]


def test_authentication_error_fails_without_retry():
    calls = 0

    def transport(cfg):
        nonlocal calls
        calls += 1
        raise AuthenticationError("authentication failed with HTTP 401")

    result = run_collection(config(), transport=transport, logger=lambda message: None)

    assert result.outcome is Outcome.FAILED
    assert calls == 1


def test_invalid_json_is_rejected():
    with pytest.raises(ResponseValidationError, match="invalid JSON"):
        decode_response(b"not-json")


def test_identical_response_repetition_is_detected():
    repeated = response(completed=100, missing=2588, remaining=25)

    result = run_collection(config(max_stalled_responses=1), transport=lambda cfg: repeated,
                            logger=lambda message: None)

    assert result.outcome is Outcome.FAILED
    assert result.reason == "identical response repeated"


def test_maximum_call_count_returns_partial():
    index = 0

    def transport(cfg):
        nonlocal index
        index += 1
        return response(completed=index * 100, missing=2688 - index * 100, remaining=20 - index)

    result = run_collection(config(max_calls=3), transport=transport, logger=lambda message: None)

    assert result.outcome is Outcome.PARTIAL
    assert result.calls == 3
    assert result.reason == "maximum call count reached"


def test_total_timeout_returns_partial_after_progress():
    times = iter([0.0, 0.0, 3.0, 6.0])

    result = run_collection(
        config(total_timeout=5),
        transport=lambda cfg: response(completed=100, missing=2588, remaining=20),
        clock=lambda: next(times),
        logger=lambda message: None,
    )

    assert result.outcome is Outcome.PARTIAL
    assert result.reason == "total timeout reached"


def test_secret_is_never_written_to_logs():
    logs = []

    result = run_collection(
        config(token="never-log-this", bypass_secret="never-log-bypass"),
        transport=lambda cfg: response(status="COMPLETE", processed=1, remaining=0,
                                       has_more=False, completed=2688, missing=0),
        logger=logs.append,
    )

    assert result.outcome is Outcome.COMPLETE
    output = "\n".join(logs)
    assert "never-log-this" not in output
    assert "never-log-bypass" not in output


def test_malformed_response_fails_explicitly():
    result = run_collection(config(), transport=lambda cfg: {"status": "PARTIAL"},
                            logger=lambda message: None)

    assert result.outcome is Outcome.FAILED
    assert "missing fields" in result.reason


def test_persistent_lock_returns_distinct_locked_exit():
    locked = response(status="LOCKED", processed=0, remaining=None, has_more=None,
                      completed=None, missing=None)

    result = run_collection(
        config(max_locked_retries=1, base_backoff=0.01),
        transport=lambda cfg: locked,
        sleep=lambda seconds: None,
        logger=lambda message: None,
    )

    assert result.outcome is Outcome.LOCKED
    assert result.exit_code == 3
    assert result.calls == 2


class StubResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(
            response(
                status="COMPLETE",
                processed=1,
                remaining=0,
                has_more=False,
                completed=2688,
                missing=0,
            )
        ).encode("utf-8")


def test_post_batch_sends_bearer_and_bypass_headers(monkeypatch):
    opener = Mock()
    opener.open.return_value = StubResponse()
    monkeypatch.setattr(
        "scripts.run_daily_price_collection.urllib.request.build_opener",
        lambda handler: opener,
    )

    post_batch(config(token="cron-secret", bypass_secret="bypass-secret"))

    request = opener.open.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer cron-secret"
    assert request.get_header("X-vercel-protection-bypass") == "bypass-secret"


def test_post_batch_without_bypass_secret_keeps_existing_auth(monkeypatch):
    opener = Mock()
    opener.open.return_value = StubResponse()
    monkeypatch.setattr(
        "scripts.run_daily_price_collection.urllib.request.build_opener",
        lambda handler: opener,
    )

    post_batch(config(token="cron-secret"))

    request = opener.open.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer cron-secret"
    assert request.get_header("X-vercel-protection-bypass") is None


@pytest.mark.parametrize(
    "api_url",
    [
        "http://alphachain-git-phase-d2-daily-prices-juns-projects-2bb09b36.vercel.app/api/v1/cron/prices/batch",
        "https://alphachain.vercel.app/api/v1/cron/prices/batch",
        "https://alphachain-git-phase-d2-daily-prices-juns-projects-2bb09b36.vercel.app/api/v1/cron/other",
    ],
)
def test_unapproved_api_url_is_rejected_before_http_request(monkeypatch, api_url):
    build_opener = Mock()
    monkeypatch.setattr(
        "scripts.run_daily_price_collection.urllib.request.build_opener",
        build_opener,
    )

    with pytest.raises(CollectionError, match="not approved"):
        post_batch(config(api_url=api_url, bypass_secret="bypass-secret"))

    build_opener.assert_not_called()


def test_cross_host_redirect_is_rejected():
    handler = ApprovedRedirectHandler()

    with pytest.raises(CollectionError, match="redirect target is not approved"):
        handler.redirect_request(
            None,
            None,
            307,
            "Temporary Redirect",
            {},
            "https://example.invalid/api/v1/cron/prices/batch",
        )


def test_workflow_limits_execution_to_d2_branch_and_passes_bypass_secret():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "daily-price-collection.yml"
    ).read_text(encoding="utf-8")

    assert "if: github.ref == 'refs/heads/phase-d2-daily-prices'" in workflow
    assert (
        "VERCEL_AUTOMATION_BYPASS_SECRET: "
        "${{ secrets.VERCEL_AUTOMATION_BYPASS_SECRET }}"
    ) in workflow
    assert "schedule:" not in workflow
