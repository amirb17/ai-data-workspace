"""Public-launch health route remains exact and narrowly scoped."""
import asyncio

from app.security.public_launch import LaunchSettings, PublicLaunchBoundary


def _request(path="/health", method="GET", host=b"10.0.0.10", query=b""):
    return {
        "type": "http", "scheme": "http", "method": method,
        "path": path, "raw_path": path.encode(),
        "query_string": query,
        "headers": [(b"host", host)],
        "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 5151),
        "http_version": "1.1",
    }


def test_health_exception_is_exact_endpoint_and_get_only():
    from app.main import app
    boundary = next(
        m for m in app.user_middleware
        if m.cls is PublicLaunchBoundary
    )
    # Drive a dedicated boundary without changing process-wide prod config.
    async def responder(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gate = PublicLaunchBoundary(responder, settings=LaunchSettings(True, ("api.example.org",)),
                                routes=app.router.routes)
    async def invoke(scope):
        messages = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            messages.append(message)
        await gate(scope, receive, send)
        return messages[0]["status"]
    assert asyncio.run(invoke(_request())) == 200
    assert asyncio.run(invoke(_request(method="POST"))) == 400
    assert asyncio.run(invoke(_request(path="/health/db"))) == 400
    assert asyncio.run(invoke(_request(query=b"foo=1"))) == 400
