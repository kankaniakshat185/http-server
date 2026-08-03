import socket
import threading
import time

from app.core.response import HTTPResponse
from app.core.server import HTTPServer


def _start_running_server(idle_timeout=None):
    server = HTTPServer(host="127.0.0.1", port=0, max_workers=2)
    server.router.add_route("GET", "/", lambda req: HTTPResponse(status=200, body=b"ok"))
    if idle_timeout is not None:
        server.IDLE_TIMEOUT_SECONDS = idle_timeout
    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()

    for _ in range(50):
        if server.server_socket is not None:
            break
        time.sleep(0.02)
    else:
        raise RuntimeError("server did not bind in time")

    port = server.server_socket.getsockname()[1]
    return server, thread, port


def test_shutdown_does_not_hang_with_open_keepalive_connection():
    """
    Regression test: stop() used to hold self.lock and then call
    _close_connection(), which re-acquired the same non-reentrant Lock,
    self-deadlocking any time stop() ran while a connection was still open.
    A default HTTP/1.1 request leaves its connection open (keep-alive), so
    this reproduces with a single ordinary request.
    """
    server, thread, port = _start_running_server()

    with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
        client.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        response = client.recv(4096)
        assert b"200 OK" in response

        # Connection is now idle, keep-alive, and still registered in
        # server.sessions - exactly the state that used to deadlock stop().
        server.running = False
        thread.join(timeout=2)

    assert not thread.is_alive(), "server.stop() hung - self-deadlock regression"


def test_oversized_content_length_is_rejected():
    server, thread, port = _start_running_server()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            oversized = server.MAX_BODY_BYTES + 1
            client.sendall(
                f"POST /files/x HTTP/1.1\r\nContent-Length: {oversized}\r\n\r\n".encode()
            )
            response = client.recv(4096)
            assert b"413" in response
    finally:
        server.running = False
        thread.join(timeout=2)


def test_malformed_request_line_gets_400():
    server, thread, port = _start_running_server()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(b"GET\r\n\r\n")  # request line needs 3 space-separated tokens
            response = client.recv(4096)
            assert b"400" in response
    finally:
        server.running = False
        thread.join(timeout=2)


def test_chunked_transfer_encoding_rejected_with_501():
    server, thread, port = _start_running_server()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(
                b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
            )
            response = client.recv(4096)
            assert b"501" in response
    finally:
        server.running = False
        thread.join(timeout=2)


def test_wrong_method_gets_405_with_allow_header():
    server, thread, port = _start_running_server()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            # "/" is only registered for GET.
            client.sendall(b"POST / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            response = client.recv(4096)
            assert b"405" in response
            assert b"Allow: GET" in response
    finally:
        server.running = False
        thread.join(timeout=2)


def test_missing_route_still_gets_404():
    server, thread, port = _start_running_server()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(b"GET /nope HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            response = client.recv(4096)
            assert b"404" in response
    finally:
        server.running = False
        thread.join(timeout=2)


def test_idle_connection_is_closed_after_timeout():
    """
    A client that opens a connection and never sends a request should be
    dropped after IDLE_TIMEOUT_SECONDS - this is the read-timeout mitigation
    docs/interview_defense.md describes for slow-drip (Slowloris-style)
    clients, bounding how many stalled connections can pile up in
    server.sessions.
    """
    server, thread, port = _start_running_server(idle_timeout=0.3)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.settimeout(2)
            # Send nothing - just hold the connection open.
            response = client.recv(4096)
            # The server closes its end; recv() on a closed peer returns b"".
            assert response == b""
    finally:
        server.running = False
        thread.join(timeout=2)
