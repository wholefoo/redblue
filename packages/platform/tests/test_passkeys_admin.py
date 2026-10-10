from fastapi.testclient import TestClient
from sqlalchemy import select

from redblue.core import passkeys as pk
from redblue.core.auth import Role, User, create_user
from redblue.core.testing_webauthn import SoftAuthenticator

ORIGIN, RP_ID = "http://testserver", "testserver"


def csrf(client):
    return client.cookies["rb_csrf"]


def _add_passkey(c, name="Laptop"):
    device = SoftAuthenticator(ORIGIN, RP_ID)
    r = c.post("/admin/passkeys/register/options")
    assert r.status_code == 200, r.text
    r = c.post("/admin/passkeys/register", json={"credential": device.create(r.text), "name": name})
    assert r.status_code == 200 and r.json() == {"ok": True, "name": name}, r.text
    return device


def _fresh_client(app):
    c = TestClient(app)
    c.get("/admin/login")
    c.headers["x-csrf-token"] = c.cookies["rb_csrf"]
    return c


def _sign_in(c, device, next_="/admin/content"):
    r = c.post("/admin/passkeys/login/options")
    assert r.status_code == 200
    return c.post("/admin/passkeys/login", json={"credential": device.get(r.text), "next": next_})


def test_add_a_passkey_then_sign_in_with_it(admin_client, app):
    device = _add_passkey(admin_client)
    page = admin_client.get("/admin/account").text
    assert "Laptop" in page and "never" in page

    c = _fresh_client(app)
    assert c.get("/admin", follow_redirects=False).status_code == 303  # signed out
    r = _sign_in(c, device)
    assert r.status_code == 200 and r.json() == {"ok": True, "redirect": "/admin/content"}
    assert c.get("/admin", follow_redirects=False).status_code == 200  # signed in
    assert "never" not in admin_client.get("/admin/account").text


def test_sign_in_redirect_stays_on_site(admin_client, app):
    device = _add_passkey(admin_client)
    r = _sign_in(_fresh_client(app), device, next_="https://evil.test/")
    assert r.json()["redirect"] == "/admin"


def test_passkey_routes_need_csrf_and_a_login(admin_client, app):
    c = TestClient(app)
    c.get("/admin/login")
    assert c.post("/admin/passkeys/login/options").status_code == 403  # no CSRF header
    c.headers["x-csrf-token"] = c.cookies["rb_csrf"]
    r = c.post("/admin/passkeys/register/options", follow_redirects=False)
    assert r.status_code == 303 and "/admin/login" in r.headers["location"]


def test_failed_sign_in_explains_and_sets_no_session(admin_client, app):
    stranger = SoftAuthenticator(ORIGIN, RP_ID)
    stranger.user_handle = b"someone"
    c = _fresh_client(app)
    r = _sign_in(c, stranger)
    assert r.status_code == 401 and "isn't registered" in r.json()["error"]
    assert c.get("/admin", follow_redirects=False).status_code == 303
    r = c.post("/admin/passkeys/login", json={"credential": "not an object"})
    assert r.status_code == 401 and "incomplete" in r.json()["error"]


def test_only_writers_and_up_can_sign_in_to_the_admin(admin_client, app):
    with app.state.rb.db.session() as db:
        create_user(db, "viewer@example.com", "viewer-password-1", Role.viewer)
    viewer = _fresh_client(app)
    r = viewer.post(
        "/admin/login",
        data={
            "email": "viewer@example.com",
            "password": "viewer-password-1",
            "csrf_token": viewer.cookies["rb_csrf"],
        },
    )
    assert r.status_code == 401  # viewers can't use the admin with a password either
    with app.state.rb.db.session() as db:
        user = db.scalar(select(User).where(User.email == "viewer@example.com"))
        device = SoftAuthenticator(ORIGIN, RP_ID)
        options, token = pk.registration_options(db, app.state.rb.settings, user)
        pk.finish_registration(db, app.state.rb.settings, user, token, device.create(options), "")
    r = _sign_in(_fresh_client(app), device)
    assert r.status_code == 401 and "can't use the admin" in r.json()["error"]


def test_remove_a_passkey(admin_client, app):
    device = _add_passkey(admin_client)
    page = admin_client.get("/admin/account").text
    pid = page.split('action="/admin/passkeys/')[1].split("/delete")[0]
    r = admin_client.post(f"/admin/passkeys/{pid}/delete", data={"csrf_token": csrf(admin_client)})
    assert r.status_code == 200 and "Passkey removed." in r.text
    assert _sign_in(_fresh_client(app), device).status_code == 401


def _stale_session(c, app):
    """A session cookie as signed before sign-in times were recorded: valid, but not recent."""
    from itsdangerous import URLSafeTimedSerializer

    with app.state.rb.db.session() as db:
        admin = db.scalar(select(User).where(User.email == "admin@example.com"))
        data = {"uid": admin.id, "v": admin.session_version}
    secret = app.state.rb.settings.secret_key.get_secret_value()
    stale = URLSafeTimedSerializer(secret, salt="rb-session").dumps(data)
    c.cookies.set("rb_session", stale, domain="testserver.local", path="/")  # replaces the real one


def test_adding_a_passkey_needs_a_recent_sign_in(admin_client, app):
    c = admin_client
    _stale_session(c, app)
    assert c.get("/admin", follow_redirects=False).status_code == 200  # still signed in
    r = c.post("/admin/passkeys/register/options")
    assert r.status_code == 403 and "confirm it's you" in r.json()["error"]
    page = c.get("/admin/account").text
    assert 'action="/admin/account/confirm"' in page and 'data-passkey="register"' not in page

    r = c.post("/admin/account/confirm", data={"csrf_token": csrf(c), "password": "wrong"})
    assert "Incorrect password." in r.text
    assert c.post("/admin/passkeys/register/options").status_code == 403

    r = c.post(
        "/admin/account/confirm",
        data={"csrf_token": csrf(c), "password": "correct-horse-battery"},
    )
    assert "You can add a passkey" in r.text and 'data-passkey="register"' in r.text
    _add_passkey(c)


def test_admins_remove_other_peoples_passkeys(admin_client, app):
    with app.state.rb.db.session() as db:
        writer = create_user(db, "w@example.com", "writer-password-1", Role.writer)
        device = SoftAuthenticator(ORIGIN, RP_ID)
        options, token = pk.registration_options(db, app.state.rb.settings, writer)
        passkey = pk.finish_registration(
            db, app.state.rb.settings, writer, token, device.create(options), "Lost phone"
        )
        wid, pid = writer.id, passkey.id
    page = admin_client.get("/admin/users").text
    assert "Lost phone" in page and f"/admin/users/{wid}/passkeys/{pid}/delete" in page

    # A writer can't use the admin route, even on their own passkey.
    w = _fresh_client(app)
    w.post(
        "/admin/login",
        data={"email": "w@example.com", "password": "writer-password-1", "csrf_token": csrf(w)},
    )
    assert w.post(f"/admin/users/{wid}/passkeys/{pid}/delete").status_code == 403
    assert admin_client.post(f"/admin/users/{wid + 99}/passkeys/{pid}/delete").status_code == 404

    r = admin_client.post(f"/admin/users/{wid}/passkeys/{pid}/delete")
    assert r.status_code == 200 and "Removed passkey" in r.text and "w@example.com." in r.text
    assert f"/passkeys/{pid}/delete" not in r.text
    assert _sign_in(_fresh_client(app), device).status_code == 401
