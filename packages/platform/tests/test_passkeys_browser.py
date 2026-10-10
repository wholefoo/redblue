"""Passkeys in real Chromium, with its built-in virtual authenticator (DevTools WebAuthn
domain): add a passkey on the account page, sign out, sign back in with it. Runs the actual
static/passkeys.js under the real CSP. Skipped without Playwright and a Chromium build, like
test_admin_screens. WebAuthn needs a domain, not an IP, so the app is served at localhost."""

from __future__ import annotations

import os
import re
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _browser(p):
    try:
        return p.chromium.launch()
    except sync_api.Error as exc:
        path = os.environ.get("RB_CHROMIUM_PATH")
        if not path:
            pytest.skip(f"No Chromium for Playwright ({str(exc).splitlines()[0][:120]})")
        return p.chromium.launch(executable_path=path)


@pytest.fixture
def live_site(tmp_path, monkeypatch):
    import uvicorn

    from redblue.core.config import Settings
    from redblue.core.security import limiter
    from redblue.platform.app import create_app
    from redblue.platform.seed import ensure_admin

    monkeypatch.chdir(tmp_path)
    limiter.reset()
    port = _free_port()
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path}/t.db",
            env="test",
            storage_dir=tmp_path / "media",
            secret_key="k" * 48,
            base_url=f"http://localhost:{port}",
            email_from="owner@example.com",
        )
    )
    with app.state.rb.db.session() as db:
        ensure_admin(db, "admin@example.com", "correct-horse-battery")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://localhost:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_add_a_passkey_and_sign_in_with_it(live_site):
    base = live_site
    problems: list[str] = []
    with sync_api.sync_playwright() as p:
        browser = _browser(p)
        page = browser.new_page()
        page.on(
            "console",
            lambda m: problems.append(f"console: {m.text[:200]}") if m.type == "error" else None,
        )
        page.on("pageerror", lambda e: problems.append(f"page error: {str(e)[:200]}"))
        cdp = page.context.new_cdp_session(page)
        cdp.send("WebAuthn.enable")
        cdp.send(
            "WebAuthn.addVirtualAuthenticator",
            {
                "options": {
                    "protocol": "ctap2",
                    "transport": "internal",
                    "hasResidentKey": True,
                    "hasUserVerification": True,
                    "isUserVerified": True,
                }
            },
        )

        page.goto(f"{base}/admin/login")
        page.fill('input[name="email"]', "admin@example.com")
        page.fill('input[name="password"]', "correct-horse-battery")
        page.click('form[action="/admin/login"] button[type="submit"]')
        page.wait_for_url(re.compile(r".*/admin/?$"))

        page.click('a[href="/admin/account"]')
        page.fill('[data-passkey="register"] input[name="name"]', "Virtual key")
        page.click('[data-passkey="register"] button[type="submit"]')
        page.wait_for_url(re.compile(r".*/admin/account\?msg=Passkey"))
        assert page.get_by_text("Virtual key").is_visible()

        page.click('form[action="/admin/logout"] button')
        page.wait_for_url(re.compile(r".*/admin/login"))
        page.get_by_role("button", name="Sign in with a passkey").click()
        page.wait_for_url(re.compile(r".*/admin/?$"))
        assert page.get_by_text("admin@example.com").is_visible()

        page.goto(f"{base}/admin/account")
        assert "never" not in page.locator("table").inner_text()  # last used is recorded
        browser.close()
    assert not problems, "\n".join(problems)
