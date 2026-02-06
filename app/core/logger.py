import logging
import uuid
from contextvars import ContextVar
from typing import Optional

# Context variable to store the request ID for the current execution context
request_id_ctx_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

class RequestIdFilter(logging.Filter):
    """
    Logging filter to add the request_id to each log record.
    """
    def filter(self, record):
        record.request_id = request_id_ctx_var.get() or "-"
        return True

def setup_logging():
    """
    Configure the root logger with a unified structured format and request tracing.
    """
    log_format = (
        "[%(asctime)s] [%(levelname)s] [%(request_id)s] [%(name)s:%(lineno)d] - %(message)s"
    )
    
    # Get the root logger
    root_logger = logging.getLogger()
    
    # Remove existing handlers if any
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create console handler
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(log_format))
    handler.addFilter(RequestIdFilter())
    
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

def set_request_id(request_id: Optional[str] = None) -> str:
    """
    Set the request ID for the current context. If none provided, generate a new one.
    """
    rid = request_id or str(uuid.uuid4().hex)[:8]
    request_id_ctx_var.set(rid)
    return rid

def get_request_id() -> Optional[str]:
    """
    Get the request ID from the current context.
    """
    return request_id_ctx_var.get()
