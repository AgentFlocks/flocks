type SelectionSnapshot = {
  ranges: Range[];
  activeElement: HTMLElement | null;
};

function captureSelection(): SelectionSnapshot {
  const selection = document.getSelection();
  const ranges: Range[] = [];
  if (selection) {
    for (let index = 0; index < selection.rangeCount; index += 1) {
      ranges.push(selection.getRangeAt(index).cloneRange());
    }
  }

  return {
    ranges,
    activeElement: document.activeElement instanceof HTMLElement ? document.activeElement : null,
  };
}

function restoreSelection(snapshot: SelectionSnapshot) {
  const selection = document.getSelection();
  if (selection) {
    selection.removeAllRanges();
    snapshot.ranges.forEach((range) => selection.addRange(range));
  }

  snapshot.activeElement?.focus?.();
}

export function fallbackCopyText(text: string): boolean {
  if (typeof document === 'undefined' || typeof document.execCommand !== 'function') {
    return false;
  }

  const textarea = document.createElement('textarea');
  const snapshot = captureSelection();

  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.setAttribute('aria-hidden', 'true');
  textarea.tabIndex = -1;
  textarea.style.position = 'fixed';
  textarea.style.top = '0';
  textarea.style.left = '-9999px';
  textarea.style.opacity = '0';
  textarea.style.pointerEvents = 'none';
  textarea.style.whiteSpace = 'pre';

  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);

  try {
    return document.execCommand('copy');
  } finally {
    document.body.removeChild(textarea);
    restoreSelection(snapshot);
  }
}

export async function copyText(text: string): Promise<void> {
  const normalizedText = String(text ?? '');
  let clipboardError: unknown;

  if (
    typeof window !== 'undefined' &&
    window.isSecureContext &&
    typeof navigator !== 'undefined' &&
    typeof navigator.clipboard?.writeText === 'function'
  ) {
    try {
      await navigator.clipboard.writeText(normalizedText);
      return;
    } catch (error) {
      clipboardError = error;
    }
  }

  if (fallbackCopyText(normalizedText)) {
    return;
  }

  throw clipboardError instanceof Error
    ? clipboardError
    : new Error('Clipboard copy failed');
}
