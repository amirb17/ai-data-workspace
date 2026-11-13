"""CloudFront HTTPS can only be asserted under an opted-in, SG-isolated ALB path."""
import asyncio
from app.security.public_launch import (
    LaunchSettings, PublicLaunchBoundary, trusted_cloudfront_https,
)


def scope(*, host=b"api.example.org", client="172.31.64.135",
          cloudfront=b"https", forwarded=b"http", duplicate=False):
    headers = [(b"host", host), (b"cloudfront-forwarded-proto", cloudfront),
               (b"x-forwarded-proto", forwarded)]
    if duplicate:
        headers.append((b"CloudFront-Forwarded-Proto", b"https"))
    return dict(type="http", scheme="http", method="GET",
                path="/auth/config", raw_path=b"/auth/config", query_string=b"",
                http_version="1.1", client=(client, 1244),
                server=("172.31.8.16", 8000), headers=headers)


def test_transport_requires_explicit_optin_and_isolated_alb():
    enabled = LaunchSettings(True, ("api.example.org",), True)
    disabled = LaunchSettings(True, ("api.example.org",), False)
    good = scope()
    assert trusted_cloudfront_https(good, enabled)
    assert not trusted_cloudfront_https(good, disabled)
    assert not trusted_cloudfront_https(scope(cloudfront=b"http"), enabled)
    assert not trusted_cloudfront_https(scope(forwarded=b"https"), enabled)
    assert not trusted_cloudfront_https(scope(client="127.0.0.1"), enabled)
    assert not trusted_cloudfront_https(scope(client="1.2.3.4"), enabled)
    assert not trusted_cloudfront_https(scope(host=b"attacker.example.org"), enabled)
    assert not trusted_cloudfront_https(scope(duplicate=True), enabled)
    assert not trusted_cloudfront_https({**good, "headers": [(b"host", b"api.example.org"),
                                            (b"x-forwarded-proto", b"https")]}, enabled)


def test_boundary_upgrades_scheme_only_after_network_and_headers():
    from app.main import app
    observed = []
    async def responder(scope_, receive, send):
        observed.append(scope_["scheme"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def run(s, trust):
        gate = PublicLaunchBoundary(
            responder, settings=LaunchSettings(True, ("api.example.org",), trust),
            routes=app.router.routes,
        )
        messages = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            messages.append(message)
        await gate(s, receive, send)
        return messages[0]["status"], messages

    good = scope()
    status, messages = asyncio.run(run(good, True))
    assert status == 200
    assert observed == ["https"]
    assert good["scheme"] == "http"  # no mutation of ASGI input
    assert (b"strict-transport-security", b"max-age=31536000") in messages[0]["headers"]

    status, _ = asyncio.run(run(scope(forwarded=b"https"), True))
    assert status == 400
    status, _ = asyncio.run(run(scope(), False))
    assert status == 400
    status, _ = asyncio.run(run(scope(host=b"attacker.example.org"), True))
    assert status == 400
    assert observed == ["https"]
