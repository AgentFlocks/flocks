import { type CSSProperties, type ReactNode } from "react";

/** Shared shell for workbench side panels. Keep children mounted when closed. */
export default function WorkbenchSidePanel({
  open,
  width,
  children,
  style,
  className = "",
  overlay = false,
}: {
  open: boolean;
  width: number;
  children: ReactNode;
  style?: CSSProperties;
  className?: string;
  /** Cover the parent (phone widths) instead of taking a column beside it. */
  overlay?: boolean;
}) {
  return (
    <div
      aria-hidden={!open}
      inert={!open || undefined}
      className={overlay
        ? `absolute inset-0 z-40 flex min-w-0 flex-col bg-white overflow-hidden ${open ? "" : "hidden"} ${className}`
        : `relative z-10 flex min-w-0 flex-col bg-white border-l border-gray-200 flex-shrink-0 overflow-hidden transition-[width] duration-300 ease-in-out motion-reduce:transition-none ${className}`}
      style={overlay ? style : { ...style, width: open ? width : 0 }}
    >
      {children}
    </div>
  );
}
