#!/usr/bin/env node

import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PROXY_SCRIPT = path.join(ROOT, 'scripts', 'cdp-proxy.mjs');
const PROXY_PORT = Number(process.env.CDP_PROXY_PORT || 3456);
const REQUIRED_NODE_MAJOR = 22;
const REQUIRED_CHROME_MAJOR_EXCLUSIVE = 144;
const REMOTE_DEBUGGING_HINT =
  'chrome: not connected — 请确保 Chrome 已打开，然后访问 chrome://inspect/#remote-debugging 并勾选 Allow remote debugging';

function printResultAndExit(lines, code) {
  for (const line of lines) {
    console.log(line);
  }
  process.exit(code);
}

function checkNode() {
  const major = Number(process.versions.node.split('.')[0]);
  const version = `v${process.versions.node}`;
  return {
    ok: major >= REQUIRED_NODE_MAJOR,
    major,
    version,
  };
}

function checkPort(port, host = '127.0.0.1', timeoutMs = 2000) {
  return new Promise((resolve) => {
    const socket = net.createConnection(port, host);
    const timer = setTimeout(() => {
      socket.destroy();
      resolve(false);
    }, timeoutMs);
    socket.once('connect', () => {
      clearTimeout(timer);
      socket.destroy();
      resolve(true);
    });
    socket.once('error', () => {
      clearTimeout(timer);
      resolve(false);
    });
  });
}

function activePortFiles() {
  const home = os.homedir();
  const localAppData = process.env.LOCALAPPDATA || '';
  switch (os.platform()) {
    case 'darwin':
      return [
        path.join(home, 'Library/Application Support/Google/Chrome/DevToolsActivePort'),
        path.join(home, 'Library/Application Support/Google/Chrome Canary/DevToolsActivePort'),
        path.join(home, 'Library/Application Support/Chromium/DevToolsActivePort'),
      ];
    case 'linux':
      return [
        path.join(home, '.config/google-chrome/DevToolsActivePort'),
        path.join(home, '.config/google-chrome-stable/DevToolsActivePort'),
        path.join(home, '.config/chromium/DevToolsActivePort'),
      ];
    case 'win32':
      return [
        path.join(localAppData, 'Google/Chrome/User Data/DevToolsActivePort'),
        path.join(localAppData, 'Google/Chrome SxS/User Data/DevToolsActivePort'),
        path.join(localAppData, 'Chromium/User Data/DevToolsActivePort'),
      ];
    default:
      return [];
  }
}

async function detectChromePort() {
  for (const filePath of activePortFiles()) {
    try {
      const lines = fs.readFileSync(filePath, 'utf8').trim().split(/\r?\n/).filter(Boolean);
      const port = parseInt(lines[0], 10);
      if (port > 0 && port < 65536 && (await checkPort(port))) {
        return port;
      }
    } catch {
      // ignore
    }
  }

  for (const port of [9222, 9229, 9333]) {
    if (await checkPort(port)) {
      return port;
    }
  }
  return null;
}

function chromeBinaryCandidates() {
  const programFiles = process.env.ProgramFiles || 'C:\\Program Files';
  const programFilesX86 = process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)';

  switch (os.platform()) {
    case 'darwin':
      return [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
      ];
    case 'linux':
      return [
        'google-chrome',
        'google-chrome-stable',
        'chromium',
        'chromium-browser',
      ];
    case 'win32':
      return [
        path.join(programFiles, 'Google', 'Chrome', 'Application', 'chrome.exe'),
        path.join(programFilesX86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
        path.join(programFiles, 'Chromium', 'Application', 'chrome.exe'),
      ];
    default:
      return [];
  }
}

function parseVersion(rawText) {
  const match = rawText.match(/(\d+)\.(\d+)\.(\d+)\.(\d+)/);
  if (!match) return null;
  return {
    raw: match[0],
    major: Number(match[1]),
  };
}

function tryReadBinaryVersion(candidate) {
  const result = spawnSync(candidate, ['--version'], {
    encoding: 'utf8',
    shell: false,
  });
  if (result.error || result.status !== 0) return null;
  const parsed = parseVersion(`${result.stdout}\n${result.stderr}`);
  if (!parsed) return null;
  return { ...parsed, source: candidate };
}

async function tryReadVersionFromRemoteDebugging(port) {
  if (!port) return null;
  try {
    const response = await fetch(`http://127.0.0.1:${port}/json/version`, {
      signal: AbortSignal.timeout(2000),
    });
    const payload = await response.json();
    const parsed = parseVersion(`${payload.Browser || ''}\n${payload['User-Agent'] || ''}`);
    if (!parsed) return null;
    return { ...parsed, source: `remote-debugging:${port}` };
  } catch {
    return null;
  }
}

async function detectChromeVersion(port) {
  for (const candidate of chromeBinaryCandidates()) {
    const version = tryReadBinaryVersion(candidate);
    if (version) return version;
  }
  return tryReadVersionFromRemoteDebugging(port);
}

function httpGetJson(url, timeoutMs = 3000) {
  return fetch(url, { signal: AbortSignal.timeout(timeoutMs) })
    .then(async (res) => {
      try {
        return JSON.parse(await res.text());
      } catch {
        return null;
      }
    })
    .catch(() => null);
}

function startProxyDetached() {
  const logFile = path.join(os.tmpdir(), 'browser-use-cdp-proxy.log');
  const logFd = fs.openSync(logFile, 'a');
  const child = spawn(process.execPath, [PROXY_SCRIPT], {
    detached: true,
    stdio: ['ignore', logFd, logFd],
    ...(os.platform() === 'win32' ? { windowsHide: true } : {}),
  });
  child.unref();
  fs.closeSync(logFd);
}

async function ensureProxy() {
  const targetsUrl = `http://127.0.0.1:${PROXY_PORT}/targets`;
  const targets = await httpGetJson(targetsUrl);
  if (Array.isArray(targets)) {
    return { ok: true, status: 'ready' };
  }

  startProxyDetached();
  await new Promise((resolve) => setTimeout(resolve, 1500));

  for (let i = 0; i < 10; i += 1) {
    const result = await httpGetJson(targetsUrl, 5000);
    if (Array.isArray(result)) {
      return { ok: true, status: 'ready' };
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }

  return { ok: false, status: 'timeout', logFile: path.join(os.tmpdir(), 'browser-use-cdp-proxy.log') };
}

async function main() {
  const node = checkNode();
  const port = await detectChromePort();
  const chrome = await detectChromeVersion(port);

  const nodeOkLine = node.ok
    ? `node: ok (${node.version})`
    : `node: unsupported (${node.version}, requires >= ${REQUIRED_NODE_MAJOR})`;

  const chromeOk = chrome && chrome.major > REQUIRED_CHROME_MAJOR_EXCLUSIVE;
  const chromeLine = chromeOk
    ? `chrome: ok (${chrome.raw}, source: ${chrome.source})`
    : chrome
      ? `chrome: unsupported (${chrome.raw}, requires > ${REQUIRED_CHROME_MAJOR_EXCLUSIVE})`
      : 'chrome: unsupported (version not detected, requires > 144)';

  if (!node.ok || !chromeOk) {
    printResultAndExit(
      [
        nodeOkLine,
        chromeLine,
        'mode: agent-browser',
      ],
      2,
    );
  }

  if (!port) {
    printResultAndExit(
      [
        nodeOkLine,
        chromeLine,
        REMOTE_DEBUGGING_HINT,
      ],
      1,
    );
  }

  const proxy = await ensureProxy();
  if (!proxy.ok) {
    printResultAndExit(
      [
        nodeOkLine,
        chromeLine,
        `remote-debugging: ok (port ${port})`,
        `proxy: failed (${proxy.status})`,
        `proxy-log: ${proxy.logFile}`,
      ],
      3,
    );
  }

  printResultAndExit(
    [
      nodeOkLine,
      chromeLine,
      `remote-debugging: ok (port ${port})`,
      'proxy: ready',
      'mode: cdp-direct',
    ],
    0,
  );
}

await main();
