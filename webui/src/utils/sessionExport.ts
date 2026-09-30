import { sessionApi } from '@/api/session';
import { sanitizeSessionExportName } from '@/utils/sessionStatus';

/** Save a session (info and messages) as a JSON file through the browser. */
export async function exportSessionJson(sessionId: string, title: string): Promise<void> {
  const [info, messages] = await Promise.all([
    sessionApi.get(sessionId),
    sessionApi.getMessages(sessionId),
  ]);
  const blob = new Blob([JSON.stringify({ info, messages }, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `session-${sanitizeSessionExportName(title || sessionId)}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}
