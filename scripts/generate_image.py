#!/usr/bin/env python3
"""Generate an image through an OpenAI-compatible CafeCode endpoint."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import os
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_ENDPOINT = "https://neko.cafecode.work/v1/images/generations"
DEFAULT_EDIT_ENDPOINT = "https://neko.cafecode.work/v1/images/edits"
RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--prompt", help="Exact prompt text to send")
    source.add_argument("--prompt-file", type=Path, help="UTF-8 file containing the exact prompt")
    source.add_argument("--request-file", type=Path, help="UTF-8 JSON request body")
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--out",
        type=Path,
        help="Complete output image path; overrides the configured output directory",
    )
    output.add_argument("--output-dir", type=Path, help="Output directory; the file name defaults to image.<format>")
    parser.add_argument("--endpoint", help="Override the endpoint; references use /v1/images/edits by default")
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--output-format", default="png", choices=("png", "jpeg", "webp"))
    parser.add_argument("--quality", choices=("low", "medium", "high", "auto"))
    parser.add_argument("--moderation", choices=("auto", "low"))
    parser.add_argument("--reference", action="append", default=[], type=Path, help="Reference image path; repeatable")
    parser.add_argument("--config-file", type=Path, help="TOML config path; defaults to ~/.codex/config.toml")
    parser.add_argument("--config-key", default="cafecode-imagegen-key", help="Top-level TOML field containing the API key")
    parser.add_argument(
        "--output-dir-config-key",
        default="cafecode-imagegen-output-dir",
        help="Top-level TOML field containing the default output directory",
    )
    parser.add_argument("--api-key-env", default="CAFECODE_IMAGE_API_KEY")
    parser.add_argument("--header", action="append", default=[], metavar="NAME=VALUE", help="Additional request header; repeatable")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    parser.add_argument("--allow-http", action="store_true", help="Allow http:// endpoints, intended for local tests")
    parser.add_argument("--all", action="store_true", dest="save_all", help="Save every image returned in data[]")
    parser.add_argument("--dry-run", action="store_true", help="Print the request and do not call the endpoint")
    return parser.parse_args()


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        return args.prompt
    assert args.prompt_file is not None
    return args.prompt_file.read_text(encoding="utf-8")


def load_request(args: argparse.Namespace) -> dict[str, Any]:
    if args.request_file is not None:
        value = json.loads(args.request_file.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request JSON must be an object")
        body = dict(value)
        if "prompt" not in body:
            raise ValueError("request JSON must contain prompt")
    else:
        body = {
            "model": args.model,
            "prompt": read_prompt(args),
            "size": args.size,
            "output_format": args.output_format,
        }
        if args.quality is not None:
            body["quality"] = args.quality
        if args.moderation is not None:
            body["moderation"] = args.moderation
    if args.reference:
        existing = body.get("images", [])
        if existing and not isinstance(existing, list):
            raise ValueError("request JSON images must be an array")
        body["images"] = list(existing) + [encode_reference(path) for path in args.reference]
    return body


def encode_reference(path: Path) -> dict[str, str]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"reference image does not exist: {resolved}")
    mime, _ = mimetypes.guess_type(resolved.name)
    if mime not in {"image/png", "image/jpeg", "image/webp"}:
        raise ValueError(f"unsupported reference image type: {resolved}")
    encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
    return {"image_url": f"data:{mime};base64,{encoded}"}


def request_for_display(body: dict[str, Any]) -> dict[str, Any]:
    display = dict(body)
    images = display.get("images")
    if isinstance(images, list):
        display["images"] = [
            {"image_url": f"<data URL, {len(item.get('image_url', ''))} chars>"}
            if isinstance(item, dict) and str(item.get("image_url", "")).startswith("data:")
            else item
            for item in images
        ]
    return display


def apply_overrides(body: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if args.request_file is None:
        return body
    result = dict(body)
    if args.model != "gpt-image-2":
        result["model"] = args.model
    if args.size != "1024x1024":
        result["size"] = args.size
    if args.output_format != "png":
        result["output_format"] = args.output_format
    if args.quality is not None:
        result["quality"] = args.quality
    if args.moderation is not None:
        result["moderation"] = args.moderation
    return result


def validate_endpoint(endpoint: str, allow_http: bool) -> None:
    parsed = urlparse(endpoint)
    allowed = {"https"} | ({"http"} if allow_http else set())
    if parsed.scheme not in allowed or not parsed.netloc:
        expected = "https://" if not allow_http else "https:// or http://"
        raise ValueError(f"endpoint must use {expected} and include a host")


def parse_headers(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    for value in values:
        if "=" not in value:
            raise ValueError(f"invalid --header {value!r}; expected NAME=VALUE")
        name, header_value = value.split("=", 1)
        name = name.strip()
        if not name or not header_value:
            raise ValueError(f"invalid --header {value!r}")
        result[name] = header_value
    return result


def config_path_for(args: argparse.Namespace) -> Path:
    return args.config_file or (Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml")


def load_config(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    config_path = config_path_for(args).expanduser()
    if config_path.exists():
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
        if not isinstance(config, dict):
            raise ValueError(f"config file must contain a TOML table: {config_path}")
        return config_path, config
    return config_path, {}


def load_api_key(args: argparse.Namespace, config: dict[str, Any], config_path: Path) -> str | None:
    value = config.get(args.config_key)
    if value is not None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{args.config_key} in {config_path} must be a non-empty string")
        return value.strip()
    if args.api_key_env:
        value = os.environ.get(args.api_key_env)
        if value:
            return value
    return None


def resolve_output_path(args: argparse.Namespace, body: dict[str, Any], config: dict[str, Any], config_path: Path) -> Path:
    if args.out is not None:
        return args.out.expanduser()

    if args.output_dir is not None:
        output_dir = args.output_dir.expanduser()
    else:
        configured = config.get(args.output_dir_config_key)
        if configured is None:
            output_dir = Path("image-output")
        elif not isinstance(configured, str) or not configured.strip():
            raise ValueError(f"{args.output_dir_config_key} in {config_path} must be a non-empty string")
        else:
            output_dir = Path(configured.strip()).expanduser()

    output_format = str(body.get("output_format", args.output_format)).lower()
    extension = output_format if output_format in {"png", "jpeg", "webp"} else args.output_format
    return output_dir / f"image.{extension}"


def response_error(exc: Exception) -> tuple[bool, str]:
    if isinstance(exc, HTTPError):
        body = exc.read().decode("utf-8", errors="replace")[:1000]
        return exc.code in RETRY_STATUSES, f"HTTP {exc.code}: {body}"
    if isinstance(exc, (URLError, TimeoutError)):
        return True, str(exc)
    return False, str(exc)


def post_json(endpoint: str, body: dict[str, Any], headers: dict[str, str], timeout: float, retries: int, delay: float) -> dict[str, Any]:
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last_error = "request failed"
    for attempt in range(retries + 1):
        request = Request(endpoint, data=encoded, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("response JSON must be an object")
            return parsed
        except Exception as exc:  # noqa: BLE001 - normalize transport errors for CLI users
            retryable, last_error = response_error(exc)
            if not retryable or attempt >= retries:
                raise RuntimeError(last_error) from exc
            time.sleep(delay * (2**attempt))
    raise RuntimeError(last_error)


def extract_images(response: dict[str, Any]) -> list[tuple[str, bytes | str]]:
    raw_items = response.get("data", response)
    items = raw_items if isinstance(raw_items, list) else [raw_items]
    images: list[tuple[str, bytes | str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("b64_json"), str):
            encoded = "".join(item["b64_json"].split())
            try:
                images.append(("b64_json", base64.b64decode(encoded, validate=True)))
            except (ValueError, binascii.Error) as exc:
                raise RuntimeError("response contains invalid b64_json") from exc
        elif isinstance(item.get("url"), str):
            images.append(("url", item["url"]))
    if not images:
        raise RuntimeError("response contains no data[].b64_json or data[].url image")
    return images


def fetch_url(url: str, timeout: float) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError("image URLs in API responses must use https://")
    request = Request(url, headers={"Accept": "image/*"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def suffix_path(path: Path, index: int) -> Path:
    return path.with_name(f"{path.stem}-{index}{path.suffix}")


def main() -> int:
    args = parse_args()
    try:
        if args.retries < 0 or args.timeout <= 0 or args.retry_delay < 0:
            raise ValueError("timeout must be positive; retries and retry-delay must be non-negative")
        body = apply_overrides(load_request(args), args)
        endpoint = args.endpoint or (DEFAULT_EDIT_ENDPOINT if body.get("images") else DEFAULT_ENDPOINT)
        validate_endpoint(endpoint, args.allow_http)
        headers = parse_headers(args.header)
        config_path, config = load_config(args)
        output_path = resolve_output_path(args, body, config, config_path)

        if args.dry_run:
            print(
                json.dumps(
                    {
                        "endpoint": endpoint,
                        "output": str(output_path.resolve()),
                        "request": request_for_display(body),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        api_key = load_api_key(args, config, config_path)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        elif endpoint in {DEFAULT_ENDPOINT, DEFAULT_EDIT_ENDPOINT}:
            raise RuntimeError(f"missing API key: add {args.config_key} to {config_path}")

        response = post_json(endpoint, body, headers, args.timeout, args.retries, args.retry_delay)
        images = extract_images(response)
        selected = images if args.save_all else images[:1]
        outputs: list[str] = []
        for index, (kind, value) in enumerate(selected):
            content = fetch_url(value, args.timeout) if kind == "url" else value
            if not content:
                raise RuntimeError("response image is empty")
            target = output_path if not args.save_all else suffix_path(output_path, index + 1)
            atomic_write(target, content)
            outputs.append(str(target.resolve()))
        print(json.dumps({"ok": True, "outputs": outputs, "count": len(outputs)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"cafecode-imagegen: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
