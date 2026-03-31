/**
 * runner.ts — flocks DingTalk bridge
 *
 * 构造一个最小化的 OpenClaw PluginRuntime/ClawdbotPluginApi 模拟层，
 * 使 plugin.ts 无需任何修改即可在 flocks 环境中运行。
 *
 * 关键替换：
 *   plugin.ts 内部的 streamFromGateway() 会调用
 *     POST http://127.0.0.1:{port}/v1/chat/completions  (SSE)
 *   我们让 port 指向 flocks，并在 flocks 上注册 /v1/chat/completions 端点
 *   ——但更干净的做法是：让 cfg.gateway.port 指向一个本文件内嵌的
 *   轻量 HTTP 代理，该代理把 OpenAI 格式转换成 flocks 真实 API 调用：
 *     POST /api/session          → 创建/复用 session
 *     POST /api/session/{id}/message → 触发推理
 *     GET  /api/event            → SSE，过滤 message.part.updated.delta
 *   然后以 OpenAI SSE 格式回流给 plugin.ts，零侵入 plugin.ts。
 *
 * 启动方式（由 dingtalk.py 通过 subprocess 调用）：
 *   DINGTALK_CLIENT_ID=xxx DINGTALK_CLIENT_SECRET=xxx FLOCKS_PORT=8000 bun run runner.ts
 */

import plugin from './dingtalk-openclaw-connector/plugin.ts';
import { createServer, type IncomingMessage, type ServerResponse } from 'http';

// ── 环境变量 ────────────────────────────────────────────────────────────────
const CLIENT_ID      = process.env.DINGTALK_CLIENT_ID     || '';
const CLIENT_SECRET  = process.env.DINGTALK_CLIENT_SECRET || '';
const FLOCKS_PORT    = parseInt(process.env.FLOCKS_PORT    || '8000', 10);
const FLOCKS_AGENT   = process.env.FLOCKS_AGENT            || '';
const GATEWAY_TOKEN  = process.env.FLOCKS_GATEWAY_TOKEN    || '';
const DEBUG          = process.env.DINGTALK_DEBUG === 'true';
const ACCOUNT_ID     = process.env.DINGTALK_ACCOUNT_ID     || '__default__';

// 代理监听在随机端口，plugin.ts 的 streamFromGateway 打到这里
const PROXY_HOST = '127.0.0.1';
let   PROXY_PORT = 0;  // 启动后确定

if (!CLIENT_ID || !CLIENT_SECRET) {
  console.error('[runner] 缺少环境变量 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET');
  process.exit(1);
}

const FLOCKS_BASE = `http://127.0.0.1:${FLOCKS_PORT}`;

// ── session 映射：session_key → flocks session_id ──────────────────────────
const sessionMap = new Map<string, string>();

/**
 * 将 sessionKey（可能是 JSON 字符串）解析为可读的 session 标题。
 * 格式与飞书/企微保持一致：
 *   DM  → [Dingtalk] DM — {senderName}
 *   群聊 → [Dingtalk] {chatId}
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
    // sessionKey 不是 JSON，直接使用
    return `[Dingtalk] ${sessionKey}`;
  }
}

async function getOrCreateSession(sessionKey: string, agentName: string): Promise<string> {
  const existing = sessionMap.get(sessionKey);
  if (existing) {
    // 验证 session 还存在
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
  if (!r.ok) throw new Error(`创建 session 失败: ${r.status} ${await r.text()}`);

  const data: any = await r.json();
  const sessionId: string = data.id;
  sessionMap.set(sessionKey, sessionId);
  console.log(`[runner] session created: key=${sessionKey} id=${sessionId}`);
  return sessionId;
}

// ── 把 flocks /api/event SSE 转换成 OpenAI delta SSE ─────────────────────
async function* flocksToOpenAIStream(
  sessionId: string,
  userText: string,
  agentName: string,
  systemPrompts: string[],
): AsyncGenerator<string, void, unknown> {
  // 1. 连接 event SSE（先建连接，再发消息，避免错过首帧）
  const eventUrl = `${FLOCKS_BASE}/api/event`;
  const eventResp = await fetch(eventUrl, {
    headers: { Accept: 'text/event-stream' },
  });
  if (!eventResp.ok || !eventResp.body) {
    throw new Error(`连接 event SSE 失败: ${eventResp.status}`);
  }

  // 2. 发送消息（触发推理）
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
    throw new Error(`发送消息失败: ${msgResp.status} ${await msgResp.text()}`);
  }

  // 3. 消费 event SSE，提取 message.part.updated 的 delta
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
        const delta: string = props.delta || '';
        const partType: string = props.part?.type || '';
        if (delta && partType === 'text') {
          yield openAIChunk(delta);
        }
      }

      // 推理完成信号
      if (type === 'message.updated') {
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

// ── 内嵌 HTTP 代理：把 /v1/chat/completions 转成 flocks 调用 ───────────────
function startProxy(): Promise<number> {
  return new Promise((resolve) => {
    const server = createServer(async (req: IncomingMessage, res: ServerResponse) => {
      if (req.method !== 'POST' || req.url !== '/v1/chat/completions') {
        res.writeHead(404);
        res.end('Not found');
        return;
      }

      // 读取请求体
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
        console.error('[proxy] 处理失败:', err.message);
        res.write(`data: ${JSON.stringify({ error: { message: err.message } })}\n\n`);
        res.write('data: [DONE]\n\n');
      }
      res.end();
    });

    server.listen(0, PROXY_HOST, () => {
      const addr = server.address() as { port: number };
      PROXY_PORT = addr.port;
      console.log(`[runner] 代理监听 ${PROXY_HOST}:${PROXY_PORT} → flocks :${FLOCKS_PORT}`);
      resolve(PROXY_PORT);
    });
  });
}

// ── 构造假 runtime ──────────────────────────────────────────────────────────
const fakeRuntime = {
  gateway: { port: PROXY_PORT },  // startAccount 启动后会读最新值
  channel: {
    activity: {
      record: (channelId: string, accountId: string, event: string) => {
        if (DEBUG) console.log(`[runner][activity] ${channelId}/${accountId}: ${event}`);
      },
    },
  },
};

// ── 构造假 api ──────────────────────────────────────────────────────────────
const fakeApi: any = {
  runtime: fakeRuntime,
  logger: {
    info:  (msg: string) => console.log(`[plugin] ${msg}`),
    warn:  (msg: string) => console.warn(`[plugin] ${msg}`),
    error: (msg: string) => console.error(`[plugin] ${msg}`),
    debug: (msg: string) => { if (DEBUG) console.log(`[plugin:debug] ${msg}`); },
  },

  registerChannel({ plugin: channelPlugin }: any) {
    console.log(`[runner] registerChannel → 启动 startAccount (accountId=${ACCOUNT_ID})`);

    const abortController = new AbortController();
    const shutdown = () => {
      console.log('[runner] 收到停止信号，中止...');
      abortController.abort();
    };
    process.once('SIGTERM', shutdown);
    process.once('SIGINT',  shutdown);

    // cfg 里 gateway.port 指向本地代理
    const cfg = {
      channels: {
        'dingtalk-connector': {
          clientId:     CLIENT_ID,
          clientSecret: CLIENT_SECRET,
          gatewayToken: GATEWAY_TOKEN,
          debug:        DEBUG,
          ...(FLOCKS_AGENT ? { defaultAgent: FLOCKS_AGENT } : {}),
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
      console.error('[runner] startAccount 异常:', err.message);
      process.exit(1);
    });
  },

  registerGatewayMethod(name: string, _fn: any) {
    if (DEBUG) console.log(`[runner] registerGatewayMethod: ${name} (noop)`);
  },
};

// ── 启动：先开代理，再注册插件 ───────────────────────────────────────────────
(async () => {
  await startProxy();

  // 更新 runtime 里的端口（startAccount 读 cfg.gateway.port，cfg 在 registerChannel 里构造，已用最新值）
  fakeRuntime.gateway.port = PROXY_PORT;

  console.log(`[runner] 启动 DingTalk connector → flocks :${FLOCKS_PORT}`);
  plugin.register(fakeApi);
})();
