import contextvars
import json
import logging
import re
from datetime import datetime, timezone

request_id_context = contextvars.ContextVar("request_id", default="-")
SECRET_QUERY_PATTERN = re.compile(
    r"(?i)(crtfc_key|api_key|access_token|token)=([^&\s\"']+)"
)


def redact_secrets(message: str) -> str:
    return SECRET_QUERY_PATTERN.sub(r"\1=[REDACTED]", message)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_secrets(record.getMessage()),
            "request_id": request_id_context.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
