#!/usr/bin/env node

import {
  DisconnectReason,
  downloadMediaMessage,
  fetchLatestBaileysVersion,
  makeWASocket,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import express from 'express';
import pino from 'pino';
import path from 'path';
import { createHash, randomBytes } from 'crypto';
import {
  existsSync,
  mkdirSync,
  readFileSync,
  unlinkSync,
  writeFileSync,
} from 'fs';
import { execFileSync } from 'child_process';
import { fileURLToPath } from 'url';
import { tmpdir } from 'os';

const args = process.argv.slice(2);

function getArg(name, defaultValue) {
  const idx = args.indexOf(`--${name}`);
  return idx >= 0 && args[idx + 1] ? args[idx + 1] : defaultValue;
}

function envFlag(name, defaultValue = false) {
  const raw = process.env[name];
  if (raw === undefined) return defaultValue;
  return ['1', 'true', 'yes', 'on'].includes(String(raw).toLowerCase());
}

const PORT = Number.parseInt(getArg('port', '3100'), 10);
const SESSION_DIR = getArg('session', path.join(process.env.HOME || '.', '.flocks', 'workspace', 'channels', 'whatsapp', 'session'));
const MEDIA_DIR = process.env.FLOCKS_WHATSAPP_MEDIA_DIR
  || path.join(process.env.HOME || '.', '.flocks', 'workspace', 'channels', 'whatsapp', 'media');
const MODE = getArg('mode', process.env.FLOCKS_WHATSAPP_MODE || 'bot');
const REPLY_PREFIX = process.env.FLOCKS_WHATSAPP_REPLY_PREFIX || '';
const CHUNK_DELAY_MS = Number.parseInt(process.env.FLOCKS_WHATSAPP_CHUNK_DELAY_MS || '300', 10);
const SEND_TIMEOUT_MS = Number.parseInt(process.env.FLOCKS_WHATSAPP_SEND_TIMEOUT_MS || '60000', 10);
const BRIDGE_TOKEN = process.env.FLOCKS_WHATSAPP_BRIDGE_TOKEN || '';
const CONFIG_HASH = process.env.FLOCKS_WHATSAPP_CONFIG_HASH || '';
const ALLOWED_MEDIA_ROOTS = String(process.env.FLOCKS_WHATSAPP_ALLOWED_MEDIA_ROOTS || '')
  .split(path.delimiter)
  .map(item => item.trim())
  .filter(Boolean)
  .map(item => path.resolve(item));
const PAIR_ONLY = args.includes('--pair-only');
const PAIR_JSON = args.includes('--pair-json');
const PAIR_TIMEOUT_MS = Number.parseInt(process.env.FLOCKS_WHATSAPP_PAIR_TIMEOUT_MS || '120000', 10);
const DEBUG = envFlag('FLOCKS_WHATSAPP_DEBUG');

mkdirSync(SESSION_DIR, { recursive: true });
mkdirSync(MEDIA_DIR, { recursive: true });

let scriptHash = '';
let tokenHash = '';
try {
  scriptHash = createHash('sha256')
    .update(readFileSync(fileURLToPath(import.meta.url)))
    .digest('hex')
    .slice(0, 16);
} catch {}
if (BRIDGE_TOKEN) {
  tokenHash = createHash('sha256').update(BRIDGE_TOKEN).digest('hex').slice(0, 12);
}

let sock = null;
let connectionState = 'disconnected';
let messageQueue = [];
const messageStore = new Map();
const recentlySent = new Map();
let sendQueue = Promise.resolve();

const logger = pino({ level: DEBUG ? 'debug' : 'silent' });

function emitPairEvent(event) {
  if (!PAIR_JSON) return;
  try {
    console.log(JSON.stringify({ ts: Date.now(), ...event }));
  } catch {}
}

function rememberSent(id) {
  if (!id) return;
  recentlySent.set(id, Date.now());
  if (recentlySent.size > 1000) {
    const cutoff = Date.now() - 10 * 60 * 1000;
    for (const [key, ts] of recentlySent.entries()) {
      if (ts < cutoff) recentlySent.delete(key);
    }
  }
}

function normalizeJid(value) {
  if (!value) return '';
  return String(value).replace(/:\d+@/, '@');
}

function splitLongMessage(message, limit = 4096) {
  const text = String(message || '');
  if (!text) return [];
  if (text.length <= limit) return [text];
  const chunks = [];
  let remaining = text;
  while (remaining.length > limit) {
    let splitAt = remaining.lastIndexOf('\n', limit);
    if (splitAt < Math.floor(limit / 2)) splitAt = remaining.lastIndexOf(' ', limit);
    if (splitAt < 1) splitAt = limit;
    chunks.push(remaining.slice(0, splitAt).trimEnd());
    remaining = remaining.slice(splitAt).trimStart();
  }
  if (remaining) chunks.push(remaining);
  return chunks;
}

function enqueueSend(fn) {
  const task = sendQueue.then(() => fn(), () => fn());
  sendQueue = task.catch(() => {});
  return task;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function sendWithTimeout(chatId, payload, options = {}) {
  return enqueueSend(() => {
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`sendMessage timed out after ${SEND_TIMEOUT_MS}ms`)), SEND_TIMEOUT_MS);
    });
    return Promise.race([
      sock.sendMessage(chatId, payload, options),
      timeout,
    ]).finally(() => clearTimeout(timer));
  });
}

function getMessageContent(msg) {
  const message = msg?.message || {};
  return message.ephemeralMessage?.message
    || message.viewOnceMessage?.message
    || message.viewOnceMessageV2?.message
    || message;
}

function extractText(content) {
  return content.conversation
    || content.extendedTextMessage?.text
    || content.imageMessage?.caption
    || content.videoMessage?.caption
    || content.documentMessage?.caption
    || '';
}

function detectMediaBlock(content) {
  if (content.imageMessage) return { block: content.imageMessage, type: 'image', ext: '.jpg' };
  if (content.videoMessage) return { block: content.videoMessage, type: 'video', ext: '.mp4' };
  if (content.audioMessage) return { block: content.audioMessage, type: content.audioMessage.ptt ? 'ptt' : 'audio', ext: '.ogg' };
  if (content.documentMessage) return { block: content.documentMessage, type: 'document', ext: path.extname(content.documentMessage.fileName || '') || '.bin' };
  if (content.stickerMessage) return { block: content.stickerMessage, type: 'sticker', ext: '.webp' };
  return null;
}

function safeExt(ext) {
  const cleaned = String(ext || '').toLowerCase().replace(/[^a-z0-9.]/g, '');
  return cleaned && cleaned.startsWith('.') ? cleaned : '.bin';
}

async function cacheInboundMedia(msg, mediaInfo) {
  try {
    const buffer = await downloadMediaMessage(
      msg,
      'buffer',
      {},
      { logger, reuploadRequest: sock.updateMediaMessage },
    );
    const fileName = `${mediaInfo.type}_${randomBytes(8).toString('hex')}${safeExt(mediaInfo.ext)}`;
    const filePath = path.join(MEDIA_DIR, fileName);
    writeFileSync(filePath, buffer);
    return filePath;
  } catch (err) {
    console.warn('[whatsapp-bridge] failed to cache media:', err.message);
    return '';
  }
}

function buildBridgeEvent({ msg, chatId, senderId, isGroup, body, mediaInfo, mediaPath, fromOwner }) {
  const content = getMessageContent(msg);
  const contextInfo = content.extendedTextMessage?.contextInfo
    || content.imageMessage?.contextInfo
    || content.videoMessage?.contextInfo
    || content.documentMessage?.contextInfo
    || {};
  const botIds = new Set([
    normalizeJid(sock?.user?.id),
    normalizeJid(sock?.user?.lid),
  ].filter(Boolean));
  const mentionedJids = (contextInfo.mentionedJid || []).map(normalizeJid);
  const mentioned = mentionedJids.some(jid => botIds.has(jid));
  const quotedParticipant = normalizeJid(contextInfo.participant || '');
  const isReplyToBot = Boolean(quotedParticipant && botIds.has(quotedParticipant));
  return {
    messageId: msg.key.id || '',
    chatId,
    senderId,
    senderName: msg.pushName || '',
    isGroup,
    body,
    hasMedia: Boolean(mediaPath),
    mediaUrls: mediaPath ? [mediaPath] : [],
    mediaType: mediaInfo?.type || '',
    mime: mediaInfo?.block?.mimetype || '',
    quotedMessageId: contextInfo.stanzaId || '',
    quotedParticipant,
    mentioned,
    isReplyToBot,
    fromOwner: Boolean(fromOwner),
  };
}

async function startSocket() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  const { version } = await fetchLatestBaileysVersion();
  sock = makeWASocket({
    version,
    auth: state,
    logger,
    printQRInTerminal: false,
    browser: ['Flocks', 'Chrome', '120.0'],
    syncFullHistory: false,
    markOnlineOnConnect: false,
    getMessage: async (key) => messageStore.get(key?.id) || { conversation: '' },
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      emitPairEvent({ event: 'qr', qr });
      if (!PAIR_JSON) console.log(qr);
    }
    if (connection === 'close') {
      const reason = new Boom(lastDisconnect?.error)?.output?.statusCode;
      connectionState = 'disconnected';
      if (reason === DisconnectReason.loggedOut) {
        emitPairEvent({ event: 'error', error: 'logged_out', reason });
        process.exit(1);
      }
      emitPairEvent({ event: 'disconnected', reason });
      setTimeout(startSocket, reason === 515 ? 1000 : 3000);
    } else if (connection === 'open') {
      connectionState = 'connected';
      emitPairEvent({
        event: 'connected',
        user: sock?.user ? { id: sock.user.id || null, name: sock.user.name || sock.user.verifiedName || null } : null,
      });
      if (PAIR_ONLY) setTimeout(() => process.exit(0), 1500);
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify' && type !== 'append') return;
    for (const msg of messages || []) {
      if (!msg?.message) continue;
      const chatId = normalizeJid(msg.key.remoteJid);
      const senderId = normalizeJid(msg.key.participant || chatId);
      const isGroup = chatId.endsWith('@g.us');
      const fromMe = Boolean(msg.key.fromMe);

      if (fromMe) {
        if (recentlySent.has(msg.key.id)) continue;
        if (MODE === 'bot') continue;
        const selfIds = new Set([
          normalizeJid(sock?.user?.id).replace(/:\d+@/, '@'),
          normalizeJid(sock?.user?.lid).replace(/:\d+@/, '@'),
        ].filter(Boolean));
        if (!selfIds.has(chatId)) continue;
      } else if (MODE === 'self-chat') {
        continue;
      }

      const content = getMessageContent(msg);
      const body = extractText(content);
      const mediaInfo = detectMediaBlock(content);
      const mediaPath = mediaInfo ? await cacheInboundMedia(msg, mediaInfo) : '';
      if (!body && !mediaPath) continue;
      const event = buildBridgeEvent({
        msg,
        chatId,
        senderId,
        isGroup,
        body,
        mediaInfo,
        mediaPath,
        fromOwner: fromMe,
      });
      messageStore.set(msg.key.id, msg);
      messageQueue.push(event);
      if (messageQueue.length > 1000) messageQueue.shift();
    }
  });
}

function validateHost(req, res, next) {
  const raw = String(req.headers.host || '').trim();
  const host = raw.includes(':') ? raw.slice(0, raw.lastIndexOf(':')) : raw;
  const normalized = host.replace(/^\[|\]$/g, '').toLowerCase();
  if (!['localhost', '127.0.0.1', '::1'].includes(normalized)) {
    return res.status(400).json({ error: 'Invalid Host header' });
  }
  return next();
}

function validateToken(req, res, next) {
  const auth = String(req.headers.authorization || '');
  const bearer = auth.toLowerCase().startsWith('bearer ') ? auth.slice(7).trim() : '';
  const token = String(req.headers['x-flocks-bridge-token'] || bearer);
  if (!BRIDGE_TOKEN || token !== BRIDGE_TOKEN) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  return next();
}

function isAllowedMediaPath(filePath) {
  if (ALLOWED_MEDIA_ROOTS.length === 0) return false;
  const resolved = path.resolve(filePath);
  return ALLOWED_MEDIA_ROOTS.some(root => resolved === root || resolved.startsWith(`${root}${path.sep}`));
}

function inferMediaPayload(filePath, mediaType, caption) {
  const buffer = readFileSync(filePath);
  const ext = path.extname(filePath).slice(1).toLowerCase();
  const type = mediaType || (
    ['jpg', 'jpeg', 'png', 'webp', 'gif'].includes(ext) ? 'image'
      : ['mp4', 'mov', 'mkv', 'webm'].includes(ext) ? 'video'
        : ['ogg', 'opus', 'mp3', 'wav', 'm4a'].includes(ext) ? 'audio'
          : 'document'
  );
  if (type === 'image') return { image: buffer, caption: caption || undefined };
  if (type === 'video') return { video: buffer, caption: caption || undefined };
  if (type === 'audio') {
    let audioBuffer = buffer;
    let audioExt = ext;
    let tmpPath = null;
    if (!['ogg', 'opus'].includes(ext)) {
      tmpPath = path.join(tmpdir(), `flocks_voice_${randomBytes(6).toString('hex')}.ogg`);
      try {
        execFileSync('ffmpeg', ['-y', '-i', filePath, '-ar', '48000', '-ac', '1', '-c:a', 'libopus', tmpPath], { timeout: 30000, stdio: 'pipe' });
        audioBuffer = readFileSync(tmpPath);
        audioExt = 'ogg';
      } catch {
        audioBuffer = buffer;
      } finally {
        try { if (tmpPath && existsSync(tmpPath)) unlinkSync(tmpPath); } catch {}
      }
    }
    return { audio: audioBuffer, mimetype: audioExt === 'ogg' || audioExt === 'opus' ? 'audio/ogg; codecs=opus' : 'audio/mpeg', ptt: audioExt === 'ogg' || audioExt === 'opus' };
  }
  return {
    document: buffer,
    fileName: path.basename(filePath),
    mimetype: 'application/octet-stream',
    caption: caption || undefined,
  };
}

if (PAIR_ONLY) {
  emitPairEvent({ event: 'started', session: SESSION_DIR });
  setTimeout(() => {
    emitPairEvent({ event: 'error', error: 'pairing_timeout' });
    process.exit(2);
  }, PAIR_TIMEOUT_MS);
  await startSocket();
} else {
  await startSocket();
  const app = express();
  app.use(express.json({ limit: '2mb' }));
  app.use(validateHost);
  app.use(validateToken);

  app.get('/messages', (_req, res) => {
    const messages = messageQueue.splice(0, messageQueue.length);
    res.json(messages);
  });

  app.post('/send', async (req, res) => {
    if (!sock || connectionState !== 'connected') return res.status(503).json({ error: 'Not connected to WhatsApp' });
    const { chatId, message, replyTo } = req.body || {};
    if (!chatId || !message) return res.status(400).json({ error: 'chatId and message are required' });
    try {
      const text = REPLY_PREFIX ? `${REPLY_PREFIX}${message}` : String(message);
      const chunks = splitLongMessage(text);
      const messageIds = [];
      for (let i = 0; i < chunks.length; i += 1) {
        const quoted = replyTo && i === 0 ? messageStore.get(replyTo) : null;
        const options = quoted ? { quoted } : {};
        const sent = await sendWithTimeout(chatId, { text: chunks[i] }, options);
        rememberSent(sent?.key?.id);
        if (sent?.key?.id) messageIds.push(sent.key.id);
        if (i < chunks.length - 1) await sleep(CHUNK_DELAY_MS);
      }
      res.json({ success: true, messageId: messageIds[messageIds.length - 1] || '', messageIds });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  app.post('/send-media', async (req, res) => {
    if (!sock || connectionState !== 'connected') return res.status(503).json({ error: 'Not connected to WhatsApp' });
    const { chatId, filePath, mediaType, caption } = req.body || {};
    if (!chatId || !filePath) return res.status(400).json({ error: 'chatId and filePath are required' });
    if (!existsSync(filePath)) return res.status(404).json({ error: `File not found: ${filePath}` });
    if (!isAllowedMediaPath(filePath)) return res.status(403).json({ error: `File path is not allowed: ${filePath}` });
    try {
      const payload = inferMediaPayload(filePath, mediaType, caption);
      const sent = await sendWithTimeout(chatId, payload);
      rememberSent(sent?.key?.id);
      res.json({ success: true, messageId: sent?.key?.id || '' });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  app.get('/health', (_req, res) => {
    res.json({
      status: connectionState,
      queueLength: messageQueue.length,
      uptime: process.uptime(),
      scriptHash,
      sessionPath: path.resolve(SESSION_DIR),
      mediaDir: path.resolve(MEDIA_DIR),
      mode: MODE,
      configHash: CONFIG_HASH,
      tokenHash,
    });
  });

  app.listen(PORT, '127.0.0.1', () => {
    console.log(`[whatsapp-bridge] listening on 127.0.0.1:${PORT}`);
  });
}
