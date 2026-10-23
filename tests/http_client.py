def enable_csrf(client):
    csrf = client.get("/auth/csrf")
    assert csrf.status_code == 200

    client.headers.update(
        {
            "Origin": "http://localhost:5173",
            "X-CSRF-Token": csrf.json()["csrf_token"],
        }
    )

    return client