import logging
from app.core.response import HTTPResponse

logger = logging.getLogger(__name__)

def logger_middleware(request, next_handler) -> HTTPResponse:
    """
    Middleware that captures request metrics and handles uncaught route exceptions.
    """
    client_ip, client_port = getattr(request, "client_addr", ("unknown", 0))

    try:
        response = next_handler(request)
    except Exception:
        logger.exception("%s:%s - %s %s -> Crash", client_ip, client_port, request.method, request.path)
        # Full traceback goes to the log; the client only gets a generic
        # message so internal exception details never leak over the wire.
        response = HTTPResponse(
            status=500,
            headers={"Content-Type": "text/plain"},
            body=b"Internal Server Error"
        )

    status_str = HTTPResponse.STATUS_MAP.get(response.status, str(response.status))
    logger.info("%s:%s - %s %s -> %s", client_ip, client_port, request.method, request.path, status_str)
    return response
