"""
HTTP Router and Middleware Pipeline.
Supports exact matching, parameterized paths (/users/{id}),
RFC 9110 compliant 405 Method Not Allowed with Allow headers, and middleware chaining.
"""

import re
from typing import Callable, Dict, List, Tuple, Optional
from server.parser.http_request import HttpRequest
from server.parser.http_response import HttpResponse

# Type alias for handlers and middleware
HandlerFunc = Callable[[HttpRequest], HttpResponse]
MiddlewareFunc = Callable[[HttpRequest, HandlerFunc], HttpResponse]


class RoutePattern:
    """
    Compiled route pattern supporting parameterized tokens like /users/{id}.
    """
    def __init__(self, path_pattern: str):
        self.raw_pattern = path_pattern
        # Convert /users/{id} into regex pattern: /users/(?P<id>[^/]+)
        regex_str = re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", r"(?P<\1>[^/]+)", path_pattern)
        self.regex = re.compile(f"^{regex_str}$")

    def match(self, path: str) -> Optional[Dict[str, str]]:
        match = self.regex.match(path)
        if match:
            return match.groupdict()
        return None


class Router:
    """
    High-level request router with middleware pipeline support.
    """
    def __init__(self):
        # List of (method, RoutePattern, HandlerFunc)
        self.routes: List[Tuple[str, RoutePattern, HandlerFunc]] = []
        self.middleware: List[MiddlewareFunc] = []

    def add_route(self, method: str, path: str, handler: HandlerFunc):
        """Register a route with an HTTP method and path pattern."""
        self.routes.append((method.upper(), RoutePattern(path), handler))

    def get(self, path: str):
        """Decorator for GET routes."""
        def decorator(handler: HandlerFunc):
            self.add_route("GET", path, handler)
            return handler
        return decorator

    def post(self, path: str):
        """Decorator for POST routes."""
        def decorator(handler: HandlerFunc):
            self.add_route("POST", path, handler)
            return handler
        return decorator

    def put(self, path: str):
        """Decorator for PUT routes."""
        def decorator(handler: HandlerFunc):
            self.add_route("PUT", path, handler)
            return handler
        return decorator

    def delete(self, path: str):
        """Decorator for DELETE routes."""
        def decorator(handler: HandlerFunc):
            self.add_route("DELETE", path, handler)
            return handler
        return decorator

    def use(self, middleware: MiddlewareFunc):
        """Append a middleware function to the execution chain."""
        self.middleware.append(middleware)

    def dispatch(self, request: HttpRequest) -> HttpResponse:
        """
        Match request against registered routes and execute middleware chain.
        """
        matching_routes = []
        allowed_methods = set()

        for method, pattern, handler in self.routes:
            params = pattern.match(request.path)
            if params is not None:
                allowed_methods.add(method)
                if method == request.method or method == "*":
                    matching_routes.append((handler, params))

        if not allowed_methods:
            return HttpResponse.error(404, f"No route matching '{request.path}'")

        if not matching_routes:
            # RFC 9110 Section 15.5.6: 405 Method Not Allowed MUST include an Allow header
            resp = HttpResponse.error(405, f"Method '{request.method}' not allowed for '{request.path}'")
            resp.set_header("Allow", ", ".join(sorted(allowed_methods)))
            return resp

        # Select first matching handler
        handler, params = matching_routes[0]
        request.path_params = params

        # Compose middleware pipeline
        def execute_handler(req: HttpRequest) -> HttpResponse:
            return handler(req)

        pipeline = execute_handler
        for mw in reversed(self.middleware):
            prev_next = pipeline

            def wrap_mw(m=mw, n=prev_next):
                return lambda req: m(req, n)

            pipeline = wrap_mw()

        return pipeline(request)
