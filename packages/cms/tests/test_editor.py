from redblue.cms.blocks import BLOCK_TYPES
from redblue.cms.editor import editor_spec

KINDS = {"text", "textarea", "media", "number", "checkbox", "select", "strings", "group", "rows"}


def _walk(fields):
    for f in fields:
        yield f
        yield from _walk(f.get("fields", []))


def test_every_block_type_and_field_has_a_form():
    spec = editor_spec([{"key": "hero.jpg", "label": "A hero"}])
    assert sorted(b["type"] for b in spec["blocks"]) == BLOCK_TYPES
    assert spec["media"] == [{"key": "hero.jpg", "label": "A hero"}]
    for block in spec["blocks"]:
        for field in _walk(block["fields"]):
            assert field["kind"] in KINDS, (block["type"], field)
            assert field["name"] != "type"


def test_fields_follow_the_models():
    blocks = {b["type"]: {f["name"]: f for f in b["fields"]} for b in editor_spec()["blocks"]}
    level = blocks["heading"]["level"]
    assert level["kind"] == "select" and level["options"] == [2, 3, 4] and level["default"] == 2
    assert blocks["heading"]["text"]["kind"] == "text"  # capped at 200: one line
    assert blocks["paragraph"]["text"]["kind"] == "textarea"
    assert "**bold**" in blocks["paragraph"]["text"]["hint"]
    assert blocks["image"]["media_key"]["kind"] == "media"
    assert blocks["image"]["alt"]["required"] is False and blocks["image"]["alt"]["default"] == ""
    assert blocks["table"]["rows"]["kind"] == "rows"
    faq = blocks["faq"]["items"]
    assert (
        faq["kind"] == "group"
        and faq["required"]
        and [f["name"] for f in faq["fields"]]
        == [
            "question",
            "answer",
        ]
    )
    assert blocks["testimonial"]["rating"] == {
        "name": "rating",
        "label": "Rating",
        "required": False,
        "kind": "number",
        "min": 1,
        "max": 5,
    }
    assert blocks["howto"]["supplies"]["default"] == []
