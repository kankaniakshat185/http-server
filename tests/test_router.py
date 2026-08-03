from app.routing.router import Router


def test_exact_match():
    router = Router()
    router.add_route("GET", "/", lambda req: "root")
    handler, params = router.match("GET", "/")
    assert handler is not None
    assert params == {}


def test_path_param_match():
    router = Router()
    handler_fn = lambda req: "echo"
    router.add_route("GET", "/echo/:string", handler_fn)
    handler, params = router.match("GET", "/echo/hello")
    assert handler is handler_fn
    assert params == {"string": "hello"}


def test_no_match_returns_none():
    router = Router()
    router.add_route("GET", "/", lambda req: "root")
    handler, params = router.match("GET", "/missing")
    assert handler is None
    assert params == {}


def test_method_mismatch_returns_none():
    router = Router()
    router.add_route("GET", "/", lambda req: "root")
    handler, params = router.match("POST", "/")
    assert handler is None


def test_segment_count_mismatch():
    router = Router()
    router.add_route("GET", "/echo/:string", lambda req: "echo")
    handler, params = router.match("GET", "/echo/a/b")
    assert handler is None


def test_allowed_methods_reports_other_registered_verbs():
    router = Router()
    router.add_route("GET", "/echo/:string", lambda req: "echo")
    router.add_route("PUT", "/echo/:string", lambda req: "echo-put")

    assert set(router.allowed_methods("/echo/hi")) == {"GET", "PUT"}


def test_allowed_methods_empty_for_unregistered_path():
    router = Router()
    router.add_route("GET", "/", lambda req: "root")

    assert router.allowed_methods("/nope") == []
