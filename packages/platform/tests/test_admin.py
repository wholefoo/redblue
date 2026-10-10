import json
import re


def test_admin_requires_login_and_csrf(client):
    r = client.get("/admin/content", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/login?next=")
    bad = client.post(
        "/admin/login",
        data={
            "email": "admin@example.com",
            "password": "wrong-password!!",
            "csrf_token": csrf(client),
        },
    )
    assert bad.status_code == 401
    del client.headers["x-csrf-token"]
    r = client.post(
        "/admin/login", data={"email": "admin@example.com", "password": "correct-horse-battery"}
    )
    assert r.status_code == 403  # no CSRF token


def test_admin_pages_render(admin_client):
    for path in [
        "/admin",
        "/admin/content",
        "/admin/content/new?collection=post",
        "/admin/media",
        "/admin/redirects",
        "/admin/audience",
        "/admin/growth",
        "/admin/growth/social",
        "/admin/experiments",
        "/admin/security",
        "/admin/observe",
        "/admin/templates",
        "/admin/users",
    ]:
        r = admin_client.get(path)
        assert r.status_code == 200, (path, r.text[:500])


def test_content_lifecycle_through_admin(admin_client):
    c = admin_client
    blocks = [
        {
            "type": "answer",
            "question": "What is RedBlue?",
            "answer": "A secure, self-hosted web platform.",
        }
    ]
    r = c.post(
        "/admin/content/new?collection=post",
        data={
            "csrf_token": csrf(c),
            "title": "Admin post",
            "summary": "Made in the admin",
            "blocks": json.dumps(blocks),
            "data": "{}",
            "target_questions": "What is RedBlue?",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200 and "Draft created" in r.text
    eid = int(re.search(r"/admin/content/(\d+)/workflow", r.text).group(1))
    assert c.get("/blog/admin-post").status_code == 404
    assert c.get(f"/admin/content/{eid}/preview").status_code == 200
    for action in ("submit", "approve", "publish"):
        r = c.post(f"/admin/content/{eid}/workflow", data={"csrf_token": csrf(c), "action": action})
        assert r.status_code == 200
    assert "A secure, self-hosted web platform." in c.get("/blog/admin-post").text

    bad = c.post(
        f"/admin/content/{eid}",
        data={
            "csrf_token": csrf(c),
            "title": "Admin post",
            "data": "{}",
            "blocks": json.dumps([{"type": "paragraph", "text": "[x](javascript:alert(1))"}]),
        },
    )
    assert "Please fix" in bad.text and "Unsafe" in bad.text


def test_thin_programmatic_page_blocked(admin_client):
    c = admin_client
    r = c.post(
        "/admin/content/new?collection=location",
        data={
            "csrf_token": csrf(c),
            "title": "Plumber in Springfield",
            "blocks": json.dumps([{"type": "paragraph", "text": "We fix pipes."}]),
            "data": json.dumps({"name": "Acme Plumbing", "city": "Springfield"}),
        },
        follow_redirects=True,
    )
    eid = int(re.search(r"/admin/content/(\d+)/workflow", r.text).group(1))
    for action in ("submit", "approve"):
        c.post(f"/admin/content/{eid}/workflow", data={"csrf_token": csrf(c), "action": action})
    r = c.post(f"/admin/content/{eid}/workflow", data={"csrf_token": csrf(c), "action": "publish"})
    assert "Thin content" in r.text
    assert c.get("/locations/plumber-in-springfield").status_code == 404


def test_media_upload_requires_alt_and_real_images(admin_client):
    c = admin_client
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        + (4).to_bytes(4, "big")
        + (3).to_bytes(4, "big")
        + b"\x00" * 20
    )
    r = c.post(
        "/admin/media",
        data={"csrf_token": csrf(c), "alt": "A tiny square"},
        files={"file": ("pic.png", png, "image/png")},
        follow_redirects=True,
    )
    assert "Uploaded" in r.text
    key = re.search(r"media_key “([^”]+)”", r.text).group(1)
    img = c.get(f"/media/{key}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    r = c.post(
        "/admin/media",
        data={"csrf_token": csrf(c), "alt": "x"},
        files={"file": ("x.svg", b"<svg onload=alert(1)>", "image/svg+xml")},
        follow_redirects=True,
    )
    assert "Unsupported file type" in r.text


def test_users_and_roles(admin_client, app):
    c = admin_client
    r = c.post(
        "/admin/users", data={"csrf_token": csrf(c), "email": "w@example.com", "role": "writer"}
    )
    pw = re.search(r"shown once\): <code>([^<]+)</code>", r.text).group(1)
    from fastapi.testclient import TestClient

    w = TestClient(app)
    w.get("/")
    w.post(
        "/admin/login",
        data={"email": "w@example.com", "password": pw, "csrf_token": w.cookies["rb_csrf"]},
    )
    assert w.get("/admin/users").status_code == 403
    assert w.get("/admin/content").status_code == 200


def test_growth_audit_and_report(admin_client):
    c = admin_client
    r = c.post("/admin/growth/audit", data={"csrf_token": csrf(c)})
    assert r.status_code == 200 and "SEO/AEO audit" in r.text
    r = c.post("/admin/growth/report", data={"csrf_token": csrf(c)}, follow_redirects=True)
    assert "Growth report generated" in r.text
    r = c.post(
        "/admin/growth/utm",
        data={
            "csrf_token": csrf(c),
            "url": "https://x.com/p",
            "source": "news",
            "medium": "email",
            "campaign": "launch",
        },
    )
    assert "utm_source=news" in r.text


def csrf(client):
    return client.cookies["rb_csrf"]


def test_back_only_redirects_within_the_site():
    from redblue.admin.app import back

    ok = back("/admin/video/projects/3?open=vimeo#upload-vimeo", "Vimeo upload failed: x")
    assert ok.headers["location"] == (
        "/admin/video/projects/3?open=vimeo&msg=Vimeo%20upload%20failed%3A%20x#upload-vimeo"
    )
    for target in ("https://evil.example/x", "//evil.example/x", "/\\evil.example", "admin"):
        assert back(target, "hi").headers["location"] == "/admin"


def test_admin_explains_images_from_hosts_not_allowed(admin_client, app):
    c = admin_client
    image = {"type": "image", "src": "https://elsewhere.example/a.png", "alt": "A picture"}
    r = c.post(
        "/admin/content/new?collection=post",
        data={
            "csrf_token": csrf(c),
            "title": "Outside image",
            "blocks": json.dumps([image]),
            "data": "{}",
        },
    )
    assert r.status_code == 200 and "Draft created" not in r.text
    assert "blocked by the site&#39;s security policy" in r.text and "RB_IMAGE_HOSTS" in r.text

    app.state.rb.settings.image_hosts = ["https://elsewhere.example"]
    r = c.post(
        "/admin/content/new?collection=post",
        data={
            "csrf_token": csrf(c),
            "title": "Outside image",
            "blocks": json.dumps([image]),
            "data": "{}",
        },
        follow_redirects=True,
    )
    assert "Draft created" in r.text


def test_block_preview_renders_like_the_site_and_validates_like_a_save(admin_client, app):
    c = admin_client
    url = "/admin/content/preview-blocks"
    blocks = [
        {"type": "heading", "text": "Hello <b>there</b>", "level": 3},
        {"type": "paragraph", "text": "**Bold** and [a link](/about)."},
    ]
    html = c.post(url, json={"blocks": blocks}).json()["html"]
    assert "Hello &lt;b&gt;there&lt;/b&gt;</h3>" in html  # escaped, as on the site
    assert '<strong>Bold</strong> and <a href="/about">a link</a>' in html

    bad = c.post(url, json={"blocks": [{"type": "paragraph", "text": "[x](javascript:alert(1))"}]})
    assert bad.status_code == 200 and "Unsafe or invalid link" in bad.json()["errors"][0]
    image = {"type": "image", "src": "https://elsewhere.example/a.png", "alt": "A picture"}
    assert "RB_IMAGE_HOSTS" in c.post(url, json={"blocks": [image]}).json()["errors"][0]
    assert c.post(url, json={"nope": 1}).status_code == 400

    no_csrf = c.post(url, json={"blocks": []}, headers={"x-csrf-token": "wrong"})
    assert no_csrf.status_code == 403
    from fastapi.testclient import TestClient

    anon = TestClient(app)
    anon.get("/admin/login")
    anon.headers["x-csrf-token"] = anon.cookies["rb_csrf"]
    assert anon.post(url, json={"blocks": []}, follow_redirects=False).status_code == 303


def _publish(c, title, text):
    r = c.post(
        "/admin/content/new?collection=post",
        data={
            "csrf_token": csrf(c),
            "title": title,
            "blocks": json.dumps([{"type": "paragraph", "text": text}]),
            "data": "{}",
        },
        follow_redirects=True,
    )
    eid = int(re.search(r"/admin/content/(\d+)/workflow", r.text).group(1))
    for action in ("submit", "approve", "publish"):
        c.post(f"/admin/content/{eid}/workflow", data={"csrf_token": csrf(c), "action": action})
    return eid


def test_previewing_a_draft_never_changes_the_live_page(admin_client):
    c = admin_client
    eid = _publish(c, "Live one", "Published text")
    c.post(
        f"/admin/content/{eid}",
        data={
            "csrf_token": csrf(c),
            "title": "Live one",
            "slug": "live-one",
            "blocks": json.dumps([{"type": "paragraph", "text": "Unreviewed draft"}]),
            "data": "{}",
        },
    )
    r = c.get(f"/admin/content/{eid}/preview")
    assert "Unreviewed draft" in r.text and r.headers["x-robots-tag"] == "noindex"
    form = {"title": "Renamed", "slug": "other-slug", "blocks": "[]", "data": "{}"}
    c.post(f"/admin/content/preview-page?entry_id={eid}", data=form)
    live = c.get("/blog/live-one")
    assert "Published text" in live.text and "Unreviewed draft" not in live.text
    assert c.get("/blog/other-slug").status_code == 404


def test_full_page_preview_renders_the_unsaved_form(admin_client):
    c = admin_client
    url = "/admin/content/preview-page?collection=post"
    form = {
        "title": "Heat <pumps>",
        "summary": "A summary",
        "blocks": json.dumps([{"type": "heading", "text": "First", "level": 3}]),
        "data": "{}",
        "tags": "energy",
    }
    html = c.post(url, data=form).json()["html"]
    assert html.lower().startswith("<!doctype html>")
    assert "Heat &lt;pumps&gt;" in html and "First</h3>" in html  # title and blocks, escaped
    assert "<header" in html and "<footer" in html  # the site's layout around them
    assert "Untitled" in c.post(url, data={**form, "title": ""}).json()["html"]

    bad = {**form, "blocks": json.dumps([{"type": "paragraph", "text": "[x](javascript:1)"}])}
    assert "Unsafe or invalid link" in c.post(url, data=bad).json()["errors"][0]
    assert "valid JSON" in c.post(url, data={**form, "data": "{oops"}).json()["errors"][0]
    assert c.post("/admin/content/preview-page?collection=nope", data=form).status_code == 404
    assert c.post("/admin/content/preview-page?entry_id=999", data=form).status_code == 404
    assert c.post(url, data=form, headers={"x-csrf-token": "wrong"}).status_code == 403
    assert c.get("/admin/content").status_code == 200
    assert "Heat" not in c.get("/admin/content").text  # nothing was stored
