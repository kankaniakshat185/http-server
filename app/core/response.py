import gzip
import os

class HTTPResponse:
    """
    Represents an HTTP response. Encapsulates status codes, headers, and bodies.
    Adheres to SOLID Single Responsibility Principle by only managing response formatting.
    """
    STATUS_MAP = {
        200: "200 OK",
        201: "201 Created",
        204: "204 No Content",
        400: "400 Bad Request",
        403: "403 Forbidden",
        404: "404 Not Found",
        405: "405 Method Not Allowed",
        413: "413 Payload Too Large",
        500: "500 Internal Server Error",
        501: "501 Not Implemented",
    }

    # Content-types that are already compressed - gzip-ing them again just
    # burns CPU for no size benefit (and can occasionally make them larger).
    _NON_COMPRESSIBLE_PREFIXES = ("image/", "video/", "audio/")
    _NON_COMPRESSIBLE_TYPES = {
        "application/zip", "application/gzip", "application/x-gzip",
        "application/pdf", "application/octet-stream",
    }

    # Files served via `stream_path` are read/written in fixed-size chunks
    # instead of being buffered into memory whole.
    STREAM_CHUNK_BYTES = 64 * 1024

    def __init__(self, status: int = 200, headers: dict = None, body: bytes = b"", stream_path: str = None):
        self.status = status
        self.headers = headers or {}
        self.body = body
        # When set, write_to() streams this file's contents as the body
        # instead of using `body` - keeps large file responses out of memory.
        self.stream_path = stream_path

    def _should_compress(self, resp_headers: dict, body_len: int) -> bool:
        if body_len == 0 or "content-encoding" in resp_headers:
            return False
        content_type = resp_headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type.startswith(self._NON_COMPRESSIBLE_PREFIXES):
            return False
        if content_type in self._NON_COMPRESSIBLE_TYPES:
            return False
        return True

    def _status_line(self) -> str:
        status_str = self.STATUS_MAP.get(self.status, f"{self.status} Unknown")
        return f"HTTP/1.1 {status_str}\r\n"

    @staticmethod
    def _format_headers(resp_headers: dict) -> str:
        headers_str = ""
        for k, v in resp_headers.items():
            header_name = "-".join(part.capitalize() for part in k.split("-"))
            headers_str += f"{header_name}: {v}\r\n"
        headers_str += "\r\n"
        return headers_str

    def to_bytes(self, client_supports_gzip: bool = False) -> bytes:
        """
        Serializes the response into raw HTTP/1.1 network bytes.
        Handles dynamic gzip compression based on client capability.
        Not used for streamed (stream_path) responses - see write_to().
        """
        response_body = self.body
        resp_headers = {k.lower(): v for k, v in self.headers.items()}

        if client_supports_gzip and self._should_compress(resp_headers, len(response_body)):
            response_body = gzip.compress(response_body)
            resp_headers["content-encoding"] = "gzip"

        resp_headers["content-length"] = str(len(response_body))

        return (self._status_line() + self._format_headers(resp_headers)).encode("utf-8") + response_body

    def write_to(self, conn, client_supports_gzip: bool = False) -> None:
        """
        Sends this response over a connected socket. Small/dynamic bodies are
        serialized and sent in one call (optionally gzip-compressed); a
        `stream_path` body is sent uncompressed in fixed-size chunks so large
        files never sit fully in memory.
        """
        if self.stream_path is None:
            conn.sendall(self.to_bytes(client_supports_gzip))
            return

        file_size = os.path.getsize(self.stream_path)
        resp_headers = {k.lower(): v for k, v in self.headers.items()}
        resp_headers["content-length"] = str(file_size)

        header_bytes = (self._status_line() + self._format_headers(resp_headers)).encode("utf-8")
        conn.sendall(header_bytes)

        with open(self.stream_path, "rb") as f:
            while True:
                chunk = f.read(self.STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                conn.sendall(chunk)
