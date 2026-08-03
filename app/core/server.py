import logging
import queue
import socket
import selectors
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from app.core.request import HTTPRequest
from app.core.response import HTTPResponse
from app.middleware.base import MiddlewarePipeline
from app.routing.router import Router

logger = logging.getLogger(__name__)

class HTTPServer:
    """
    Core Server orchestrating sockets, selectors event loop, thread pools,
    routing, and middleware pipeline.
    Adheres to SOLID Single Responsibility and Open/Closed Principles.
    """

    MAX_HEADER_BYTES = 16 * 1024
    MAX_BODY_BYTES = 10 * 1024 * 1024
    # A connection - whether mid-request or idly holding a keep-alive slot -
    # gets closed if nothing is heard from it for this long. Bounds memory/fd
    # usage under a Slowloris-style trickle of bytes, per the read-timeout
    # mitigation described in docs/interview_defense.md.
    IDLE_TIMEOUT_SECONDS = 30

    def __init__(self, host: str = "0.0.0.0", port: int = 4221, max_workers: int = 10):
        self.host = host
        self.port = port
        self.selector = selectors.DefaultSelector()
        self.thread_pool = ThreadPoolExecutor(max_workers=max_workers)
        self.router = Router()
        self.pipeline = MiddlewarePipeline()
        self.sessions = {}
        # Guards structural changes to `sessions` (insert/remove/lookup) only.
        # Per-connection buffer state is guarded by that session's own "lock"
        # entry instead, so unrelated connections never contend with each other.
        self.sessions_lock = threading.Lock()
        # selectors.DefaultSelector is not safe to mutate from multiple threads
        # concurrently with select(). Worker threads never call register()/
        # unregister() directly - they post a request here, and only the
        # event-loop thread (inside start()) drains it and touches the selector.
        self._selector_actions = queue.Queue()
        self.running = False
        self.server_socket = None

    def start(self):
        """
        Binds to socket and starts the asynchronous selectors event loop.
        """
        self.server_socket = socket.create_server((self.host, self.port), reuse_port=True)
        self.server_socket.setblocking(False)
        self.selector.register(self.server_socket, selectors.EVENT_READ, self._accept)

        self.running = True
        logger.info("Server started on http://%s:%s using hybrid Async + Thread Pool concurrency model...", self.host, self.port)

        try:
            while self.running:
                # 0.5s timeout allows safe thread registrations and loop checks
                events = self.selector.select(timeout=0.5)
                for key, mask in events:
                    callback = key.data
                    try:
                        callback(key.fileobj, mask)
                    except Exception:
                        # A single misbehaving connection must never take down
                        # the whole event loop.
                        logger.exception("Error in event callback")
                self._drain_selector_actions()
                self._sweep_idle_connections()
        except KeyboardInterrupt:
            logger.info("Shutting down server...")
        finally:
            self.stop()

    def stop(self):
        """
        Closes all sockets and shuts down the thread pool.
        """
        self.running = False
        with self.sessions_lock:
            for conn in list(self.sessions.keys()):
                self._close_connection_locked(conn)
            if self.server_socket:
                try:
                    self.selector.unregister(self.server_socket)
                except KeyError:
                    pass
                self.server_socket.close()
        self.selector.close()
        self.thread_pool.shutdown(wait=False)

    def _drain_selector_actions(self):
        """
        Applies register/unregister requests queued by worker threads.
        Must only ever be called from the event-loop thread (inside start()),
        which keeps the selector itself single-threaded.
        """
        while True:
            try:
                action, conn = self._selector_actions.get_nowait()
            except queue.Empty:
                return
            try:
                if action == "register":
                    with self.sessions_lock:
                        still_open = conn in self.sessions
                    if still_open:
                        self.selector.register(conn, selectors.EVENT_READ, self._read)
                elif action == "unregister":
                    self.selector.unregister(conn)
            except (KeyError, ValueError, OSError):
                pass

    def _sweep_idle_connections(self):
        """
        Closes any connection that has been silent for longer than
        IDLE_TIMEOUT_SECONDS, whether it's sitting mid-request (a slow-drip
        client) or fully idle between keep-alive requests. Runs once per
        event-loop tick, so worst-case detection latency matches the
        select() timeout.
        """
        now = time.monotonic()
        with self.sessions_lock:
            stale = [
                conn for conn, session in self.sessions.items()
                if now - session["last_activity"] > self.IDLE_TIMEOUT_SECONDS
            ]
        for conn in stale:
            logger.info("Closing idle connection %s (no activity for %ss)", conn, self.IDLE_TIMEOUT_SECONDS)
            self._close_connection(conn)

    def _accept(self, server_socket, mask):
        """Accepts incoming TCP connection and registers it for reading."""
        try:
            conn, addr = server_socket.accept()
        except OSError:
            logger.exception("Error accepting connection")
            return

        conn.setblocking(False)

        session = {
            "buffer": b"",
            "content_length": None,
            "headers_part": None,
            "body_part": None,
            "client_addr": addr,
            "lock": threading.Lock(),
            "last_activity": time.monotonic(),
        }
        with self.sessions_lock:
            self.sessions[conn] = session
        # _accept always runs on the event-loop thread, so a direct call is safe here.
        self.selector.register(conn, selectors.EVENT_READ, self._read)

    def _read(self, conn, mask):
        """Reads non-blocking chunks into connection session buffers."""
        with self.sessions_lock:
            session = self.sessions.get(conn)
        if not session:
            return

        try:
            chunk = conn.recv(4096)
            if not chunk:
                self._close_connection(conn)
                return

            with session["lock"]:
                session["last_activity"] = time.monotonic()
                if session["content_length"] is None:
                    session["buffer"] += chunk
                else:
                    session["body_part"] += chunk

                if not self._ensure_headers_parsed(conn, session):
                    return
                self._dispatch_if_complete(conn, session)

        except Exception:
            logger.exception("Error reading socket")
            self._close_connection(conn)

    def _send_response(self, conn, response, supports_gzip: bool = False):
        """
        Sends a response from a thread-pool worker thread. `conn` is normally
        non-blocking (owned by the event loop), but a large or streamed body
        sent across multiple writes can exceed the OS send buffer and raise
        BlockingIOError instead of waiting - which would abort the response
        mid-stream. Worker threads are free to block, so the socket is
        switched to blocking mode for the duration of this call only.
        """
        conn.setblocking(True)
        try:
            response.write_to(conn, supports_gzip)
        finally:
            conn.setblocking(False)

    def _process_request(self, conn, addr, headers_part: bytes, body_part: bytes):
        """Executes the middleware chain and routing logic inside a Thread Pool worker thread."""
        try:
            request = HTTPRequest.parse(headers_part, body_part)
        except ValueError as e:
            response = HTTPResponse(
                status=400,
                headers={"Content-Type": "text/plain"},
                body=f"Bad Request: {e}".encode("utf-8"),
            )
            try:
                self._send_response(conn, response)
            except Exception:
                pass
            self._close_connection(conn)
            return

        try:
            request.client_addr = addr

            # Router handler wrapped in middleware pipeline
            def final_handler(req: HTTPRequest) -> HTTPResponse:
                handler, params = self.router.match(req.method, req.path)
                if handler:
                    req.path_params = params
                    return handler(req)
                allowed_methods = self.router.allowed_methods(req.path)
                if allowed_methods:
                    return HTTPResponse(
                        status=405,
                        headers={"Content-Type": "text/plain", "Allow": ", ".join(sorted(allowed_methods))},
                        body=b"Method Not Allowed",
                    )
                return HTTPResponse(status=404, headers={"Content-Type": "text/plain"}, body=b"Not Found")

            # Execute pipeline
            response = self.pipeline.execute(request, final_handler)

            # Send response, streaming large static file bodies where applicable.
            supports_gzip = "gzip" in request.get_header("accept-encoding")
            self._send_response(conn, response, supports_gzip)

            # Persistent Connection logic
            # HTTP/1.1 defaults to keep-alive. HTTP/1.0 defaults to close.
            conn_header = request.get_header("connection").lower()
            is_keep_alive = (request.version == "HTTP/1.1" and conn_header != "close") or (conn_header == "keep-alive")

            if not is_keep_alive:
                self._close_connection(conn)
            else:
                # Re-register for reading future pipelined requests. The actual
                # selector call happens on the event-loop thread, never here.
                self._selector_actions.put(("register", conn))
                self._check_buffered_request(conn)

        except Exception:
            logger.exception("Error processing client task")
            self._close_connection(conn)

    def _check_buffered_request(self, conn):
        """Checks if session buffer contains a complete HTTP request. Dispatches immediately if so."""
        with self.sessions_lock:
            session = self.sessions.get(conn)
        if not session:
            return

        with session["lock"]:
            if not self._ensure_headers_parsed(conn, session):
                return
            self._dispatch_if_complete(conn, session)

    def _ensure_headers_parsed(self, conn, session) -> bool:
        """
        Splits session['buffer'] into headers/body once the CRLFCRLF boundary
        has arrived, enforcing MAX_HEADER_BYTES / MAX_BODY_BYTES so a client
        can't force unbounded memory growth. Caller must hold session['lock'].
        Returns False if the connection was rejected and closed.
        """
        if session["content_length"] is not None:
            return True

        if b"\r\n\r\n" not in session["buffer"]:
            if len(session["buffer"]) > self.MAX_HEADER_BYTES:
                self._reject_and_close(conn, 400)
                return False
            return True

        headers_part, body_part = session["buffer"].split(b"\r\n\r\n", 1)

        if self._is_chunked_encoding(headers_part):
            # Chunked request bodies aren't supported: silently treating this
            # as a zero-length body would desync the next pipelined request
            # off the still-unread chunk data. Fail loudly instead.
            self._reject_and_close(conn, 501)
            return False

        content_length = self._parse_content_length(headers_part)
        if content_length > self.MAX_BODY_BYTES:
            self._reject_and_close(conn, 413)
            return False

        session["content_length"] = content_length
        session["headers_part"] = headers_part
        session["body_part"] = body_part
        return True

    def _dispatch_if_complete(self, conn, session) -> bool:
        """
        If a full request body has been buffered, unregisters the socket and
        hands the request off to the thread pool. Caller must hold session['lock'].
        Returns False if the connection was rejected and closed.
        """
        if session["content_length"] is None:
            return True

        if len(session["body_part"]) > self.MAX_BODY_BYTES:
            self._reject_and_close(conn, 413)
            return False

        if len(session["body_part"]) >= session["content_length"]:
            body = session["body_part"][:session["content_length"]]
            leftover = session["body_part"][session["content_length"]:]
            headers_to_process = session["headers_part"]
            addr = session["client_addr"]

            # Reset session state for subsequent pipelined requests
            session["buffer"] = leftover
            session["content_length"] = None
            session["headers_part"] = None
            session["body_part"] = None

            # Temporarily drop read interest while the thread pool processes this
            self._selector_actions.put(("unregister", conn))
            self.thread_pool.submit(self._process_request, conn, addr, headers_to_process, body)

        return True

    def _reject_and_close(self, conn, status: int):
        """Sends a minimal error response and tears down the connection. Caller must hold session['lock'].."""
        try:
            message = HTTPResponse.STATUS_MAP.get(status, str(status))
            response = HTTPResponse(status=status, headers={"Content-Type": "text/plain"}, body=message.encode("utf-8"))
            response.write_to(conn)
        except Exception:
            pass
        self._close_connection(conn)

    def _parse_content_length(self, headers_part: bytes) -> int:
        headers_decoded = headers_part.decode("utf-8", errors="ignore")
        for line in headers_decoded.split("\r\n"):
            if line.lower().startswith("content-length:"):
                try:
                    return int(line.split(":", 1)[1].strip())
                except (ValueError, IndexError):
                    return 0
        return 0

    def _is_chunked_encoding(self, headers_part: bytes) -> bool:
        headers_decoded = headers_part.decode("utf-8", errors="ignore")
        for line in headers_decoded.split("\r\n"):
            if line.lower().startswith("transfer-encoding:") and "chunked" in line.lower():
                return True
        return False

    def _close_connection(self, conn):
        """Cleans up session state and closes the client socket descriptor."""
        with self.sessions_lock:
            self._close_connection_locked(conn)

    def _close_connection_locked(self, conn):
        """Same as _close_connection, but assumes sessions_lock is already held."""
        if conn in self.sessions:
            del self.sessions[conn]
            self._selector_actions.put(("unregister", conn))
            try:
                conn.close()
            except Exception:
                pass
