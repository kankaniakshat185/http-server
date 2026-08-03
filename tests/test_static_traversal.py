from app.middleware.static import StaticFilesMiddleware


class FakeRequest:
    def __init__(self, method, path, body=b""):
        self.method = method
        self.path = path
        self.body = body


def _next_handler_should_not_run(request):
    raise AssertionError("next_handler should not be reached for /files/ requests")


def test_serves_file_within_directory(tmp_path):
    (tmp_path / "hello.txt").write_bytes(b"hello world")
    middleware = StaticFilesMiddleware(path_prefix="/files/", directory=str(tmp_path))

    response = middleware(FakeRequest("GET", "/files/hello.txt"), _next_handler_should_not_run)

    assert response.status == 200
    assert response.body == b"hello world"


def test_rejects_dot_dot_traversal_outside_directory(tmp_path):
    target_dir = tmp_path / "data"
    target_dir.mkdir()
    (tmp_path / "secret.txt").write_bytes(b"top secret")

    middleware = StaticFilesMiddleware(path_prefix="/files/", directory=str(target_dir))
    response = middleware(FakeRequest("GET", "/files/../secret.txt"), _next_handler_should_not_run)

    assert response.status == 403


def test_rejects_sibling_directory_prefix_bypass(tmp_path):
    # Regression test for CWE-22 partial-prefix bypass: a naive
    # `resolved.startswith(self.directory)` check incorrectly treats
    # "<tmp>/data-evil" as being inside "<tmp>/data", since the string
    # "data-evil" starts with "data".
    target_dir = tmp_path / "data"
    target_dir.mkdir()
    sibling_dir = tmp_path / "data-evil"
    sibling_dir.mkdir()
    (sibling_dir / "secret.txt").write_bytes(b"top secret")

    middleware = StaticFilesMiddleware(path_prefix="/files/", directory=str(target_dir))
    response = middleware(FakeRequest("GET", "/files/../data-evil/secret.txt"), _next_handler_should_not_run)

    assert response.status == 403


def test_post_writes_file(tmp_path):
    middleware = StaticFilesMiddleware(path_prefix="/files/", directory=str(tmp_path))

    response = middleware(FakeRequest("POST", "/files/upload.txt", body=b"payload"), _next_handler_should_not_run)

    assert response.status == 201
    assert (tmp_path / "upload.txt").read_bytes() == b"payload"


def test_delete_removes_file(tmp_path):
    (tmp_path / "gone.txt").write_bytes(b"bye")
    middleware = StaticFilesMiddleware(path_prefix="/files/", directory=str(tmp_path))

    response = middleware(FakeRequest("DELETE", "/files/gone.txt"), _next_handler_should_not_run)

    assert response.status == 204
    assert not (tmp_path / "gone.txt").exists()


def test_large_file_is_streamed_not_buffered(tmp_path):
    big_content = b"a" * (StaticFilesMiddleware.STREAM_THRESHOLD_BYTES + 1)
    (tmp_path / "big.bin").write_bytes(big_content)
    middleware = StaticFilesMiddleware(path_prefix="/files/", directory=str(tmp_path))

    response = middleware(FakeRequest("GET", "/files/big.bin"), _next_handler_should_not_run)

    assert response.status == 200
    assert response.stream_path is not None
    assert response.body == b""

    class FakeConn:
        def __init__(self):
            self.sent = bytearray()

        def sendall(self, data):
            self.sent.extend(data)

    conn = FakeConn()
    response.write_to(conn)
    raw = bytes(conn.sent)
    header_end = raw.index(b"\r\n\r\n") + 4
    assert raw[header_end:] == big_content


def test_non_matching_prefix_falls_through_to_next_handler(tmp_path):
    middleware = StaticFilesMiddleware(path_prefix="/files/", directory=str(tmp_path))
    sentinel = object()

    result = middleware(FakeRequest("GET", "/echo/hi"), lambda request: sentinel)

    assert result is sentinel
