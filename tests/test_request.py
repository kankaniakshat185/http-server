import pytest

from app.core.request import HTTPRequest


def test_parse_basic_get():
    raw = b"GET /echo/hi HTTP/1.1\r\nHost: localhost\r\nUser-Agent: test\r\n"
    request = HTTPRequest.parse(raw, b"")
    assert request.method == "GET"
    assert request.path == "/echo/hi"
    assert request.version == "HTTP/1.1"
    assert request.get_header("user-agent") == "test"


def test_header_lookup_is_case_insensitive():
    raw = b"GET / HTTP/1.1\r\nUSER-AGENT: weird-case\r\n"
    request = HTTPRequest.parse(raw, b"")
    assert request.get_header("User-Agent") == "weird-case"
    assert request.get_header("user-agent") == "weird-case"


def test_query_params_parsed():
    raw = b"GET /search?q=hello&empty HTTP/1.1\r\n"
    request = HTTPRequest.parse(raw, b"")
    assert request.path == "/search"
    assert request.query_params == {"q": "hello", "empty": ""}


def test_empty_request_line_raises():
    with pytest.raises(ValueError):
        HTTPRequest.parse(b"", b"")


def test_malformed_request_line_raises():
    with pytest.raises(ValueError):
        HTTPRequest.parse(b"GET /\r\n", b"")
