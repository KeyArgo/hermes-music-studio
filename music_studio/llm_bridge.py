"""Drafting goes through Hermes' public LLM facade (`ctx.llm`), never through core internals.

Review item 1. The studio used to spawn `hermes --print-runtime-command`, then run a worker that
imported Hermes' private modules, built an `AIAgent` with provider credentials resolved by
`resolve_runtime_provider`, and reached into `tools.image_generation_tool._handle_image_generate` for
cover art. All of that is core's business: it can change without notice, and it hands a plugin the
credentials of the host.

What a plugin is actually offered is `ctx.llm.complete(...)`: the host chooses the provider and model,
owns routing, auth, timeouts and fallback, and the plugin never sees a key. So that is what this module
calls.

Two consequences worth stating plainly, because they are visible to a user:

* **Provider/model overrides are gated by the host.** Passing `provider=` or `model=` raises unless the
  owner has set `plugins.entries.<id>.llm.allow_provider_override` / `allow_model_override`. A refusal
  (`PluginLlmTrustError`, or any error that names those flags) becomes ``LlmTrustError``, which carries
  the two ``hermes config set`` commands. This module does not retry on the host's default model, and
  it does not write those settings itself.
* **Drafting needs the plugin process.** `standalone/app.py` is a separate loopback process with no
  `ctx`; a request there answers 503 with a sentence that says where to go, rather than pretending.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from typing import Any, Callable, Sequence

# Hermes' own default is 30 seconds, which is shorter than a full draft on a slow model. A timed-out
# call raises openai.APITimeoutError out of ctx.llm, and nothing downstream recognised it, so the page
# showed a bare 500. Ask for a duration a song draft can actually finish in.
TIMEOUT_SECONDS = 120

_LOG = logging.getLogger(__name__)

MISSING = (
    "Drafting needs Hermes' model. Use the Music Studio pane in Hermes Desktop, ask in a chat, "
    "or call the music_studio tool with "
    "action='draft'. A standalone browser tab has no host model to borrow."
)


class LlmUnavailable(RuntimeError):
    """No `ctx.llm` was bound — this process is not the plugin host."""


# The owner runs these. The plugin must not write `plugins.entries.*.llm.allow_*` itself.
TRUST_COMMANDS = (
    "hermes config set plugins.entries.hermes-music-studio.llm.allow_provider_override true",
    "hermes config set plugins.entries.hermes-music-studio.llm.allow_model_override true",
)


class LlmTrustError(RuntimeError):
    """The host refused a provider/model override because the plugin is not trusted with it.

    ``commands`` is the two ``hermes config set`` lines the owner copies. There is no second
    attempt on the host's default model.
    """

    def __init__(self) -> None:
        self.commands = TRUST_COMMANDS
        super().__init__(
            "Hermes refused the chosen provider or model. "
            "Run these two commands, then restart Hermes:\n" + "\n".join(self.commands)
        )

    def payload(self) -> dict[str, Any]:
        """The body the page reads: a sentence plus the two commands, as a list."""
        return {"error": str(self), "commands": list(self.commands)}


DRAFT_TASK = "music_studio_draft"

# Copyable as is: `hermes model` asks which model to use and saves it as the default.
MODEL_COMMANDS = ("hermes model",)

# Phrases that only ever describe the model itself. Ordered patterns below cover the many ways a
# provider words "model X is not something we serve", with the model named BEFORE the refusal, so a
# complaint about a parameter ("'temperature' isn't available for this model") is not read as one.
_MISSING_MODEL_MARKERS = (
    "model_not_found", "unknown model", "no such model", "invalid model", "not a valid model",
    "unsupported model",
)
_MISSING_MODEL_PATTERN = re.compile(
    r"\bmodel\b[^.;\n]{0,80}?\b(?:not found|does not exist|doesn't exist|is not included|isn't included|"
    r"not included|is not available|isn't available|not available|is not supported|isn't supported|"
    r"not supported|is not served|isn't served)")


def _is_missing_model(exc: BaseException) -> bool:
    """The provider answered, and its answer is that the model asked for is not one it serves."""
    message = str(exc).lower()
    if "model" not in message:
        return False
    if getattr(exc, "status_code", None) == 404 or type(exc).__name__ == "NotFoundError":
        return True
    return any(marker in message for marker in _MISSING_MODEL_MARKERS) or bool(
        _MISSING_MODEL_PATTERN.search(message))


class LlmModelMissing(RuntimeError):
    """The provider does not serve the model Hermes asked it for.

    This is a setting, not a passing fault, so trying again cannot fix it. A chat keeps working
    because a chat can run on a model chosen for that chat, while a plugin uses the default saved in
    Hermes' settings. The sentence says so, and ``commands`` is the line that changes the default.
    """

    def __init__(self, exc: BaseException, chosen: str | None = None) -> None:
        provider_said = " ".join(str(exc).split())[:300]
        _LOG.warning("Hermes' model is not served by its provider: %s", provider_said)
        if chosen:
            # The model picked in Music Studio is the one refused; the saved default is not involved.
            self.commands = ()
            super().__init__(
                f"The model chosen in Music Studio ({chosen}) is not available from its provider. "
                "Pick another model next to Discuss idea, or choose Your Hermes model, then try again. "
                f"The provider said: {provider_said}")
            return
        self.commands = MODEL_COMMANDS
        super().__init__(
            "Hermes' saved default model is not available from its provider, so drafting cannot "
            "use it. Chats can still work, because a chat may run on a model picked for that chat. "
            "Pick a working default with the command below, or set a model only for drafting under "
            "'Music Studio drafting' in Hermes' model settings, then try again. "
            f"The provider said: {provider_said}"
        )


class LlmFailed(RuntimeError):
    """The host's model was asked and did not deliver: a timeout, or the provider's own error.

    The sentence is written for the person who pressed the button, and the routes pass it straight
    through as the error detail. The original exception is kept as ``__cause__`` and its traceback is
    logged, so the next failure is still diagnosable.
    """


def _is_timeout(exc: BaseException) -> bool:
    """A timeout, however the provider spells it.

    openai raises ``APITimeoutError`` (a subclass of ``APIConnectionError``, not of ``TimeoutError``),
    so the class name is checked as well as the builtin type.
    """
    return isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__


def _failure(exc: BaseException) -> LlmFailed:
    """The plain sentence a person reads in the page, with the traceback logged once."""
    _LOG.warning("Hermes model call failed: %s", type(exc).__name__, exc_info=True)
    if _is_timeout(exc):
        return LlmFailed(
            f"Hermes' model did not answer within {TIMEOUT_SECONDS} seconds. "
            "Try again, or pick a faster model."
        )
    return LlmFailed(f"Hermes' model call failed: {type(exc).__name__}: {str(exc)[:300]}")


def _accepts_no_timeout(exc: BaseException) -> bool:
    """True when the TypeError is ``complete()`` rejecting the ``timeout`` keyword itself.

    An older Hermes, or a test fake with a narrow signature, says exactly that. Any other TypeError is
    a real failure and stays one instead of being retried blindly.
    """
    return isinstance(exc, TypeError) and "timeout" in str(exc)


def _override(value: str | None) -> str | None:
    """A provider or model id worth sending, or None when the owner left it to Hermes."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _is_trust_refusal(exc: BaseException) -> bool:
    """Hermes' trust gate, without importing the private ``agent.plugin_llm`` module.

    The host raises ``PluginLlmTrustError``. A message that names ``allow_provider_override`` or
    ``allow_model_override`` is the same refusal when the class itself is not what was thrown.
    """
    if type(exc).__name__ == "PluginLlmTrustError":
        return True
    message = str(exc)
    return "allow_provider_override" in message or "allow_model_override" in message


_llm: Any = None
_lock = threading.Lock()


def bind(llm: Any) -> None:
    """Called from `register(ctx)` with `ctx.llm`. Later bindings win (a reload is normal)."""
    global _llm
    with _lock:
        _llm = llm


def unbind() -> None:
    global _llm
    with _lock:
        _llm = None


def available() -> bool:
    with _lock:
        return _llm is not None


def _unsupported_keyword(exc: BaseException) -> str | None:
    """The optional keyword an older ``complete()`` does not take (``task`` or ``timeout``), else None.

    Only Python's own "unexpected keyword argument" wording counts, so a TypeError raised inside a
    newer Hermes about the value of ``task`` is not mistaken for a missing parameter.
    """
    if not isinstance(exc, TypeError):
        return None
    message = str(exc)
    for keyword in ("task", "timeout"):
        if f"unexpected keyword argument '{keyword}'" in message:
            return keyword
    if _accepts_no_timeout(exc) and "unexpected keyword" in message:
        return "timeout"
    return None


def _is_task_refusal(exc: BaseException) -> bool:
    """Hermes refused the drafting task key itself, not a provider or model.

    The task is registered in the process that ran ``register(ctx)``. A dashboard route served by
    another process does not know it and refuses it; that call then goes to the default model, which
    is what drafting did before the task existed.
    """
    return _is_trust_refusal(exc) and "auxiliary task" in str(exc)


def _ask(llm: Any, kwargs: dict[str, Any], overrides: dict[str, Any]) -> Any:
    """One call to the host's facade, dropping an optional keyword an older Hermes does not take."""
    sent = dict(kwargs)
    while True:
        try:
            return llm.complete(**sent, **overrides)
        except TypeError as exc:
            keyword = _unsupported_keyword(exc)
            if keyword is None or keyword not in sent:
                raise
            # An older Hermes takes neither `timeout` nor `task`. Drafting still works on the host's
            # own deadline and default model rather than failing the request over a keyword.
            sent.pop(keyword)
        except Exception as exc:  # noqa: BLE001 - the host's trust error is not importable here
            if "task" not in sent or not _is_task_refusal(exc):
                raise
            _LOG.warning("Hermes did not accept the drafting task here; using the default model")
            sent.pop("task")


def _call(messages: Sequence[dict[str, str]], *, provider: str | None, model: str | None,
          max_tokens: int, temperature: float, llm: Any = None) -> str:
    if llm is None:
        with _lock:
            llm = _llm
    if llm is None:
        raise LlmUnavailable(MISSING)

    kwargs: dict[str, Any] = {
        "messages": [dict(message) for message in messages],
        "max_tokens": max_tokens,
        "temperature": temperature,
        # Without this the host applies its 30 second default and a long draft dies as a bare 500.
        "timeout": TIMEOUT_SECONDS,
    }
    overrides = {key: text for key, value in (("provider", provider), ("model", model))
                 if (text := _override(value))}
    if not overrides:
        # Its own model slot in Hermes' settings, left empty = the default model, as before. Only when
        # nothing was chosen in the page: Hermes would otherwise mix a chosen provider with the slot's
        # model, and a model from one provider sent to another fails.
        kwargs["task"] = DRAFT_TASK
    try:
        result = _ask(llm, kwargs, overrides)
    except Exception as exc:  # noqa: BLE001 - the host's trust error is not importable here
        if _is_trust_refusal(exc):
            # One call. Retrying without the override would silently draft on a different model.
            raise LlmTrustError() from exc
        # Everything else — a timeout, a provider error, a refused connection — becomes one plain
        # sentence the page can show, instead of an exception class the route never catches.
        if _is_missing_model(exc):
            raise LlmModelMissing(exc, chosen=overrides.get("model") or overrides.get("provider")) from exc
        raise _failure(exc) from exc
    text = getattr(result, "text", None)
    if text is None and isinstance(result, dict):
        text = result.get("text")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Hermes returned an empty response")
    return text


def complete(messages: Sequence[dict[str, str]], *, provider: str | None = None,
             model: str | None = None, max_tokens: int = 1200, temperature: float = 0.7,
             llm: Any = None) -> str:
    """Synchronous completion — the shape the FastAPI routes and the tool handler both use."""
    return _call(messages, provider=provider, model=model, max_tokens=max_tokens,
                 temperature=temperature, llm=llm)


async def acomplete(messages: Sequence[dict[str, str]], *, provider: str | None = None,
                    model: str | None = None, max_tokens: int = 1200,
                    temperature: float = 0.7) -> str:
    """Async wrapper: `ctx.llm` is synchronous, the routes are not."""
    return await asyncio.to_thread(
        _call, messages, provider=provider, model=model, max_tokens=max_tokens,
        temperature=temperature)
