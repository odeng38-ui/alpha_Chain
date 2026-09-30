import json
import logging

from app.logging_config import JsonFormatter, configure_logging


def test_http_client_logs_do_not_emit_secret_bearing_urls():
    configure_logging("INFO")

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING

    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "GET https://example.test?crtfc_key=top-secret&api_key=also-secret",
        (),
        None,
    )
    payload = json.loads(JsonFormatter().format(record))

    assert "top-secret" not in payload["message"]
    assert "also-secret" not in payload["message"]
    assert "crtfc_key=[REDACTED]" in payload["message"]
    assert "api_key=[REDACTED]" in payload["message"]
