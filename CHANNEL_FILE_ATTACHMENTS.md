# Channel File & Image Attachments

Every built-in channel in `flocks` (weixin, feishu, wecom, dingtalk, telegram)
exposes the same contract for file/image attachments:

- **Inbound** — remote media in any channel becomes a local `FilePart` on
  the user message in the session, so downstream agents and tools see a
  uniform `file://` URI regardless of which platform the file came from.
- **Outbound** — `OutboundContext.media_url` is uploaded through the
  platform's native attachment API (not just a markdown link), and any
  companion text is sent as a follow-up message in the channel's normal
  format.

This document is the **review guide** for changes that touch the channel
media path. It tells reviewers where to look, what is in scope, and what
business logic is non-obvious.

---

## 1. Architecture at a glance

```
                ┌─────────────────────────────────────┐
                │  _DOWNLOADERS (registry in          │
                │  flocks/channel/inbound/dispatcher) │
                └─────────────────────────────────────┘
                              │ dynamic lookup
        ┌─────────┬───────────┼───────────┬─────────────┐
        ▼         ▼           ▼           ▼             ▼
   feishu.   wecom.      dingtalk.    telegram.      weixin.
 inbound_media inbound_  inbound_     inbound_       inbound_
              media      media        media          media
   (existing) (new)      (new)        (new)          (existing)

                       Outbound (send_media):
        ┌─────────┬───────────┬───────────┬─────────────┐
        ▼         ▼           ▼           ▼             ▼
   Feishu   WeCom        DingTalk     Telegram      Weixin
   Channel. Channel.    Channel.     Channel.      Channel.
   send_media send_media send_media   send_media    send_media
   (existing) (new)      (new)       (new)         (existing)
```

**The contract is symmetrical**: every channel owns a `download_inbound_media(msg, config)`
helper and a `send_media(ctx)` method. New channels only need to add
those two pieces — the dispatcher picks them up automatically.

---

## 2. Inbound flow (the file changes the user actually receives)

1. The channel's stream / webhook handler parses the platform's native
   message and calls `InboundMessage` with `media_url` set to whatever
   opaque reference the platform gives us:
   - feishu: `lark://image/<key>` / `lark://file/<key>`
   - wecom:  full `https://...` URL (the SDK already decodes the frame)
   - dingtalk: bare `download_code` (no scheme)
   - telegram: `telegram://<kind>/<file_id>` (we synthesise the URI)
   - weixin:  `file://` URI pointing at the already-downloaded file
2. The dispatcher's `_append_user_message` writes the user message and,
   if `media_url` is set, calls `_download_channel_media(msg, config)`.
3. `_download_channel_media` does a **dynamic** `__import__`-style lookup
   into the channel's `inbound_media` module and returns
   `DownloadedInboundMedia` (or `None` on failure).
4. On success the dispatcher calls `Message.store_part(session_id,
   message_id, FilePart(...))` and also rewrites the placeholder text
   (`[图片消息]` / `[文件消息: x]` / `[Image]` / `[Attachment]` / `[图片]`
   / `[文件]`) to `Attached files:\n- <path>`. The two updates are
   published as SSE `message.part.updated` events.

### Why the dynamic lookup?

The dispatcher keeps a private `_DOWNLOADERS` table for eager
registration, but the **real lookup** at request time is a dynamic
`__import__("flocks.channel.builtin.<ch>.inbound_media")` call. This
matters because:

- Test code patches `flocks.channel.builtin.<ch>.inbound_media.download_inbound_media`
  to inject a fake. If we cached the bound callable at module-import
  time the patch would not apply.
- Plugins shipped in separate wheels can register themselves at runtime
  by calling `register_inbound_media_downloader(channel_id, fn)`;
  the dynamic lookup is the fallback for the built-ins.

### Failure modes (must review)

| Failure | Behavior | Log key |
| --- | --- | --- |
| Decrypt error (wecom only) | `download_inbound_media` returns `None` | `wecom.media.decrypt_failed` |
| Body exceeds size cap | `None` returned; nothing stored | `wecom.media.file_too_large` / `dingtalk.media.file_too_large` / `telegram.media.file_too_large` |
| Network / exchange error | `None` returned; original `text` placeholder stays | `wecom.media.download_failed` / `dingtalk.media.exchange_failed` / `dingtalk.media.download_failed` / `telegram.media.download_failed` |
| SDK / dep missing | `None` returned; logged at warn | `wecom.media.sdk_not_available` |
| Missing channel config | `None` returned; logged at warn | `telegram.media.no_token` |

**Critical**: a `None` return is *not* an exception — the user message
keeps its original text, and the agent still gets the placeholder. We
deliberately do **not** raise because a single failed download must not
take down the inbound pipeline.

---

## 3. Outbound flow (sending a file to the user)

Every channel implements `send_media(ctx: OutboundContext) -> DeliveryResult`.
The contract is:

- Read the local file or fetch the remote URL into memory.
- Upload via the platform's native API; receive a server-side reference
  (`media_id` / `downloadCode` / `file_id`).
- Call the platform's send API with that reference.
- If `ctx.text` is set, send it **after** the attachment as a separate
  message (because none of the platforms' attachment APIs accept a
  caption). Wecom uses `markdown`; dingtalk uses `markdown` too;
  telegram uses the `caption` field of the chosen `send_*` endpoint.
- Return a `DeliveryResult(message_id=...)` so the gateway can record
  delivery and re-publish it on the bus.

### Channel-specific quirks (read these before changing anything)

#### wecom
- `media_type` is one of `file` / `image` / `voice` / `video`, inferred
  from the filename's MIME.
- `upload_media` requires a base64 chunked upload on the SDK side; the
  PR only adds the call site, not the transport.
- When `reply_to_id` matches a cached inbound frame, the SDK's
  `reply_media` is used instead of `send_media_message` so the message
  is threaded under the user's original message.
- The `frame_cache` is bounded at 500 entries (existing config).

#### dingtalk
- OAPI accepts `msgKey=image` only with a public `photoURL`; for local
  files (or any non-remote media) we use `msgKey=file` with the
  upload's `downloadCode` + `fileName`. The code picks the right key
  based on `(media_type == "image" and ctx.media_url starts with
  http(s))`.
- The upload is multipart, not JSON. We bypass the generic
  `api_request_for_account` for the upload and call `httpx` directly
  with the persisted `x-acs-dingtalk-access-token` from
  `get_access_token`.
- Per-account robot-code is resolved via `resolve_account_credentials`
  so multi-account configs work.

#### telegram
- Bot API has six send endpoints; we route by `PreparedTelegramMedia.kind`
  which is inferred from MIME:
  - `image/*` (non-GIF) → `sendPhoto`
  - `image/gif`        → `sendAnimation`
  - `video/*`          → `sendVideo`
  - `audio/ogg`        → `sendVoice`
  - `audio/*`          → `sendAudio`
  - else               → `sendDocument`
- Agents can override the routing with a `telegram:document:<url>`
  prefix on `media_url`. This is the documented escape hatch for
  screenshots / extreme aspect ratios that exceed Telegram's photo
  dimension limits — they get sent as a file instead.
- The 50MB size cap is enforced on read (local) and on streaming
  (remote) before the body ever reaches the Bot API.

---

## 4. Files added / changed (review checklist)

| File | Status | Review focus |
| --- | --- | --- |
| `flocks/channel/builtin/wecom/inbound_media.py` | new | AES-256-CBC decrypt, mixed-message aeskey lookup order, `Content-Disposition` filename parsing (UTF-8 + plain) |
| `flocks/channel/builtin/wecom/media.py` | new | MIME → media_type mapping, local/http size limits, sanitize filename |
| `flocks/channel/builtin/wecom/channel.py` | modified | `send_media` ordering (media then text), reply-vs-send selection via `_frame_cache` |
| `flocks/channel/builtin/dingtalk/inbound_media.py` | new | download_code vs URL detection (`_is_download_code`), OAPI exchange response shape (`downloadUrl` + `fileName`), error classification |
| `flocks/channel/builtin/dingtalk/media.py` | new | multipart upload via `httpx`, token re-use via `get_access_token`, OAPI error parsing |
| `flocks/channel/builtin/dingtalk/channel.py` | modified | `msgKey` selection, OAPI payload shape, **text-after-media** ordering (matches wecom) |
| `flocks/channel/builtin/telegram/inbound_media.py` | new | `telegram://<kind>/<file_id>` URI scheme, getFile + file download split, 20MB streaming cap, photo synthesis filename |
| `flocks/channel/builtin/telegram/media.py` | new | Kind inference table, override mechanism, 50MB cap |
| `flocks/channel/builtin/telegram/channel.py` | modified | `send_media` kind-to-endpoint routing, override prefix parsing, multipart field naming |
| `flocks/channel/builtin/telegram/inbound.py` | modified | `media_url` population with `telegram://...` URI, `_extract_primary_file_id` picks the largest photo variant |
| `flocks/channel/inbound/dispatcher.py` | modified | `_DOWNLOADERS` registry, dynamic lookup, `_is_placeholder_text` matcher, placeholder text rewrite + SSE event |
| `tests/channel/test_wecom.py` | expanded | outbound upload/reply/missing-media-id, mixed file, content-disposition, inbound decrypt/size/mixed-nested |
| `tests/channel/test_dingtalk.py` | expanded | download_code exchange, oversized, send_media, text-after-file, image URL inline |
| `tests/channel/test_telegram.py` | expanded | URI parse, getFile+download, kind inference (png/pdf/gif/ogg), endpoint routing, override prefix, error path |
| `tests/channel/test_channel.py` | expanded | per-channel downloader routing, placeholder detection, end-to-end per-channel pipeline |
| `tests/channel/test_e2e_file_roundtrip.py` | new | real PNG byte round-trips through fake servers + dispatcher pipeline |

---

## 5. What is explicitly **out of scope**

- **Multi-attachment messages**: a single inbound may carry N files but
  `InboundMessage.media_url` is single-valued. The current behavior is
  "first file wins, log the rest as dropped" (matches weixin's existing
  behavior; documented in `base.py`). A future PR can add
  `media_urls: list[str]` if needed.
- **Voice / video call attachments**: out of scope for the
  file/image round-trip. Voice notes are sent via `sendVoice` on
  telegram; Wecom's `voice` and `video` msgtypes go through the same
  `download_inbound_media` path but with a different MIME → media_type
  guess on the outbound side.
- **Cross-channel file forwarding**: there is no "re-share this file to
  another channel" affordance. Each channel's outbound path is
  independent; the agent re-encodes via `OutboundContext.media_url` on
  the new target.
- **Sticker / animated-sticker on Telegram**: skipped; only documented
  media kinds are routed.

---

## 6. How to test locally

```bash
# Channel + dispatcher tests
uv run pytest tests/channel/ -q

# End-to-end round-trip (real PNG bytes through fake servers)
uv run pytest tests/channel/test_e2e_file_roundtrip.py -v
```

There is no real-platform integration test (we have no credentials to
the actual WeCom / DingTalk / Telegram services in CI). The
`test_e2e_file_roundtrip.py` tests cover the full byte-preservation
contract using in-process fake servers, which is the strongest signal
we can produce without live credentials.

When validating against a real platform:

1. Configure the channel in WebUI.
2. Send a small image to the bot.
3. The session's user message must show a `FilePart` with a `file://`
   URI on the local disk.
4. Ask the agent to send the same file back; verify it arrives in the
   chat as a native attachment, not a markdown link.
