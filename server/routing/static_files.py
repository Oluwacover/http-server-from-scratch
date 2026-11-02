"""
Static File Handler with Path Traversal Protection and ETag Caching.
Serves static resources (HTML, CSS, JS, images) adhering to RFC 9110 conditional request standards.
"""

import os
import mimetypes
import hashlib
from typing import Optional
from server.parser.http_request import HttpRequest
from server.parser.http_response import HttpResponse


class StaticFileHandler:
    """
    Safely serves files from a designated directory root.
    """
    def __init__(self, directory: str, url_prefix: str = "/static", max_age: int = 3600):
        self.directory = os.path.abspath(directory)
        self.url_prefix = url_prefix.rstrip("/")
        self.max_age = max_age

    def handle(self, request: HttpRequest) -> HttpResponse:
        if not request.path.startswith(self.url_prefix):
            return HttpResponse.error(404, "Not found")

        rel_path = request.path[len(self.url_prefix):].lstrip("/\\")
        if not rel_path:
            rel_path = "index.html"

        # Resolve path safely to prevent directory traversal
        target_path = os.path.abspath(os.path.join(self.directory, rel_path))

        # Check directory confinement
        if not target_path.startswith(self.directory):
            return HttpResponse.error(403, "Access Forbidden: Path Traversal Detected")

        if not os.path.isfile(target_path):
            return HttpResponse.error(404, f"File not found: {rel_path}")

        # Compute file ETag based on mtime and size
        stat = os.stat(target_path)
        etag_raw = f"{stat.st_mtime}-{stat.st_size}".encode("ascii")
        etag = f'"{hashlib.md5(etag_raw).hexdigest()}"'

        # Check conditional request (If-None-Match)
        client_etag = request.headers.get("If-None-Match")
        if client_etag == etag:
            # 304 Not Modified
            resp = HttpResponse(status_code=304)
            resp.set_header("ETag", etag)
            return resp

        # Guess MIME type
        content_type, _ = mimetypes.guess_type(target_path)
        if not content_type:
            content_type = "application/octet-stream"

        try:
            with open(target_path, "rb") as f:
                content = f.read()
        except OSError as e:
            return HttpResponse.error(500, f"Error reading file: {e}")

        response = HttpResponse.raw(content, content_type=content_type)
        response.set_header("ETag", etag)
        response.set_header("Cache-Control", f"public, max-age={self.max_age}")
        return response
