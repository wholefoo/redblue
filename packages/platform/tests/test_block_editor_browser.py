"""The visual block editor in real Chromium: build a post by clicking and dragging, watch the
live preview (blocks and full page), save it, and check the stored blocks; open an entry using
every block type and save it unchanged through the editor (the JSON must come back
identical); bad JSON in the raw view is refused. Fails on console errors, including CSP
violations. Skipped without Playwright and Chromium, like test_admin_screens."""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

EVERY_BLOCK = [
    {"type": "heading", "level": 2, "text": "Heat pumps", "anchor": "heat-pumps"},
    {"type": "paragraph", "text": "They move heat, **not** make it. See [the guide](/guide)."},
    {"type": "list", "ordered": True, "items": ["Air source", "Ground source"]},
    {"type": "image", "src": "/_rb/favicon.svg", "alt": "A heat pump", "caption": "Outside"},
    {"type": "quote", "text": "Efficient in winter.", "cite": "An engineer"},
    {"type": "code", "language": "python", "code": "print('hi')"},
    {"type": "callout", "tone": "warning", "text": "Check your insulation first."},
    {"type": "answer", "question": "Do they work in the cold?", "answer": "Yes, down to -20C."},
    {"type": "faq", "items": [{"question": "How loud?", "answer": "About 40 dB."}]},
    {
        "type": "howto",
        "name": "Bleed a radiator",
        "total_time": "PT15M",
        "supplies": ["Radiator key"],
        "steps": [{"name": "Turn off", "text": "Switch the heating off."}],
    },
    {"type": "cta", "heading": "Get a quote", "button_label": "Ask us", "button_url": "/contact"},
    {"type": "embed", "provider": "youtube", "video_id": "dQw4w9WgXcQ", "title": "Explainer"},
    {"type": "table", "caption": "Costs", "headers": ["Type", "Cost"], "rows": [["Air", "8k"]]},
    {"type": "divider"},
    {"type": "features", "heading": "Why", "items": [{"title": "Cheap", "text": "Low bills."}]},
    {"type": "pricing", "plans": [{"name": "Basic", "price": "10", "features": ["Survey"]}]},
    {"type": "testimonial", "quote": "Great.", "author": "Sam", "rating": 5},
    {"type": "stats", "items": [{"value": "3x", "label": "Efficiency"}]},
    {"type": "form", "kind": "newsletter", "heading": "Updates"},
    {
        "type": "comparison",
        "columns": ["Air", "Ground"],
        "rows": [{"feature": "Cost", "values": ["Low", "High"]}],
        "verdict": "Air for most homes.",
    },
    {"type": "sources", "items": [{"title": "Energy agency", "url": "https://example.org/r"}]},
]


def _png(width: int = 8, height: int = 8) -> bytes:
    """A real (red) PNG, so the browser can draw the media thumbnails."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


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
def live(tmp_path, monkeypatch):
    import uvicorn

    from redblue.cms import service as cms
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
            base_url=f"http://127.0.0.1:{port}",
            email_from="owner@example.com",
        )
    )
    from redblue.cms.models import Media

    stored = app.state.rb.storage.put(_png(), "red-square.png")
    with app.state.rb.db.session() as db:
        db.add(
            Media(
                key=stored.key,
                mime=stored.mime,
                size=stored.size,
                sha256=stored.sha256,
                alt="A red square",
                width=8,
                height=8,
            )
        )
        admin = ensure_admin(db, "admin@example.com", "correct-horse-battery")
        entry = cms.create_entry(
            db, "post", cms.EntryInput(title="Every block", blocks=EVERY_BLOCK), admin
        )
        every_id, blocks = entry.id, entry.blocks
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", app, every_id, blocks, stored.key
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _blocks(app, entry_id):
    from redblue.cms.models import Entry

    with app.state.rb.db.session() as db:
        return db.get(Entry, entry_id).blocks


def test_block_editor(live):
    base, app, every_id, stored, media_key = live
    problems: list[str] = []
    with sync_api.sync_playwright() as p:
        browser = _browser(p)
        # Tall enough that every card is on screen: Playwright drags don't scroll mid-drag.
        page = browser.new_page(viewport={"width": 1280, "height": 2400})
        page.on(
            "console",
            lambda m: problems.append(f"console: {m.text[:200]}") if m.type == "error" else None,
        )
        page.on("pageerror", lambda e: problems.append(f"page error: {str(e)[:200]}"))
        page.goto(f"{base}/admin/login")
        page.fill('input[name="email"]', "admin@example.com")
        page.fill('input[name="password"]', "correct-horse-battery")
        page.click('form[action="/admin/login"] button[type="submit"]')
        page.wait_for_url(re.compile(r".*/admin/?$"))

        # 1. Build a post by clicking.
        page.goto(f"{base}/admin/content/new?collection=post")
        assert page.locator('textarea[name="blocks"]').is_hidden()
        editor = page.locator("#rb-blocks-editor")
        while editor.locator("section.rb-block").count():  # clear the post template's blocks
            editor.get_by_role("button", name="Remove block 1").click()
        assert editor.get_by_text("No blocks yet").is_visible()

        def add(kind):
            editor.locator('select[aria-label="Block type"]').select_option(kind)
            editor.get_by_role("button", name="Add block", exact=True).click()
            return editor.locator("section.rb-block").last

        para = add("paragraph")
        para.get_by_label("Text").fill("Second, after the move.")
        heading = add("heading")
        heading.get_by_label("Level (optional)").select_option("3")
        heading.get_by_label("Text", exact=True).fill("First")
        heading.get_by_role("button", name="Move block 2 up").click()
        faq = add("faq")
        faq.get_by_label("Question").fill("Is it visual?")
        faq.get_by_label("Answer").fill("Yes.")
        lst = add("list")
        lst.get_by_role("textbox", name="Items 1", exact=True).fill("One")
        lst.get_by_role("button", name="Add to items").click()
        lst.get_by_role("textbox", name="Items 2", exact=True).fill("Two")

        # Drag the list (block 4) by its handle above the FAQ (block 3), then back below it.
        def drag(source, target):
            editor.locator("section.rb-block").nth(source).locator(".rb-block__handle").drag_to(
                editor.locator("section.rb-block").nth(target),
                target_position={"x": 40, "y": 5},  # the top half: drop above the target
            )

        drag(3, 2)
        labels = editor.locator("section.rb-block").evaluate_all(
            "cards => cards.map(c => c.getAttribute('aria-label'))"
        )
        assert labels == ["Block 1: Heading", "Block 2: Paragraph", "Block 3: List", "Block 4: FAQ"]
        assert json.loads(page.locator('textarea[name="blocks"]').input_value())[2]["items"] == [
            "One",
            "Two",
        ]
        drag(2, 3)  # above the FAQ again: no move
        assert editor.locator("section.rb-block").nth(2).get_attribute("aria-label") == (
            "Block 3: List"
        )
        last = editor.locator("section.rb-block").nth(3)
        editor.locator("section.rb-block").nth(2).locator(".rb-block__handle").drag_to(
            last, target_position={"x": 40, "y": last.bounding_box()["height"] - 5}
        )
        assert editor.locator("section.rb-block").nth(3).get_attribute("aria-label") == (
            "Block 4: List"
        )
        assert not editor.locator(".rb-drop-before, .rb-drop-after, .is-dragging").count()

        # Insert an image between the heading and the paragraph, picked by its thumbnail.
        editor.get_by_role("button", name="Add a block below block 1").click()
        editor.get_by_label("Type of block to add below block 1").select_option("image")
        editor.get_by_role("button", name="Add block below block 1").click()
        image = editor.locator("section.rb-block").nth(1)
        assert image.get_attribute("aria-label") == "Block 2: Image"
        image.get_by_role("button", name="Use image: A red square").click()
        assert (
            image.locator(".rb-media-preview img").evaluate("i => i.complete && i.naturalWidth")
            == 8
        )
        image.get_by_label("Alt text (optional)").fill("A red square")

        # The live preview renders the blocks like the site does, and goes stale on errors.
        preview = page.locator(".rb-preview__body")
        preview.locator("h3", has_text="First").wait_for()
        assert preview.locator("img").evaluate("i => i.naturalWidth") == 8
        para = editor.locator("section.rb-block").nth(2)
        para.get_by_label("Text").fill("See [this](javascript:alert(1)).")
        page.get_by_text("Not updated until this is fixed").wait_for()
        assert "Unsafe or invalid link" in page.locator(".rb-preview").inner_text()
        assert "is-stale" in preview.get_attribute("class")
        para.get_by_label("Text").fill("Second, after the move.")
        page.wait_for_function(
            "() => !document.querySelector('.rb-preview__body').classList.contains('is-stale')"
        )
        assert preview.locator("p", has_text="Second, after the move.").is_visible()

        # The full page: the site's header and layout around the title and blocks, from the
        # form as it is now, in a sandboxed frame without scripts.
        page.fill('input[name="title"]', "Made visually")
        page.get_by_role("button", name="Full page").click()
        frame = page.frame_locator("iframe.rb-preview__frame")
        frame.locator("h1", has_text="Made visually").wait_for()
        assert frame.locator("h3", has_text="First").is_visible()
        assert frame.locator("header").count() and frame.locator("footer").count()
        assert frame.locator("script").count() == 0
        assert frame.locator("body").evaluate("b => getComputedStyle(b).margin") == "0px"
        assert not preview.is_visible()
        page.fill('input[name="title"]', "Made visually, live")
        frame.locator("h1", has_text="Made visually, live").wait_for()
        page.fill('input[name="title"]', "Made visually")
        frame.locator("h1", has_text=re.compile(r"^\s*Made visually\s*$")).wait_for()
        page.get_by_role("button", name="Blocks").click()
        preview.locator("h3", has_text="First").wait_for()
        assert page.locator("iframe.rb-preview__frame").is_hidden()

        page.get_by_role("button", name="Create draft").click()
        page.wait_for_url(re.compile(r".*/admin/content/\d+\?msg="))
        new_id = int(re.search(r"/admin/content/(\d+)", page.url).group(1))
        assert _blocks(app, new_id) == [
            {"type": "heading", "level": 3, "text": "First"},
            {"type": "image", "media_key": media_key, "alt": "A red square", "decorative": False},
            {"type": "paragraph", "text": "Second, after the move."},
            {"type": "faq", "items": [{"question": "Is it visual?", "answer": "Yes."}]},
            {"type": "list", "ordered": False, "items": ["One", "Two"]},
        ]

        # 2. Every block type round-trips unchanged through the editor.
        page.goto(f"{base}/admin/content/{every_id}")
        assert page.locator("section.rb-block").count() == len(EVERY_BLOCK)
        page.get_by_role("button", name="Edit as JSON").click()  # writes the editor's JSON
        assert json.loads(page.locator('textarea[name="blocks"]').input_value()) == stored
        page.get_by_role("button", name="Edit visually").click()
        page.get_by_role("button", name="Save draft").click()
        page.wait_for_url(re.compile(r".*\?msg="))
        assert _blocks(app, every_id) == stored

        # 3. Bad JSON stays in the raw view with an explanation.
        page.get_by_role("button", name="Edit as JSON").click()
        page.fill('textarea[name="blocks"]', "[{oops")
        page.get_by_role("button", name="Edit visually").click()
        assert page.get_by_text("The JSON isn't valid").is_visible()
        assert page.locator('textarea[name="blocks"]').is_visible()
        browser.close()
    assert not problems, "\n".join(problems)
