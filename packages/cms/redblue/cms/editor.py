"""Form description for the admin's visual block editor.

The editor (admin ``static/blocks-editor.js``) builds its forms from this description, which is
made from the Pydantic block models in :mod:`redblue.cms.blocks`. A new block type or field
shows up in the editor without touching the JavaScript. The models stay the only rules: the
editor writes the same JSON the API takes, and saving validates it exactly as before.
"""

from __future__ import annotations

import types
import typing
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from redblue.cms.blocks import Block

BLOCK_LABELS = {
    "heading": "Heading",
    "paragraph": "Paragraph",
    "list": "List",
    "image": "Image",
    "quote": "Quote",
    "code": "Code",
    "callout": "Callout",
    "answer": "Answer (question first)",
    "faq": "FAQ",
    "howto": "How-to",
    "cta": "Call to action",
    "embed": "Video",
    "table": "Table",
    "divider": "Divider",
    "features": "Features",
    "pricing": "Pricing",
    "testimonial": "Testimonial",
    "stats": "Stats",
    "form": "Form",
    "comparison": "Comparison",
    "sources": "Sources",
}

FIELD_LABELS = {
    "media_key": "Image from the media library",
    "src": "Image URL (if not from the library)",
    "alt": "Alt text",
    "decorative": "Decorative (no alt text needed)",
    "video_id": "Video ID",
    "total_time": "Total time (e.g. PT30M)",
    "button_label": "Button label",
    "button_url": "Button link",
    "cta_label": "Button label",
    "cta_url": "Button link",
    "lead_magnet_key": "Lead magnet",
    "image_key": "Image from the media library",
    "source_url": "Source link",
    "url": "Link",
    "verified": "Verified by an editor (real, attributable)",
}

# Picked from the media library (by key).
MEDIA_FIELDS = {"media_key", "image_key"}

# Fields shown as a multi-line box (unless capped at 300 chars); also anything longer.
LONG_TEXT = {"text", "answer", "detail", "code", "quote", "description"}

# Fields that accept **bold**, *italic*, `code` and [link](url).
INLINE_MARKUP = {
    ("paragraph", "text"),
    ("list", "items"),
    ("callout", "text"),
    ("faq.items", "answer"),
}

INLINE_HINT = "Supports **bold**, *italic*, `code` and [link text](https://…)."


class UnsupportedField(TypeError):
    """A block field the editor can't show yet; add a kind for it here and in the JS."""


def _label(name: str) -> str:
    return FIELD_LABELS.get(name, name.replace("_", " ").capitalize())


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    if get_origin(annotation) in (typing.Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1 and len(get_args(annotation)) == 2:
            return args[0], True
    return annotation, False


def _max_length(info) -> int | None:
    for m in info.metadata:
        if getattr(m, "max_length", None) is not None:
            return m.max_length
    return None


def _bounds(info) -> dict:
    out = {}
    for m in info.metadata:
        for key in ("ge", "le"):
            if getattr(m, key, None) is not None:
                out["min" if key == "ge" else "max"] = getattr(m, key)
    return out


def _field(owner: str, name: str, info) -> dict:
    annotation, optional = _unwrap_optional(info.annotation)
    spec: dict = {"name": name, "label": _label(name), "required": info.is_required()}
    if optional:
        spec["required"] = False
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Literal:
        spec.update(kind="select", options=list(args))
    elif annotation is bool:
        spec["kind"] = "checkbox"
    elif annotation is int:
        spec.update(kind="number", **_bounds(info))
    elif annotation is str and name in MEDIA_FIELDS:
        spec["kind"] = "media"
    elif annotation is str:
        limit = _max_length(info)
        long = (limit or 0) > 300 or (name in LONG_TEXT and limit is None)
        spec["kind"] = "textarea" if long else "text"
        if _max_length(info):
            spec["maxlength"] = _max_length(info)
    elif origin is list and args == (str,):
        spec["kind"] = "strings"
    elif origin is list and get_origin(args[0]) is list and get_args(args[0]) == (str,):
        spec["kind"] = "rows"
    elif origin is list and isinstance(args[0], type) and issubclass(args[0], BaseModel):
        spec.update(kind="group", item=args[0].__name__, fields=_fields(f"{owner}.{name}", args[0]))
    else:
        raise UnsupportedField(f"{owner}.{name}: {info.annotation!r}")
    if (owner, name) in INLINE_MARKUP:
        spec["hint"] = INLINE_HINT
    default = info.default
    if default is not PydanticUndefined and default is not None:
        spec["default"] = default
    elif info.default_factory is list:
        spec["default"] = []
    return spec


def _fields(owner: str, model: type[BaseModel]) -> list[dict]:
    return [
        _field(owner, name, info) for name, info in model.model_fields.items() if name != "type"
    ]


def block_models() -> list[type[BaseModel]]:
    union = get_args(Block)[0]
    return list(get_args(union))


def editor_spec(media: list[dict] | None = None) -> dict:
    """``{"blocks": [{type, label, help, fields}], "media": [{key, label}]}`` for the editor."""
    blocks = []
    for model in block_models():
        kind = model.model_fields["type"].default
        fields = _fields(kind, model)
        doc = (model.__doc__ or "").strip().split("\n")[0]
        if any("hint" in f for f in fields):
            doc = ""  # the field's own hint already explains the markup
        blocks.append(
            {
                "type": kind,
                "label": BLOCK_LABELS.get(kind, kind.capitalize()),
                "help": doc,
                "fields": fields,
            }
        )
    return {"blocks": blocks, "media": media or []}
