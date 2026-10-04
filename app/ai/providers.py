"""Vision LLM providers. Each turns (photos, prompts, JSON schema) into a parsed dict."""
import base64
import json
import logging
import re
import time

import requests

log = logging.getLogger(__name__)


class AIError(RuntimeError):
    """User-presentable provider failure."""


class AIBadOutput(AIError):
    """The model answered, but not with parseable JSON (worth one retry)."""


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    raise AIBadOutput("The AI returned something that isn't valid JSON. Try again.")


def _b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")


class Provider:
    name = ""

    def __init__(self, conf: dict):
        self.conf = conf
        self.model = conf.get("model") or ""
        self.timeout = int(conf.get("timeout") or 170)
        self.max_tokens = int(conf.get("max_tokens") or 16000)
        self.temperature = float(conf.get("temperature") if conf.get("temperature") is not None else 0.2)
        self.usage = {"input": 0, "output": 0}

    def add_usage(self, inp, out):
        self.usage["input"] += int(inp or 0)
        self.usage["output"] += int(out or 0)

    def take_usage(self) -> dict:
        u, self.usage = self.usage, {"input": 0, "output": 0}
        return u

    def generate(self, images: list[bytes], system: str, prompt: str, schema: dict) -> dict:
        raise NotImplementedError

    def list_models(self) -> list[str]:
        return []

    def test(self) -> str:
        """Cheap round trip; returns a short human message or raises AIError."""
        models = self.list_models()
        if self.model and models and self.model not in models:
            return f"Connected, but model '{self.model}' was not in the provider's list ({len(models)} models available)."
        return f"Connected. {len(models)} models available." if models else "Connected."


# ---------------------------------------------------------------- Anthropic

class AnthropicProvider(Provider):
    name = "anthropic"

    def _client(self):
        import anthropic
        if not self.conf.get("api_key"):
            raise AIError("No Anthropic API key configured")
        return anthropic.Anthropic(api_key=self.conf["api_key"], timeout=self.timeout,
                                   base_url=self.conf.get("base_url") or None)

    def generate(self, images, system, prompt, schema):
        import anthropic
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _b64(img)}}
                   for img in images]
        content.append({"type": "text", "text": prompt})
        kwargs = dict(
            model=self.model, max_tokens=self.max_tokens, system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": schema},
                           "effort": self.conf.get("effort") or "high"},
        )
        if self.conf.get("fallback", True):
            # Server-side fallback re-runs a refused request on a suitable model.
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        msg = self.stream(**kwargs)
        if msg.stop_reason == "refusal":
            raise AIError("The model declined to process these photos.")
        if msg.stop_reason == "max_tokens":
            raise AIError("The response was cut off (max tokens). Increase max tokens or send fewer photos.")
        text = next((b.text for b in msg.content if b.type == "text"), "")
        return _parse_json(text)

    def stream(self, **kwargs):
        """Streamed request (avoids HTTP timeouts on long outputs) with friendly errors + usage."""
        import anthropic
        try:
            with self._client().beta.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise AIError("Anthropic rejected the API key") from e
        except anthropic.NotFoundError as e:
            raise AIError(f"Model '{self.model}' not found on Anthropic") from e
        except anthropic.RateLimitError as e:
            raise AIError("Anthropic rate limit reached, try again in a minute") from e
        except anthropic.BadRequestError as e:
            raise AIError(f"Anthropic rejected the request: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise AIError(f"Anthropic error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise AIError("Could not reach Anthropic (network error)") from e
        u = getattr(msg, "usage", None)
        if u is not None:
            self.add_usage((u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
                           + (getattr(u, "cache_creation_input_tokens", 0) or 0), u.output_tokens)
        return msg

    def list_models(self):
        import anthropic
        try:
            return [m.id for m in self._client().models.list()]
        except anthropic.AuthenticationError as e:
            raise AIError("Anthropic rejected the API key") from e
        except anthropic.APIError as e:
            raise AIError(f"Anthropic error: {e}") from e


# ---------------------------------------------------------------- OpenAI / compatible

class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, conf, compatible=False):
        super().__init__(conf)
        self.compatible = compatible

    def _client(self):
        from openai import OpenAI
        key = self.conf.get("api_key")
        if not key and not self.compatible:
            raise AIError("No OpenAI API key configured")
        return OpenAI(api_key=key or "not-needed", base_url=self.conf.get("base_url") or None,
                      timeout=self.timeout)

    def _reasoning_model(self) -> bool:
        return not self.compatible and bool(re.match(r"^(o\d|gpt-5)", self.model))

    def generate(self, images, system, prompt, schema):
        import openai
        content = [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_b64(img)}"}}
                   for img in images]
        content.append({"type": "text", "text": prompt})
        base = dict(model=self.model, messages=[{"role": "system", "content": system},
                                                {"role": "user", "content": content}])
        if self._reasoning_model():
            base["max_completion_tokens"] = self.max_tokens
        else:
            base["max_tokens"] = self.max_tokens
            base["temperature"] = self.temperature

        formats = [{"type": "json_schema", "json_schema": {"name": "inventory_items", "strict": True, "schema": schema}}]
        if self.compatible:
            # Not every OpenAI-compatible server supports json_schema; degrade gracefully.
            formats += [{"type": "json_object"}, None]
            base["messages"][0]["content"] = system + "\n\nRespond with JSON only, matching this schema:\n" + json.dumps(schema)
        client = self._client()
        last_err = None
        for fmt in formats:
            kwargs = dict(base)
            if fmt:
                kwargs["response_format"] = fmt
            try:
                resp = client.chat.completions.create(**kwargs)
                self._usage_from(resp)
                choice = resp.choices[0]
                if getattr(choice.message, "refusal", None):
                    raise AIError(f"The model declined: {choice.message.refusal}")
                if choice.finish_reason == "length":
                    raise AIError("The response was cut off (max tokens). Increase max tokens or send fewer photos.")
                return _parse_json(choice.message.content or "")
            except openai.BadRequestError as e:
                last_err = e
                if self.compatible:
                    continue
                raise AIError(f"Request rejected: {e.message}") from e
            except openai.AuthenticationError as e:
                raise AIError("The provider rejected the API key") from e
            except openai.NotFoundError as e:
                raise AIError(f"Model '{self.model}' not found") from e
            except openai.RateLimitError as e:
                raise AIError("Rate limit reached, try again in a minute") from e
            except openai.APIConnectionError as e:
                raise AIError("Could not reach the AI provider (network error)") from e
            except openai.APIStatusError as e:
                raise AIError(f"Provider error {e.status_code}: {e.message}") from e
        raise AIError(f"Request rejected: {getattr(last_err, 'message', last_err)}")

    def _usage_from(self, resp):
        u = getattr(resp, "usage", None)
        if u is not None:
            self.add_usage(getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0))

    def create(self, **kwargs):
        """chat.completions.create with friendly errors + usage (used by the chat assistant)."""
        import openai
        try:
            resp = self._client().chat.completions.create(**kwargs)
        except openai.AuthenticationError as e:
            raise AIError("The provider rejected the API key") from e
        except openai.NotFoundError as e:
            raise AIError(f"Model '{self.model}' not found") from e
        except openai.RateLimitError as e:
            raise AIError("Rate limit reached, try again in a minute") from e
        except openai.APIConnectionError as e:
            raise AIError("Could not reach the AI provider (network error)") from e
        except openai.APIStatusError as e:
            raise AIError(f"Provider error {e.status_code}: {e.message}") from e
        self._usage_from(resp)
        return resp

    def list_models(self):
        import openai
        try:
            return sorted(m.id for m in self._client().models.list())
        except openai.AuthenticationError as e:
            raise AIError("The provider rejected the API key") from e
        except openai.APIError as e:
            raise AIError(f"Provider error: {e}") from e


# ---------------------------------------------------------------- Google Gemini

class GoogleProvider(Provider):
    name = "google"

    def _client(self):
        # Keep one client per provider: google-genai closes its HTTP connection when the
        # Client object is garbage-collected, so a temporary client breaks the request.
        if getattr(self, "_genai", None) is None:
            from google import genai
            if not self.conf.get("api_key"):
                raise AIError("No Google API key configured")
            self._genai = genai.Client(api_key=self.conf["api_key"])
        return self._genai

    def generate(self, images, system, prompt, schema):
        from google.genai import errors, types
        parts = [types.Part.from_bytes(data=img, mime_type="image/jpeg") for img in images]
        parts.append(types.Part.from_text(text=prompt))
        config = types.GenerateContentConfig(
            system_instruction=system, temperature=self.temperature,
            max_output_tokens=self.max_tokens, response_mime_type="application/json",
            response_json_schema=schema,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        resp = self.call(contents=parts, config=config)
        if getattr(resp, "parsed", None):
            return resp.parsed
        return _parse_json(resp.text or "")

    RETRY_DELAYS = (2, 5)  # seconds; Gemini often answers 503 "high demand" / 429 briefly

    def call(self, contents, config):
        from google.genai import errors
        for attempt in range(len(self.RETRY_DELAYS) + 1):
            try:
                resp = self._client().models.generate_content(model=self.model, contents=contents, config=config)
                break
            except (errors.ClientError, errors.ServerError) as e:
                code = getattr(e, "code", None)
                transient = isinstance(e, errors.ServerError) or code == 429
                if transient and attempt < len(self.RETRY_DELAYS):
                    log.info("Gemini %s, retrying in %ss", code, self.RETRY_DELAYS[attempt])
                    time.sleep(self.RETRY_DELAYS[attempt])
                    continue
                if isinstance(e, errors.ServerError):
                    raise AIError(f"Gemini server error: {getattr(e, 'message', e)}") from e
                raise AIError(f"Gemini rejected the request: {getattr(e, 'message', e)}") from e
        um = getattr(resp, "usage_metadata", None)
        if um is not None:
            self.add_usage(um.prompt_token_count, (um.candidates_token_count or 0) + (getattr(um, "thoughts_token_count", 0) or 0))
        return resp

    def list_models(self):
        from google.genai import errors
        try:
            client = self._client()
            return sorted(m.name.removeprefix("models/") for m in client.models.list())
        except errors.APIError as e:
            raise AIError(f"Gemini error: {getattr(e, 'message', e)}") from e


# ---------------------------------------------------------------- Ollama

class OllamaProvider(Provider):
    name = "ollama"

    @property
    def base(self):
        return (self.conf.get("base_url") or "http://localhost:11434").rstrip("/")

    def generate(self, images, system, prompt, schema):
        think = bool(self.conf.get("think"))
        body = {
            "model": self.model, "stream": False, "format": schema,
            "messages": [{"role": "system", "content": system + "\n\nRespond only with JSON matching this schema:\n"
                          + json.dumps(schema)},
                         {"role": "user", "content": prompt, "images": [_b64(i) for i in images]}],
            # Reasoning models can spend thousands of tokens thinking before the JSON; give them room.
            "options": {"temperature": self.temperature,
                        "num_predict": self.max_tokens * (3 if think else 1)},
            "think": think,
        }
        data = self.chat_request(body)
        msg = data.get("message") or {}
        content = msg.get("content") or ""
        if data.get("done_reason") == "length":
            raise AIError("The response was cut off (max tokens). Increase max tokens or send fewer photos.")
        try:
            return _parse_json(content)
        except AIBadOutput:
            log.warning("Ollama returned unparseable content (done_reason=%s, eval_count=%s, thinking=%d chars): %r",
                        data.get("done_reason"), data.get("eval_count"), len(msg.get("thinking") or ""), content[:2000])
            if len(msg.get("thinking") or "") > 4000:
                raise AIBadOutput("The model spent its token budget thinking and the answer was cut off. "
                                  "Turn off thinking or raise max tokens in Settings → AI assistant.") from None
            raise

    def chat_request(self, body: dict) -> dict:
        try:
            r = requests.post(f"{self.base}/api/chat", json=body, timeout=self.timeout)
            if r.status_code == 400 and "think" in r.text.lower() and "think" in body:
                # Model without thinking support: retry without the flag
                body = {k: v for k, v in body.items() if k != "think"}
                r = requests.post(f"{self.base}/api/chat", json=body, timeout=self.timeout)
        except requests.RequestException as e:
            raise AIError(f"Could not reach Ollama at {self.base}") from e
        if r.status_code != 200:
            raise AIError(f"Ollama error {r.status_code}: {r.text[:300]}")
        data = r.json()
        self.add_usage(data.get("prompt_eval_count"), data.get("eval_count"))
        return data

    def list_models(self):
        try:
            r = requests.get(f"{self.base}/api/tags", timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            raise AIError(f"Could not reach Ollama at {self.base}") from e
        return sorted(m["name"] for m in r.json().get("models", []))


def get_provider(name: str | None = None, conf: dict | None = None) -> Provider:
    """Provider from settings (or explicit conf for 'Test connection' before saving)."""
    from ..services import settings
    ai = settings.ai_config(reveal=True)
    name = name or ai["provider"]
    conf = {**ai["providers"].get(name, {}), **(conf or {})}
    if not conf.get("model"):
        raise AIError("No model configured for this provider")
    if name == "anthropic":
        return AnthropicProvider(conf)
    if name == "openai":
        return OpenAIProvider(conf)
    if name == "openai_compatible":
        return OpenAIProvider(conf, compatible=True)
    if name == "google":
        return GoogleProvider(conf)
    if name == "ollama":
        return OllamaProvider(conf)
    raise AIError(f"Unknown AI provider: {name}")
