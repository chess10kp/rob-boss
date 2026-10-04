# Remote image-processing API

The local agent exposes the GIMP MCP decomposition workflow through an authenticated HTTP API.

## Current endpoint

```text
https://blackjack-prime-maryland-different.trycloudflare.com
```

This is a Cloudflare Quick Tunnel URL. It is temporary and changes when the tunnel restarts. A permanent hostname requires a named Cloudflare Tunnel.

## Process an image

`POST /process` accepts the raw image bytes. Supported media types:

- `image/jpeg`
- `image/png`
- `image/webp`
- `image/tiff`
- `image/bmp`

Authentication uses the bearer token stored locally at `~/.cache/rob-boss/agent-api-token`.

```bash
BASE_URL="https://blackjack-prime-maryland-different.trycloudflare.com"
TOKEN="$(cat ~/.cache/rob-boss/agent-api-token)"

curl --fail-with-body "$BASE_URL/process" \
  -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: image/jpeg" \
  -H "X-Filename: reference.jpg" \
  --data-binary @reference.jpg
```

The response contains the ordered `steps` returned by the GIMP MCP workflow. Each step includes its target RGB value, coverage, and a relative artifact path. The response also includes links to the full manifest, posterized image, and contact sheet.

Artifact requests require the same bearer token:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/jobs/<job_id>/scene/layers/01_darkest.png" \
  -o 01_darkest.png
```

## Health check

`GET /healthz` is public and returns the API status:

```bash
curl "$BASE_URL/healthz"
```

The server binds locally on `127.0.0.1:8080`; `cloudflared` provides the public ingress. The implementation is in `server.py`.
