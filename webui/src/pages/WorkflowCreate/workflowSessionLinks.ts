// The workflow list is shared by every user, so "a workflow appeared while
// this page was open" says nothing about who created it. A workflow belongs to
// this chat only when one of the chat's own tool calls writes into its
// directory (`<root>/plugins/workflows/<id>/`, global or project scoped).
//
// Only write targets count: reading another user's new workflow (read, glob,
// grep, a copy source) must not make it this chat's. Shell commands count only
// when they contain a write operation, and only when no file tool wrote a
// candidate.
// Known gaps, all off the workflow-builder path (it writes with the write
// tool and absolute paths):
// - files written by a delegated subagent session, unless the parent's own
//   call names the path;
// - a writing shell command links every workflow path in it, sources included
//   (`cp <bob's>/workflow.json /tmp/`), since telling them apart means parsing
//   the shell;
// - shell writes this cannot see: paths relative to a workdir at the
//   workflows root, and downloaders such as `curl -o` or `git clone`.

export type WorkflowLinkKind = 'write' | 'shell';

export interface WorkflowLink {
  id: string;
  kind: WorkflowLinkKind;
}

// Path arguments run up to the next separator, so ids may contain spaces.
const WORKFLOW_DIR_IN_PATH = /plugins[\\/]+workflows[\\/]+([^\\/]+)/g;
// Quoted shell words keep spaces but end at quotes and shell syntax.
const WORKFLOW_DIR_IN_QUOTED = /plugins[\\/]+workflows[\\/]+([^\\/"'`;&|<>(){}[\],*?]+)/g;
// In free text the id also ends at whitespace and full-width punctuation.
const WORKFLOW_DIR_IN_TEXT = /plugins[\\/]+workflows[\\/]+([^\\/\s"'`;&|<>(){}[\],*?，。；：、！？（）【】「」『』《》“”‘’…]+)/g;
const QUOTED = /"([^"]*)"|'([^']*)'/g;
const PATCH_WRITE_HEADER = /^\*\*\* (?:Add|Update) File:(.*)$/gm;
// mkdir/touch/tee/cp/mv/install/rsync, `>`/`>>` redirects other than to
// /dev/null or file descriptors, and Python writes inside heredocs or -c.
const SHELL_WRITE = /\b(?:mkdir|touch|tee|cp|mv|install|rsync)\b|(?:^|[^0-9&>])>>?(?![>&])(?!\s*\/dev\/null)|\.(?:write_text|write_bytes|mkdir|makedirs)\(|\bopen\([^)]*['"][wax]\+?['"]/;

// Same normalization as the tool registry's argument remapping
// (_normalize_param_key): the registry runs a call written as `file_path` or
// `FilePath`, while the stored tool part keeps the name the model sent.
function normalizeArgName(name: string): string {
  return name.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
}

function toolArg(input: Record<string, unknown>, name: string): unknown {
  if (Object.prototype.hasOwnProperty.call(input, name)) return input[name];
  const wanted = normalizeArgName(name);
  const key = Object.keys(input).find((candidate) => normalizeArgName(candidate) === wanted);
  return key === undefined ? undefined : input[key];
}

function idsIn(text: unknown, pattern: RegExp): string[] {
  if (typeof text !== 'string') return [];
  return Array.from(text.matchAll(pattern), (match) => match[1].trim())
    .filter((id) => id && !/[*?]/.test(id));
}

function patchWriteTargets(patchText: unknown): string[] {
  if (typeof patchText !== 'string') return [];
  return Array.from(patchText.matchAll(PATCH_WRITE_HEADER), (match) => match[1])
    .flatMap((header) => header.split(' -> '));
}

function shellCommandIds(command: string): string[] {
  const quoted = Array.from(command.matchAll(QUOTED), (match) => match[1] ?? match[2] ?? '');
  const unquoted = command.replace(QUOTED, ' ');
  return [
    ...quoted.flatMap((segment) => idsIn(segment, WORKFLOW_DIR_IN_QUOTED)),
    ...idsIn(unquoted, WORKFLOW_DIR_IN_TEXT),
  ];
}

export function workflowLinksFromToolPart(part: unknown): WorkflowLink[] {
  if (!part || typeof part !== 'object') return [];
  const raw = part as { type?: unknown; tool?: unknown; state?: { input?: unknown } | null };
  if (raw.type !== 'tool') return [];
  const rawInput = raw.state?.input;
  if (!rawInput || typeof rawInput !== 'object' || Array.isArray(rawInput)) return [];
  const input = rawInput as Record<string, unknown>;
  let written: string[] = [];
  let shell: string[] = [];
  switch (raw.tool) {
    case 'write':
    case 'edit':
      written = idsIn(toolArg(input, 'filePath'), WORKFLOW_DIR_IN_PATH);
      break;
    case 'mkdir':
      written = idsIn(toolArg(input, 'path'), WORKFLOW_DIR_IN_PATH);
      break;
    case 'copy':
    case 'move':
      written = idsIn(toolArg(input, 'targetPath'), WORKFLOW_DIR_IN_PATH);
      break;
    case 'apply_patch':
      written = patchWriteTargets(toolArg(input, 'patchText'))
        .flatMap((path) => idsIn(path, WORKFLOW_DIR_IN_PATH));
      break;
    case 'bash': {
      const command = toolArg(input, 'command');
      if (typeof command === 'string' && SHELL_WRITE.test(command)) {
        shell = [...shellCommandIds(command), ...idsIn(toolArg(input, 'workdir'), WORKFLOW_DIR_IN_PATH)];
      }
      break;
    }
    default:
      break;
  }
  return [
    ...Array.from(new Set(written), (id) => ({ id, kind: 'write' as const })),
    ...Array.from(new Set(shell), (id) => ({ id, kind: 'shell' as const })),
  ];
}

export function workflowLinksFromMessages(messages: unknown): WorkflowLink[] {
  if (!Array.isArray(messages)) return [];
  return messages.flatMap((message) => {
    const parts = message && typeof message === 'object'
      ? (message as { parts?: unknown }).parts
      : undefined;
    return Array.isArray(parts) ? parts.flatMap((part) => workflowLinksFromToolPart(part)) : [];
  });
}

export function addWorkflowLinks(links: Map<string, WorkflowLinkKind>, found: WorkflowLink[]): void {
  for (const { id, kind } of found) {
    if (links.get(id) !== 'write') links.set(id, kind);
  }
}

/** The single new workflow this chat wrote, or null when none or several qualify. */
export function pickLinkedWorkflow<T extends { id: string }>(
  candidates: T[],
  links: ReadonlyMap<string, WorkflowLinkKind>,
): T | null {
  const written = candidates.filter((candidate) => links.get(candidate.id) === 'write');
  if (written.length > 0) return written.length === 1 ? written[0] : null;
  const fromShell = candidates.filter((candidate) => links.get(candidate.id) === 'shell');
  return fromShell.length === 1 ? fromShell[0] : null;
}
