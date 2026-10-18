import hmac
import threading
from wsgiref.simple_server import WSGIRequestHandler, make_server

from fastapi import HTTPException, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import settings


def metrics_response(request: Request):
    secret = settings().metrics_token.get_secret_value()
    if not secret or not hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + secret):
        raise HTTPException(401)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def worker_metrics():
    def application(environ, start_response):
        secret = settings().metrics_token.get_secret_value()
        if not secret or not hmac.compare_digest(environ.get("HTTP_AUTHORIZATION", ""), "Bearer " + secret):
            start_response("401 Unauthorized", [])
            return [b"Unauthorized"]
        start_response("200 OK", [("Content-Type", CONTENT_TYPE_LATEST)])
        return [generate_latest()]

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    import os
    server = make_server(os.getenv("METRICS_BIND", "127.0.0.1"), 8093, application, handler_class=QuietHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
