import { type CSSProperties, type ReactNode } from "react";

/** Shared shell for workbench side panels. Keep children mounted when closed. */
export default function WorkbenchSidePanel({
  open,
  width,
  children,
  style,
  className = "",
}: {
  open: boolean;
  width: number;
  children: ReactNode;
  style?: CSSProperties;
  className?: string;
}) {
  return (
    <div
      aria-hidden={!open}
      inert={!open || undefined}
      className={`relative z-10 flex min-w-0 flex-col bg-white border-l border-gray-200 flex-shrink-0 overflow-hidden transition-[width] duration-300 ease-in-out motion-reduce:transition-none ${className}`}
      style={{ ...style, width: open ? width : 0 }}
    >
      {children}
    </div>
  );
}
