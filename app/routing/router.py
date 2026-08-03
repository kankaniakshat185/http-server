class Router:
    """
    Registry for HTTP route handlers. Resolves routing logic and dynamic URL parameters.
    Adheres to SOLID Single Responsibility and Open/Closed Principles.
    """
    def __init__(self):
        self.routes = {}

    def add_route(self, method: str, path_pattern: str, handler):
        """
        Registers a handler for a given HTTP method and path pattern.
        Example: router.add_route("GET", "/echo/:string", echo_handler)
        """
        method = method.upper()
        if method not in self.routes:
            self.routes[method] = []
        self.routes[method].append((path_pattern, handler))

    def match(self, method: str, path: str):
        """
        Matches an incoming request method and path to a registered handler.
        Returns (handler, path_params) or (None, {}).
        """
        method = method.upper()
        if method not in self.routes:
            return None, {}

        for pattern, handler in self.routes[method]:
            matched, params = self._match_path(pattern, path)
            if matched:
                return handler, params
        return None, {}

    def allowed_methods(self, path: str) -> list:
        """
        Returns every HTTP method that has a route registered for this path,
        regardless of which method actually matched. Used to distinguish a
        genuine 404 (no route at all) from a 405 (route exists, wrong verb).
        """
        methods = []
        for method, entries in self.routes.items():
            for pattern, _ in entries:
                matched, _ = self._match_path(pattern, path)
                if matched:
                    methods.append(method)
                    break
        return methods

    def _match_path(self, pattern: str, path: str) -> tuple[bool, dict]:
        """
        Splits and matches route parts. Resolves variables starting with ':'.
        """
        pattern_parts = [p for p in pattern.split("/") if p]
        path_parts = [p for p in path.split("/") if p]

        if len(pattern_parts) != len(path_parts):
            return False, {}

        params = {}
        for p_part, r_part in zip(pattern_parts, path_parts):
            if p_part.startswith(":"):
                param_name = p_part[1:]
                params[param_name] = r_part
            elif p_part != r_part:
                return False, {}

        return True, params
