# CafeCode Imagegen Skill

A Codex skill for generating and reference-editing images through the CafeCode API. It uses OpenAI-compatible image endpoints, reads credentials from Codex configuration, and saves verified PNG, JPEG, or WebP files locally.

## Features

- Generate images from an exact prompt or JSON request.
- Edit or ground generation with one or more reference images.
- Decode `b64_json` responses or download HTTPS image URLs.
- Download URL responses with browser-compatible headers for CafeCode's Cloudflare-protected image CDN.
- Retry transient timeouts, rate limits, and server errors.
- Keep API keys out of prompts, scripts, and command history.
- Save atomically to `image-output/image.png` by default.

## Install

Install globally for Codex:

```powershell
npx skills add cafecodework/cafecode-imagegen `
  --skill cafecode-imagegen `
  --global `
  --agent codex `
  --yes
```

List the skill without installing it:

```powershell
npx skills add cafecodework/cafecode-imagegen --list
```

## Configure

Add your key as a top-level field in `~/.codex/config.toml`:

```toml
cafecode-imagegen-key = "your-key"
cafecode-imagegen-output-dir = "~/Pictures/CafeCode"
```

The script also supports `CAFECODE_IMAGE_API_KEY` as an environment-variable fallback. Do not commit credentials to this repository.

The output-directory setting accepts an absolute path, a `~` path, or a path relative to the current working directory. Override it for one request with `--output-dir`, or use `--out` to specify the complete file path.

## Use With Codex

Ask Codex to use the installed skill:

```text
Use $cafecode-imagegen to generate a 1024x1024 PNG of a small robot reading at a desk.
```

For a reference edit:

```text
Use $cafecode-imagegen to edit this image while preserving the character identity.
```

## Use The Script Directly

Requires Python 3.11 or later. No third-party Python packages are required.

Generate from a prompt:

```powershell
python scripts/generate_image.py `
  --prompt "A clean product photo of a graphite desk lamp" `
  --size 1024x1024
```

The default output is:

```text
image-output/image.png
```

Choose another output path:

```powershell
python scripts/generate_image.py `
  --prompt-file .\prompt.txt `
  --out .\image-output\custom-name.png
```

Override only the output directory:

```powershell
python scripts/generate_image.py `
  --prompt-file .\prompt.txt `
  --output-dir .\my-images
```

Output precedence is `--out`, then `--output-dir`, then `cafecode-imagegen-output-dir`, and finally `image-output`.

Generate with reference images:

```powershell
python scripts/generate_image.py `
  --prompt-file .\edit-prompt.txt `
  --reference .\character.png `
  --reference .\style-reference.png `
  --out .\image-output\edited.png
```

Inspect a request without calling the API:

```powershell
python scripts/generate_image.py `
  --prompt "Test prompt" `
  --dry-run
```

## Endpoints

- Generation: `https://neko.cafecode.work/v1/images/generations`
- Reference editing: `https://neko.cafecode.work/v1/images/edits`

The edit endpoint is selected automatically when `--reference` is used. Override either endpoint with `--endpoint` when needed.

When the API returns `data[].url`, the script uses a browser-compatible User-Agent for the image download. This avoids Cloudflare rejecting Python urllib's default User-Agent. The API Authorization header is intentionally not forwarded to the returned image host.

## 中文速览

安装：

```powershell
npx skills add cafecodework/cafecode-imagegen --skill cafecode-imagegen -g -a codex -y
```

在 `~/.codex/config.toml` 中配置密钥：

```toml
cafecode-imagegen-key = "你的密钥"
cafecode-imagegen-output-dir = "~/Pictures/CafeCode"
```

输出路径优先级为 `--out`、`--output-dir`、配置项 `cafecode-imagegen-output-dir`，最后回退到 `image-output/image.png`。
