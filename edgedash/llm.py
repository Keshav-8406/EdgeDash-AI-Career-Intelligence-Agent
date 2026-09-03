"""
EdgeDash LLM abstraction layer.

All LLM calls go through complete_json().
Providers are selected through config.yaml.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from typing import Any, Callable

import requests
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Project configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class LLMError(RuntimeError):
    """Raised when an LLM call or response validation fails."""


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

_LAST_REQUEST_TIME = 0.0


def _rate_limit() -> None:
    """
    Enforce a minimum delay between LLM requests.

    Default policy:
    - At least 1 second between requests.
    """
    global _LAST_REQUEST_TIME

    now = time.monotonic()
    elapsed = now - _LAST_REQUEST_TIME

    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)

    _LAST_REQUEST_TIME = time.monotonic()


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Any:
    """
    Extract JSON from a model response.

    Supports:
    - Plain JSON
    - Markdown fenced JSON
    - JSON object embedded in surrounding text
    - JSON array embedded in surrounding text
    """

    text = text.strip()

    if not text:
        raise ValueError("Empty LLM response")

    # Remove markdown code fences.
    fenced = re.search(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if fenced:
        text = fenced.group(1).strip()

    # First try the complete response.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find an embedded JSON object.
    object_match = re.search(
        r"\{.*\}",
        text,
        flags=re.DOTALL,
    )

    if object_match:
        try:
            return json.loads(object_match.group(0))
        except json.JSONDecodeError:
            pass

    # Try to find an embedded JSON array.
    array_match = re.search(
        r"\[.*\]",
        text,
        flags=re.DOTALL,
    )

    if array_match:
        try:
            return json.loads(array_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(
        "Could not extract valid JSON from LLM response"
    )


# ---------------------------------------------------------------------------
# Basic JSON schema validation
# ---------------------------------------------------------------------------

def _validate(data: Any, schema: dict[str, Any]) -> None:
    """
    Validate basic JSON schema requirements.

    Supported:
    - type
    - required
    - properties
    - items
    - enum
    - null
    """

    if not isinstance(schema, dict):
        return

    def check_type(
        value: Any,
        expected_type: Any,
        location: str,
        property_schema: dict[str, Any] | None = None,
    ) -> None:

        # Support union types such as ["integer", "null"].
        if isinstance(expected_type, list):
            for allowed_type in expected_type:
                try:
                    check_type(
                        value,
                        allowed_type,
                        location,
                        property_schema,
                    )
                    return
                except ValueError:
                    continue

            raise ValueError(
                f"{location} has invalid type; "
                f"expected one of {expected_type}"
            )

        if expected_type is None:
            return

        if expected_type == "null":
            if value is not None:
                raise ValueError(
                    f"{location} must be null"
                )

        elif expected_type == "object":
            if not isinstance(value, dict):
                raise ValueError(
                    f"{location} must be an object"
                )

        elif expected_type == "array":
            if not isinstance(value, list):
                raise ValueError(
                    f"{location} must be an array"
                )

            if property_schema:
                items_schema = property_schema.get("items")

                if isinstance(items_schema, dict):
                    for index, item in enumerate(value):

                        check_type(
                            item,
                            items_schema.get("type"),
                            f"{location}[{index}]",
                            items_schema,
                        )

                        item_enum = items_schema.get("enum")

                        if (
                            item_enum is not None
                            and item not in item_enum
                        ):
                            raise ValueError(
                                f"{location}[{index}] must be one of: "
                                f"{', '.join(map(str, item_enum))}"
                            )

        elif expected_type == "string":
            if not isinstance(value, str):
                raise ValueError(
                    f"{location} must be a string"
                )

        elif expected_type == "number":
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
            ):
                raise ValueError(
                    f"{location} must be a number"
                )

        elif expected_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(
                    f"{location} must be an integer"
                )

        elif expected_type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(
                    f"{location} must be a boolean"
                )

    expected_type = schema.get("type")

    if expected_type is not None:
        check_type(
            data,
            expected_type,
            "Response",
            schema,
        )

    enum_values = schema.get("enum")

    if enum_values is not None and data not in enum_values:
        raise ValueError(
            "Response must be one of: "
            + ", ".join(map(str, enum_values))
        )

    if isinstance(data, dict):

        required = schema.get("required", [])

        for key in required:
            if key not in data:
                raise ValueError(
                    f"Missing required key: {key}"
                )

        properties = schema.get("properties", {})

        if isinstance(properties, dict):

            for key, property_schema in properties.items():

                if key not in data:
                    continue

                if not isinstance(property_schema, dict):
                    continue

                value = data[key]

                check_type(
                    value,
                    property_schema.get("type"),
                    f"Key '{key}'",
                    property_schema,
                )

                property_enum = property_schema.get("enum")

                if (
                    property_enum is not None
                    and value not in property_enum
                ):
                    raise ValueError(
                        f"Key '{key}' must be one of: "
                        f"{', '.join(map(str, property_enum))}"
                    )


# ---------------------------------------------------------------------------
# Gemini provider
# ---------------------------------------------------------------------------

def _call_gemini(
    prompt: str,
    config: Any,
    schema: dict[str, Any] | None = None,
) -> str:
    """Call Gemini through the official google-genai SDK."""

    try:
        from google import genai
    except ImportError as exc:
        raise LLMError(
            "google-genai is not installed. "
            "Install it with: pip install google-genai"
        ) from exc

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise LLMError(
            "GEMINI_API_KEY is missing from the environment."
        )

    model_name = (
        getattr(config, "llm_model", None)
        or os.getenv("GEMINI_MODEL")
        or "gemini-2.0-flash"
    )

    try:
        client = genai.Client(api_key=api_key)

        _rate_limit()

        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
        )

        text = getattr(response, "text", None)

        if not text:
            raise LLMError(
                "Gemini returned an empty response"
            )

        return text

    except LLMError:
        raise

    except Exception as exc:
        raise LLMError(
            f"Gemini request failed: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Ollama provider
# ---------------------------------------------------------------------------

def _call_ollama(
    prompt: str,
    config: Any,
    schema: dict[str, Any] | None = None,
) -> str:
    """
    Call a local Ollama model.

    When a JSON schema is provided, send the schema directly to
    Ollama's structured-output format parameter.
    """

    model_name = (
        getattr(config, "llm_model", None)
        or os.getenv("OLLAMA_MODEL")
        or "llama3.2"
    )

    url = os.getenv(
        "OLLAMA_URL",
        "http://localhost:11434/api/generate",
    )

    payload: dict[str, Any] = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
    }

    # IMPORTANT:
    # Ollama supports either "json" or a JSON schema in "format".
    # Sending the actual schema makes required fields much more reliable.
    if schema is not None:
        payload["format"] = schema
    else:
        payload["format"] = "json"

    try:
        _rate_limit()

        response = requests.post(
            url,
            json=payload,
            timeout=180,
        )

        response.raise_for_status()

        data = response.json()

        text = data.get("response")

        if not text:
            raise LLMError(
                "Ollama returned an empty response"
            )

        return text

    except LLMError:
        raise

    except requests.exceptions.Timeout as exc:
        raise LLMError(
            "Ollama request timed out after 180 seconds."
        ) from exc

    except requests.exceptions.ConnectionError as exc:
        raise LLMError(
            "Could not connect to Ollama. "
            "Make sure the Ollama service is running."
        ) from exc

    except Exception as exc:
        raise LLMError(
            f"Ollama request failed: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

ProviderFunction = Callable[
    [str, Any, dict[str, Any] | None],
    str,
]


_PROVIDERS: dict[str, ProviderFunction] = {
    "gemini": _call_gemini,
    "ollama": _call_ollama,
}


# ---------------------------------------------------------------------------
# Public LLM function
# ---------------------------------------------------------------------------

def complete_json(
    prompt: str,
    schema: dict[str, Any],
    *,
    config: Any,
    max_retries: int = 2,
) -> dict[str, Any]:
    """
    Complete a prompt and return validated JSON.

    All EdgeDash LLM calls should go through this function.

    Behaviour:
    - Provider comes from config.
    - Model comes from config.
    - Ollama receives the JSON schema directly.
    - Response must contain valid JSON.
    - Response must pass schema validation.
    - Invalid responses are retried.
    - Raises LLMError after retries are exhausted.
    """

    provider_name = (
        getattr(config, "llm_provider", None)
        or os.getenv("LLM_PROVIDER")
        or "gemini"
    ).lower()

    provider = _PROVIDERS.get(provider_name)

    if provider is None:
        raise LLMError(
            f"Unsupported LLM provider: {provider_name}. "
            f"Supported providers: {', '.join(_PROVIDERS)}"
        )

    attempts = max(
        1,
        max_retries + 1,
    )

    last_error: Exception | None = None

    for attempt in range(attempts):

        try:
            # IMPORTANT:
            # Pass the schema to the provider so Ollama can enforce it.
            response_text = provider(
                prompt,
                config,
                schema,
            )

            data = _extract_json(response_text)

            _validate(
                data,
                schema,
            )

            if not isinstance(data, dict):
                raise ValueError(
                    "Validated JSON response must be an object"
                )

            return data

        except Exception as exc:

            last_error = exc

            if attempt >= attempts - 1:
                break

            error_text = str(exc).lower()

            if (
                "429" in error_text
                or "quota" in error_text
                or "rate limit" in error_text
                or "resource exhausted" in error_text
            ):
                delay = min(
                    8.0,
                    2.0 ** attempt,
                )
            else:
                delay = 1.0

            time.sleep(delay)

    raise LLMError(
        f"LLM JSON completion failed after "
        f"{attempts} attempts: {last_error}"
    ) from last_error


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_config() -> Any:
    """Load EdgeDash configuration from config.yaml."""

    from .config import load_config

    config_path = os.path.join(
        PROJECT_ROOT,
        "config.yaml",
    )

    return load_config(config_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI configuration check."""

    parser = argparse.ArgumentParser(
        description="EdgeDash LLM configuration check"
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Check LLM configuration without making an API call",
    )

    args = parser.parse_args()

    config = _load_config()

    provider = (
        getattr(config, "llm_provider", None)
        or os.getenv("LLM_PROVIDER")
        or "gemini"
    )

    model = (
        getattr(config, "llm_model", None)
        or os.getenv("OLLAMA_MODEL")
        or os.getenv("GEMINI_MODEL")
        or "gemini-2.0-flash"
    )

    print(f"LLM provider: {provider}")
    print(f"LLM model: {model}")

    if str(provider).lower() == "gemini":

        api_key = os.getenv("GEMINI_API_KEY")

        if api_key:
            print("Gemini API key: AVAILABLE")
            print("API key value is not displayed.")
        else:
            print("Gemini API key: MISSING")
            raise SystemExit(1)

    elif str(provider).lower() == "ollama":

        print("Ollama configuration: SELECTED")

    else:

        print(
            f"Unsupported LLM provider: {provider}"
        )

        raise SystemExit(1)

    print("LLM configuration check: PASSED")


if __name__ == "__main__":
    main()