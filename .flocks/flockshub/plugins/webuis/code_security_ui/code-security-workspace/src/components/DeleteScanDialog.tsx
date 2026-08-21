import { useEffect, useRef } from "react";

import { Icon } from "../icons";
import type { ScanSummary } from "../types";

export function DeleteScanDialog({
  scan,
  deleting,
  error,
  onClose,
  onConfirm,
}: {
  scan: ScanSummary | null;
  deleting: boolean;
  error: string;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!scan) return undefined;
    window.setTimeout(() => cancelRef.current?.focus(), 0);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !deleting) {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          "button:not([disabled])",
        ),
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [deleting, onClose, scan]);

  if (!scan) return null;

  return (
    <div className="cs-delete-dialog-layer" role="presentation">
      <button
        type="button"
        className="cs-delete-dialog-scrim"
        aria-label="取消删除审计"
        onClick={onClose}
        disabled={deleting}
      />
      <section
        ref={dialogRef}
        className="cs-delete-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-scan-title"
        aria-describedby="delete-scan-description"
      >
        <span className="cs-delete-dialog__icon">
          <Icon name="trash" />
        </span>
        <div>
          <p className="cs-eyebrow">不可恢复的操作</p>
          <h2 id="delete-scan-title">删除这条审计记录？</h2>
        </div>
        <p id="delete-scan-description">
          将永久删除 <strong>{scan.display_name}</strong>{" "}
          的审计记录、事件、快照和审计产物。
        </p>
        <code title={scan.scan_id}>{scan.scan_id}</code>
        {error && (
          <p className="cs-delete-dialog__error" role="alert">
            {error}
          </p>
        )}
        <footer>
          <button
            ref={cancelRef}
            type="button"
            className="cs-button cs-button--secondary"
            onClick={onClose}
            disabled={deleting}
          >
            取消
          </button>
          <button
            type="button"
            className="cs-button cs-button--danger"
            onClick={onConfirm}
            disabled={deleting}
          >
            <Icon name="trash" />
            {deleting ? "正在删除…" : "永久删除"}
          </button>
        </footer>
      </section>
    </div>
  );
}
