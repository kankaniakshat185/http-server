import gzip

from app.core.response import HTTPResponse


def test_to_bytes_basic():
    response = HTTPResponse(status=200, headers={"Content-Type": "text/plain"}, body=b"hello")
    raw = response.to_bytes()
    assert raw.startswith(b"HTTP/1.1 200 OK\r\n")
    assert b"Content-Length: 5\r\n" in raw
    assert raw.endswith(b"hello")


def test_gzip_negotiated_when_supported():
    body = b"a" * 100
    response = HTTPResponse(status=200, body=body)
    raw = response.to_bytes(client_supports_gzip=True)
    assert b"Content-Encoding: gzip\r\n" in raw

    header_end = raw.index(b"\r\n\r\n") + 4
    compressed_body = raw[header_end:]
    assert gzip.decompress(compressed_body) == body


def test_no_gzip_when_unsupported():
    response = HTTPResponse(status=200, body=b"plain body")
    raw = response.to_bytes(client_supports_gzip=False)
    assert b"Content-Encoding" not in raw
    assert raw.endswith(b"plain body")


def test_unknown_status_code_falls_back_to_generic_reason():
    response = HTTPResponse(status=999)
    raw = response.to_bytes()
    assert raw.startswith(b"HTTP/1.1 999 Unknown\r\n")


def test_gzip_skipped_for_already_compressed_content_type():
    response = HTTPResponse(status=200, headers={"Content-Type": "image/png"}, body=b"\x89PNG" * 50)
    raw = response.to_bytes(client_supports_gzip=True)
    assert b"Content-Encoding" not in raw


class _FakeConn:
    def __init__(self):
        self.sent = bytearray()

    def sendall(self, data):
        self.sent.extend(data)


def test_write_to_sends_small_body_in_one_shot():
    response = HTTPResponse(status=200, body=b"hello")
    conn = _FakeConn()
    response.write_to(conn)
    assert bytes(conn.sent).endswith(b"hello")


def test_write_to_streams_stream_path_in_chunks(tmp_path):
    content = b"x" * 200_000
    file_path = tmp_path / "big.bin"
    file_path.write_bytes(content)

    response = HTTPResponse(status=200, headers={"Content-Type": "application/octet-stream"}, stream_path=str(file_path))
    conn = _FakeConn()
    response.write_to(conn)

    raw = bytes(conn.sent)
    header_end = raw.index(b"\r\n\r\n") + 4
    assert raw.startswith(b"HTTP/1.1 200 OK\r\n")
    assert f"Content-Length: {len(content)}".encode() in raw[:header_end]
    assert raw[header_end:] == content
