"""Music idea drafting through Hermes' public plugin LLM (`ctx.llm`).

Review item 1. The previous version spawned `hermes --print-runtime-command` and ran a worker that
imported Hermes' private modules and built an `AIAgent` with resolved provider credentials. Drafting now
goes through `music_studio.llm_bridge`, which calls `ctx.llm.complete` in the plugin process: the host
owns the provider, the model, the credentials and the timeouts, and this plugin never sees a key.

Cover-art *generation* is gone with it: it reached into `tools.image_generation_tool._handle_image_generate`,
which is core's private surface. Uploading your own image still works, and an issue asking Hermes for a
public image-generation call is written up in `docs/HERMES-ISSUES.md`.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from music_studio import llm_bridge

router = APIRouter()

MAX_MESSAGES = 20
MAX_MESSAGE_CHARS = 4000
MAX_NAME_CHARS = 200
MAX_TONE_CHARS = 100
MAX_STYLE = 100
MAX_REPLY_CHARS = 20000

MAX_TITLE_CHARS = 200
MAX_CAPTION_CHARS = 512
MAX_LYRICS_CHARS = 4096
MAX_KEYSCALE_CHARS = 24
MAX_BPM = 220
MAX_DURATION_SECONDS = 180

DRAFT_FIELDS = (
    "title",
    "caption",
    "lyrics",
    "bpm",
    "keyscale",
    "duration_seconds",
    "instrumental",
)

DRAFT_SYSTEM_PROMPT = (
    "You are a music brainstorming assistant. Return ONLY a single JSON object "
    "(no prose, no markdown fences, no commentary) with any of these optional "
    "fields: title, caption, lyrics, bpm, keyscale, duration_seconds, "
    "instrumental. Use strings for title (at most 200 characters), caption "
    "(at most 512), lyrics (at most 4096) and keyscale (at most 24); an "
    "integer bpm from 40 to 220; integer duration_seconds from 10 to 180; "
    "and a boolean for instrumental. Omit unknown fields. "
    "The user's tone and style preferences influence ONLY "
    "the text draft (title, caption, lyrics); they do not change bpm, keyscale, "
    "duration_seconds or instrumental."
)

CHAT_SYSTEM_PROMPT = (
    "You are a music brainstorming assistant. Suggest concise, practical music "
    "ideas. The user's tone and style preferences influence ONLY the text of "
    "the ideas, not any tempo, key, duration or instrumental choices you "
    "mention."
)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class IdeaRequest(BaseModel):
    # Optional since review item 1: the host's `ctx.llm` chooses the provider and model, and an override
    # is only honoured when the owner has trusted this plugin with one. A picker that is not trusted
    # therefore degrades to the default instead of refusing to draft.
    provider: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    model: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    action: Literal["chat", "draft"]
    tone: str = Field(default="", max_length=MAX_TONE_CHARS)
    style: int = Field(default=50, ge=0, le=MAX_STYLE)


def _bounded_text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if len(value) > limit:
        raise ValueError(f"{name} must be at most {limit} characters")
    return value


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    return stripped


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = _strip_code_fence(text)
    try:
        parsed = json.loads(cleaned)
    except (TypeError, json.JSONDecodeError):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("model output did not contain a JSON object")
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            raise ValueError("model output did not contain valid JSON")
    if not isinstance(parsed, dict):
        raise ValueError("model output must be a JSON object")
    return parsed


def parse_draft(text: str) -> dict[str, Any]:
    """Parse and validate a draft JSON object from model output.

    Unknown keys are dropped.  Malformed values raise ``ValueError`` so callers
    can leave an existing draft untouched.
    """
    obj = _extract_json_object(text)
    draft: dict[str, Any] = {}

    for key in DRAFT_FIELDS:
        if key not in obj:
            continue
        value = obj[key]
        if value is None:
            continue
        if key in ("title", "caption", "lyrics"):
            limits = {
                "title": MAX_TITLE_CHARS,
                "caption": MAX_CAPTION_CHARS,
                "lyrics": MAX_LYRICS_CHARS,
            }
            draft[key] = _bounded_text(value, key, limits[key])
        elif key == "bpm":
            draft[key] = _bounded_int(value, key, 40, MAX_BPM)
        elif key == "keyscale":
            draft[key] = _bounded_text(value, key, MAX_KEYSCALE_CHARS)
        elif key == "duration_seconds":
            number = _bounded_int(value, key, 10, MAX_DURATION_SECONDS)
            draft[key] = int(number) if isinstance(value, int) else number
        elif key == "instrumental":
            if not isinstance(value, bool):
                raise ValueError("instrumental must be a boolean")
            draft[key] = value

    return draft


def build_request(
    provider: str | None,
    model: str | None,
    messages: list[dict[str, Any]],
    action: str,
    tone: str,
    style: int,
) -> dict[str, Any]:
    """Build a bounded chat payload for the host's LLM facade.

    ``provider`` and ``model`` are *requests*, not instructions: `ctx.llm` honours them only when the
    owner has trusted this plugin with an override, and uses the host's own choice otherwise. Empty (or
    missing) means "you decide", which is the default and the only behaviour without that trust.
    """
    provider = _bounded_text(provider or "", "provider", MAX_NAME_CHARS)
    model = _bounded_text(model or "", "model", MAX_NAME_CHARS)

    if action not in ("chat", "draft"):
        raise ValueError("action must be 'chat' or 'draft'")

    tone = _bounded_text(tone if tone is not None else "", "tone", MAX_TONE_CHARS)
    style = _bounded_int(style, "style", 0, MAX_STYLE)

    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list")
    if len(messages) > MAX_MESSAGES:
        raise ValueError(f"messages must contain at most {MAX_MESSAGES} items")

    normalized: list[dict[str, str]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError(f"message {index} must be an object")
        role = message.get("role")
        if role not in ("system", "user", "assistant"):
            raise ValueError(f"message {index} has an invalid role")
        content = _bounded_text(message.get("content"), f"message {index} content", MAX_MESSAGE_CHARS)
        if not content.strip():
            raise ValueError(f"message {index} content must not be empty")
        normalized.append({"role": role, "content": content})

    system_prompt = DRAFT_SYSTEM_PROMPT if action == "draft" else CHAT_SYSTEM_PROMPT
    preference = (f"Tone: {tone or 'unspecified'}. Style influence: {style}/100, "
                  "where 0 permits freer interpretation and 100 follows the user's stated style closely.")
    system_content = f"{system_prompt} {preference}"

    return {
        "provider": provider,
        "model": model,
        "messages": [{"role": "system", "content": system_content}, *normalized],
        "temperature": 0.7,
        "max_tokens": 1200 if action == "draft" else 800,
        "stream": False,
    }


def _complete(body: dict[str, Any]) -> str:
    """Draft through the host's LLM facade, and say what is missing when there is no host."""
    try:
        return llm_bridge.complete(
            body["messages"],
            provider=body.get("provider") or None,
            model=body.get("model") or None,
            max_tokens=int(body.get("max_tokens") or 1200),
            temperature=float(body.get("temperature") or 0.7),
        )
    except llm_bridge.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except llm_bridge.LlmTrustError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Hermes model connection failed. Check the selected provider and model.",
        ) from None


@router.get("/studio/models")
def studio_models() -> JSONResponse:
    """What the host will use. A plugin cannot enumerate the owner's providers any more, and should not:
    `ctx.llm` routes to whatever Hermes is configured with."""
    if not llm_bridge.available():
        return JSONResponse(content={"provider": None, "model": None, "providers": [],
                                     "detail": llm_bridge.MISSING})
    return JSONResponse(content={"provider": None, "model": None, "providers": [],
                                 "detail": "Hermes chooses the provider and model."})


@router.post("/studio/ideas")
def studio_ideas(payload: IdeaRequest) -> JSONResponse:
    messages = [message.model_dump() for message in payload.messages]
    try:
        body = build_request(
            payload.provider,
            payload.model,
            messages,
            payload.action,
            payload.tone,
            payload.style,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    content = _complete(body)
    if not content.strip():
        raise HTTPException(status_code=502, detail="Hermes returned no content")
    if len(content) > MAX_REPLY_CHARS:
        raise HTTPException(status_code=502, detail="Hermes returned an oversized response")

    if payload.action == "chat":
        return JSONResponse(
            content={
                "success": True,
                "action": "chat",
                "provider": payload.provider,
                "model": payload.model,
                "tone_style_scope": "text_only",
                "content": content,
            }
        )

    try:
        draft = parse_draft(content)
    except ValueError:
        raise HTTPException(status_code=502, detail="Model returned an invalid draft")
    return JSONResponse(
        content={
            "success": True,
            "action": "draft",
            "provider": payload.provider,
            "model": payload.model,
            "tone_style_scope": "text_only",
            "draft": draft,
        }
    )
