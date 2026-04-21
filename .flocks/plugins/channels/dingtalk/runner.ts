/**
 * runner.ts — flocks DingTalk bridge
 *
 * Constructs a minimal OpenClaw PluginRuntime/ClawdbotPluginApi shim so that
 * plugin.ts can run inside the flocks environment without any modifications.
 *
 * Key substitution:
 *   plugin.ts internally calls streamFromGateway(), which posts to
 *     POST http://127.0.0.1:{port}/v1/chat/completions  (SSE)
 *   We point that port at a lightweight HTTP proxy embedded in this file.
 *   The proxy translates the OpenAI format into real flocks API calls:
 *     POST /api/session          → create or reuse a session
 *     POST /api/session/{id}/message → trigger inference
 *     GET  /api/event            → SSE, filter message.part.updated.delta
 *   Results are streamed back to plugin.ts as OpenAI SSE chunks — zero
 *   modifications to plugin.ts required.
 *
 * Startup (invoked by dingtalk.py via subprocess):
 *   DINGTALK_CLIENT_ID=xxx DINGTALK_CLIENT_SECRET=xxx FLOCKS_PORT=8000 bun run runner.ts
 */

import plugin from './dingtalk-openclaw-connector/plugin.ts';
import { createServer, type IncomingMessage, type ServerResponse } from 'http';

// ── Environment variables ────────────────────────────────────────────────────
const CLIENT_ID      = process.env.DINGTALK_CLIENT_ID     || '';
const CLIENT_SECRET  = process.env.DINGTALK_CLIENT_SECRET || '';
const FLOCKS_PORT    = parseInt(process.env.FLOCKS_PORT    || '8000', 10);
const FLOCKS_AGENT   = process.env.FLOCKS_AGENT            || '';
const GATEWAY_TOKEN  = process.env.FLOCKS_GATEWAY_TOKEN    || '';
const DEBUG          = process.env.DINGTALK_DEBUG === 'true';
const ACCOUNT_ID     = process.env.DINGTALK_ACCOUNT_ID     || '__default__';

// Optional policy / behaviour fields forwarded from flocks.json → plugin.ts
const DM_POLICY             = process.env.DINGTALK_DM_POLICY            || '';
const ALLOW_FROM_RAW        = process.env.DINGTALK_ALLOW_FROM            || '';
const ALLOW_FROM            = ALLOW_FROM_RAW ? ALLOW_FROM_RAW.split(',').map(s => s.trim()).filter(Boolean) : undefined;
const SEPARATE_SESSION      = process.env.DINGTALK_SEPARATE_SESSION      !== 'false';  // default true
const GROUP_SESSION_SCOPE   = process.env.DINGTALK_GROUP_SESSION_SCOPE   || '';
const SHARED_MEMORY         = process.env.DINGTALK_SHARED_MEMORY         === 'true';

// Proxy listens on a random port; plugin.ts's streamFromGateway calls land here
const PROXY_HOST = '127.0.0.1';
let   PROXY_PORT = 0;  // resolved after startup

if (!CLIENT_ID || !CLIENT_SECRET) {
  console.error('[runner] Missing environment variables DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET');
  process.exit(1);
}

const FLOCKS_BASE = `http://127.0.0.1:${FLOCKS_PORT}`;

// ── Session map: session_key → flocks session_id ───────────────────────────
const sessionMap = new Map<string, string>();

/**
 * Parse a sessionKey (possibly a JSON string) into a human-readable session title.
 * Format is consistent with Feishu/WeCom:
 *   DM    → [Dingtalk] DM — {senderName}
 *   Group → [Dingtalk] {chatId}
 */
function buildSessionTitle(sessionKey: string): string {
  try {
    const info = JSON.parse(sessionKey);
    const chatType: string = info.chatType || '';
    const senderName: string = info.senderName || info.peerId || sessionKey;
    const chatId: string = info.peerId || info.chatId || sessionKey;
    if (chatType === 'direct') {
      return `[Dingtalk] DM — ${senderName}`;
    }
    return `[Dingtalk] ${chatId}`;
  } catch {
    // sessionKey is not JSON, use it as-is
    return `[Dingtalk] ${sessionKey}`;
  }
}

/**
 * Register the (channel, conversation) → session mapping in flocks's
 * channel_bindings table so that the channel_message tool /
 * POST /api/channel/session-send can route outbound replies back to this
 * DingTalk conversation.
 *
 * This mirrors what InboundDispatcher.binding_service.resolve_or_create
 * does for Feishu / WeCom / Telegram — DingTalk creates its session
 * out-of-process, so we have to register the binding explicitly.
 *
 * Best-effort: a failure here only means the channel_message tool will
 * 404 for this session, the inbound reply path keeps working.
 */
async function registerChannelBinding(sessionKey: string, sessionId: string): Promise<void> {
  let chatType = 'direct';
  let chatId = sessionKey;

  try {
    const info = JSON.parse(sessionKey);
    chatType = info.chatType === 'group' ? 'group' : 'direct';
    chatId = info.peerId || info.chatId || sessionKey;
  } catch {
    // sessionKey is not JSON — keep the defaults.
  }

  // The flocks-side channel_id is "dingtalk" (see ChannelMeta.id).  Other
  // values like "dingtalk-connector" are aliases declared on ChannelMeta and
  // are accepted by the registry but the canonical binding row uses the id.
  const url = `${FLOCKS_BASE}/api/channel/dingtalk/bind`;
  const body = {
    session_id: sessionId,
    chat_id: chatId,
    chat_type: chatType,
    account_id: ACCOUNT_ID === '__default__' ? 'default' : ACCOUNT_ID,
  };

  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      console.warn(`[runner] bind failed: ${r.status} ${await r.text()}`);
    } else if (DEBUG) {
      console.log(`[runner] bind ok: chat_id=${chatId} chat_type=${chatType} session=${sessionId}`);
    }
  } catch (e: any) {
    console.warn(`[runner] bind error: ${e?.message || e}`);
  }
}

async function getOrCreateSession(sessionKey: string, agentName: string): Promise<string> {
  const existing = sessionMap.get(sessionKey);
  if (existing) {
    // Verify the session still exists
    try {
      const r = await fetch(`${FLOCKS_BASE}/api/session/${existing}`);
      if (r.ok) return existing;
    } catch {}
    sessionMap.delete(sessionKey);
  }

  const body: any = { title: buildSessionTitle(sessionKey) };
  if (agentName) body.agent = agentName;

  const r = await fetch(`${FLOCKS_BASE}/api/session`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`Failed to create session: ${r.status} ${await r.text()}`);

  const data: any = await r.json();
  const sessionId: string = data.id;
  sessionMap.set(sessionKey, sessionId);
  console.log(`[runner] session created: key=${sessionKey} id=${sessionId}`);

  // Register the channel binding so outbound tools can reach this session.
  // Done after the in-memory cache write so a slow/failing bind never
  // forces a duplicate session on the next message.
  await registerChannelBinding(sessionKey, sessionId);

  return sessionId;
}

// ── Convert flocks /api/event SSE to OpenAI delta SSE ────────────────────
async function* flocksToOpenAIStream(
  sessionId: string,
  userText: string,
  agentName: string,
  systemPrompts: string[],
): AsyncGenerator<string, void, unknown> {
  // 1. Open event SSE connection first (before sending the message to avoid missing the first frame)
  const eventUrl = `${FLOCKS_BASE}/api/event`;
  const eventResp = await fetch(eventUrl, {
    headers: { Accept: 'text/event-stream' },
  });
  if (!eventResp.ok || !eventResp.body) {
    throw new Error(`Failed to connect to event SSE: ${eventResp.status}`);
  }

  // 2. Send the user message to trigger inference
  let fullText = userText;
  if (systemPrompts.length > 0) {
    const sys = systemPrompts.map(s => `<system>\n${s}\n</system>`).join('\n');
    fullText = `${sys}\n\n${userText}`;
  }

  const msgBody: any = {
    parts: [{ type: 'text', text: fullText }],
  };
  if (agentName) msgBody.agent = agentName;

  const msgResp = await fetch(`${FLOCKS_BASE}/api/session/${sessionId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(msgBody),
  });
  if (!msgResp.ok) {
    throw new Error(`Failed to send message: ${msgResp.status} ${await msgResp.text()}`);
  }

  // 3. Consume event SSE and extract message.part.updated deltas
  const reader = eventResp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finished = false;

  while (!finished) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const raw = line.slice(6).trim();
      if (!raw || raw === '[DONE]') continue;

      let event: any;
      try { event = JSON.parse(raw); } catch { continue; }

      const type = event.type;
      const props = event.properties || {};

      // text delta → OpenAI chunk
      if (type === 'message.part.updated') {
        // Only consume events belonging to this session to avoid mixing
        // deltas from concurrent DingTalk / Web / TUI conversations.
        const eventSessionId: string = props.part?.sessionID || '';
        if (eventSessionId && eventSessionId !== sessionId) continue;

        const delta: string = props.delta || '';
        const partType: string = props.part?.type || '';
        if (delta && partType === 'text') {
          yield openAIChunk(delta);
        }
      }

      // Inference completion signal
      if (type === 'message.updated') {
        const eventSessionId: string = props.info?.sessionID || '';
        if (eventSessionId && eventSessionId !== sessionId) continue;

        const finish = props.info?.finish;
        if (finish === 'stop' || finish === 'error') {
          finished = true;
        }
      }
    }
  }

  reader.cancel().catch(() => {});
}

function openAIChunk(delta: string, finish?: string): string {
  const chunk = {
    id: 'chatcmpl-flocks',
    object: 'chat.completion.chunk',
    created: Math.floor(Date.now() / 1000),
    model: 'flocks',
    choices: [{
      index: 0,
      delta: delta ? { content: delta } : {},
      finish_reason: finish ?? null,
    }],
  };
  return `data: ${JSON.stringify(chunk)}\n\n`;
}

// ── Embedded HTTP proxy: translate /v1/chat/completions into flocks calls ──
function startProxy(): Promise<number> {
  return new Promise((resolve) => {
    const server = createServer(async (req: IncomingMessage, res: ServerResponse) => {
      if (req.method !== 'POST' || req.url !== '/v1/chat/completions') {
        res.writeHead(404);
        res.end('Not found');
        return;
      }

      // Read request body
      const chunks: Buffer[] = [];
      for await (const chunk of req) chunks.push(chunk as Buffer);
      let body: any;
      try { body = JSON.parse(Buffer.concat(chunks).toString()); }
      catch { res.writeHead(400); res.end('Bad JSON'); return; }

      const messages: any[] = body.messages || [];
      const sessionKey: string = body.user || 'default';
      const agentName: string =
        (req.headers['x-openclaw-agent-id'] as string) || FLOCKS_AGENT || '';

      const systemPrompts = messages
        .filter(m => m.role === 'system' && m.content)
        .map(m => m.content as string);

      let userText = '';
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === 'user') {
          userText = typeof messages[i].content === 'string'
            ? messages[i].content
            : String(messages[i].content);
          break;
        }
      }

      if (DEBUG) {
        console.log(`[proxy] session_key=${sessionKey} agent=${agentName} preview=${userText.slice(0, 60)}`);
      }

      if (!userText) {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        res.write(openAIChunk('', 'stop'));
        res.write('data: [DONE]\n\n');
        res.end();
        return;
      }

      // Record inbound activity as early as possible, before any Flocks API
      // calls that might fail (session creation, inference, etc.).
      fetch(`${FLOCKS_BASE}/api/channel/dingtalk/record-inbound`, { method: 'POST' }).catch(() => {});

      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
      });

      try {
        const sessionId = await getOrCreateSession(sessionKey, agentName);
        for await (const chunk of flocksToOpenAIStream(sessionId, userText, agentName, systemPrompts)) {
          res.write(chunk);
        }
        res.write(openAIChunk('', 'stop'));
        res.write('data: [DONE]\n\n');
      } catch (err: any) {
        console.error('[proxy] Request failed:', err.message);
        res.write(`data: ${JSON.stringify({ error: { message: err.message } })}\n\n`);
        res.write('data: [DONE]\n\n');
      }
      res.end();
    });

    server.listen(0, PROXY_HOST, () => {
      const addr = server.address() as { port: number };
      PROXY_PORT = addr.port;
      console.log(`[runner] proxy listening on ${PROXY_HOST}:${PROXY_PORT} → flocks :${FLOCKS_PORT}`);
      resolve(PROXY_PORT);
    });
  });
}

// ── Fake runtime shim ───────────────────────────────────────────────────────
const fakeRuntime = {
  gateway: { port: PROXY_PORT },  // updated with the actual port after startProxy()
  channel: {
    activity: {
      record: (channelId: string, accountId: string, event: string) => {
        if (DEBUG) console.log(`[runner][activity] ${channelId}/${accountId}: ${event}`);
      },
    },
  },
};

// ── Fake API shim ───────────────────────────────────────────────────────────
const fakeApi: any = {
  runtime: fakeRuntime,
  logger: {
    info:  (msg: string) => console.log(`[plugin] ${msg}`),
    warn:  (msg: string) => console.warn(`[plugin] ${msg}`),
    error: (msg: string) => console.error(`[plugin] ${msg}`),
    debug: (msg: string) => { if (DEBUG) console.log(`[plugin:debug] ${msg}`); },
  },

  registerChannel({ plugin: channelPlugin }: any) {
    console.log(`[runner] registerChannel → starting startAccount (accountId=${ACCOUNT_ID})`);

    const abortController = new AbortController();
    const shutdown = () => {
      console.log('[runner] shutdown signal received, aborting...');
      abortController.abort();
    };
    process.once('SIGTERM', shutdown);
    process.once('SIGINT',  shutdown);

    // cfg.gateway.port points to the local proxy
    const cfg = {
      channels: {
        'dingtalk-connector': {
          clientId:     CLIENT_ID,
          clientSecret: CLIENT_SECRET,
          gatewayToken: GATEWAY_TOKEN,
          debug:        DEBUG,
          ...(FLOCKS_AGENT ? { defaultAgent: FLOCKS_AGENT } : {}),
          // Optional policy / behaviour fields (only set when non-empty / non-default)
          ...(DM_POLICY           ? { dmPolicy: DM_POLICY }                         : {}),
          ...(ALLOW_FROM          ? { allowFrom: ALLOW_FROM }                        : {}),
          ...(SEPARATE_SESSION    ? {} : { separateSessionByConversation: false }),
          ...(GROUP_SESSION_SCOPE ? { groupSessionScope: GROUP_SESSION_SCOPE }        : {}),
          ...(SHARED_MEMORY       ? { sharedMemoryAcrossConversations: true }         : {}),
        },
      },
      gateway: { port: PROXY_PORT },
    };

    channelPlugin.gateway.startAccount({
      account: {
        accountId: ACCOUNT_ID,
        config: cfg.channels['dingtalk-connector'],
      },
      cfg,
      abortSignal: abortController.signal,
      log: {
        info:  (msg: string) => console.log(`[dingtalk] ${msg}`),
        warn:  (msg: string) => console.warn(`[dingtalk] ${msg}`),
        error: (msg: string) => console.error(`[dingtalk] ${msg}`),
        debug: (msg: string) => { if (DEBUG) console.log(`[dingtalk:debug] ${msg}`); },
      },
    }).catch((err: Error) => {
      console.error('[runner] startAccount error:', err.message);
      process.exit(1);
    });
  },

  registerGatewayMethod(name: string, _fn: any) {
    if (DEBUG) console.log(`[runner] registerGatewayMethod: ${name} (noop)`);
  },
};

// ── Startup: launch proxy first, then register the plugin ───────────────────
(async () => {
  await startProxy();

  // Sync the resolved port into fakeRuntime (cfg.gateway.port is set inline in registerChannel)
  fakeRuntime.gateway.port = PROXY_PORT;

  console.log(`[runner] starting DingTalk connector → flocks :${FLOCKS_PORT}`);
  plugin.register(fakeApi);
})();
