import logging
import sys
from app.core.server import HTTPServer
from app.core.request import HTTPRequest
from app.core.response import HTTPResponse
from app.middleware.logger import logger_middleware
from app.middleware.static import StaticFilesMiddleware

def handle_root(request: HTTPRequest) -> HTTPResponse:
    return HTTPResponse(status=200)

def handle_echo(request: HTTPRequest) -> HTTPResponse:
    # Retrieve dynamic path parameter
    echo_str = request.path_params.get("string", "")
    return HTTPResponse(
        status=200,
        headers={"Content-Type": "text/plain"},
        body=echo_str.encode("utf-8")
    )

def handle_user_agent(request: HTTPRequest) -> HTTPResponse:
    user_agent = request.get_header("User-Agent")
    return HTTPResponse(
        status=200,
        headers={"Content-Type": "text/plain"},
        body=user_agent.encode("utf-8")
    )

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    if len(sys.argv) < 3 or sys.argv[1] != "--directory":
        print("Usage: python3 app/main.py --directory <path>", file=sys.stderr)
        sys.exit(1)
    directory = sys.argv[2]

    # Instantiate core HTTP Server
    server = HTTPServer(host="0.0.0.0", port=4221, max_workers=8)
    
    # Register middlewares in Chain of Responsibility pipeline
    server.pipeline.use(logger_middleware)
    server.pipeline.use(StaticFilesMiddleware(path_prefix="/files/", directory=directory))
    
    # Register dynamic routes
    server.router.add_route("GET", "/", handle_root)
    server.router.add_route("GET", "/echo/:string", handle_echo)
    server.router.add_route("GET", "/user-agent", handle_user_agent)
    
    # Start loop
    server.start()

if __name__ == "__main__":
    main()
