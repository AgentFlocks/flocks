import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { Bot, MessageSquare, X } from "lucide-react";
import WorkbenchSidePanel from "./WorkbenchSidePanel";
import {
  getInitialSidePanelWidth,
  getMaxSidePanelWidth,
  SIDE_PANEL_MIN_WIDTH,
} from "./sidePanelSizing";

export default function AuditWorkbenchPanel({
  open,
  title,
  closeLabel,
  resizeLabel,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  closeLabel: string;
  resizeLabel: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const { t } = useTranslation("common");
  const [width, setWidth] = useState(getInitialSidePanelWidth);
  const closeRef = useRef<HTMLButtonElement>(null);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const clamp = (value: number) =>
    Math.min(getMaxSidePanelWidth(), Math.max(SIDE_PANEL_MIN_WIDTH, value));
  useEffect(() => {
    const resize = () => setWidth((value) => clamp(value));
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  useEffect(() => {
    if (!open) return;
    const trigger = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => {
      if (trigger?.isConnected) trigger.focus();
    };
  }, [open]);
  return createPortal(
    <>
      {open && (
        <div
          data-testid="audit-workbench-backdrop"
          className="fixed inset-0 bg-black/40 z-40"
          onClick={onClose}
        />
      )}
      <WorkbenchSidePanel
        open={open}
        width={width}
        className="shadow-2xl"
        style={{
          position: "fixed",
          right: 0,
          top: 0,
          bottom: 0,
          zIndex: 50,
          maxWidth: "100vw",
          visibility: open ? "visible" : "hidden",
        }}
      >
        <div
          role="dialog"
          aria-modal="true"
          aria-label={title}
          className="flex flex-1 min-h-0 flex-col"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              onClose();
            }
            if (event.key === "Tab") {
              const controls = Array.from(
                event.currentTarget.querySelectorAll<HTMLElement>(
                  'button:not(:disabled), textarea:not(:disabled), input:not(:disabled), a[href], [tabindex="0"]',
                ),
              ).filter((element) => element.getClientRects().length > 0);
              const first = controls[0];
              const last = controls[controls.length - 1];
              if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
              } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
              }
            }
          }}
        >
          <div
            role="separator"
            aria-label={resizeLabel}
            aria-orientation="vertical"
            tabIndex={open ? 0 : -1}
            aria-valuenow={width}
            aria-valuemin={SIDE_PANEL_MIN_WIDTH}
            aria-valuemax={getMaxSidePanelWidth()}
            className="absolute inset-y-0 left-0 w-1 cursor-col-resize hover:bg-red-400 focus:bg-red-400 touch-none"
            onPointerDown={(event) => {
              drag.current = { x: event.clientX, width };
              event.currentTarget.setPointerCapture(event.pointerId);
            }}
            onPointerMove={(event) => {
              if (drag.current)
                setWidth(
                  clamp(drag.current.width + drag.current.x - event.clientX),
                );
            }}
            onPointerUp={() => {
              drag.current = null;
            }}
            onPointerCancel={() => {
              drag.current = null;
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                event.preventDefault();
                setWidth((value) =>
                  clamp(value + (event.key === "ArrowLeft" ? 24 : -24)),
                );
              }
            }}
          />
          <header className="shrink-0 border-b border-gray-200">
            <div className="flex items-center gap-3 px-6 py-4">
              <Bot size={20} className="shrink-0 text-gray-500" />
              <h2 className="flex-1 min-w-0 text-lg font-semibold text-gray-900">
                {title}
              </h2>
              <button
                ref={closeRef}
                type="button"
                aria-label={closeLabel}
                onClick={onClose}
                className="shrink-0 p-1 rounded hover:bg-gray-100 transition-colors"
              >
                <X size={20} className="text-gray-400" />
              </button>
            </div>
            <div className="px-6">
              <div className="flex items-center justify-center gap-1.5 border-b-2 border-red-600 px-3 py-2.5 text-sm font-medium text-red-600">
                <MessageSquare size={14} />
                {t("entity.tabAIEdit")}
              </div>
            </div>
          </header>
          <div className="flex flex-1 min-h-0 flex-col overflow-hidden">
            {children}
          </div>
        </div>
      </WorkbenchSidePanel>
    </>,
    document.body,
  );
}
