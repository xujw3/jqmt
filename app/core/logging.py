from __future__ import annotations

import contextvars
import logging
import sys

from app.core.config import Settings


request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_context.get()
        return True


def set_request_id(request_id: str) -> contextvars.Token[str]:
    return request_id_context.set(request_id)


def reset_request_id(token: contextvars.Token[str]) -> None:
    request_id_context.reset(token)


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s] %(message)s",
        stream=sys.stdout,
    )

    request_id_filter = RequestIDFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(request_id_filter)
