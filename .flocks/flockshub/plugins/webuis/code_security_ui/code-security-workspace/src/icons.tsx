import type { ReactElement, SVGProps } from "react";

export type IconName =
  | "shield"
  | "plus"
  | "search"
  | "clock"
  | "check"
  | "warning"
  | "error"
  | "skip"
  | "activity"
  | "files"
  | "flask"
  | "report"
  | "close"
  | "download"
  | "panel";

const paths: Record<IconName, ReactElement> = {
  shield: (
    <>
      <path d="M12 3 5 6v5c0 4.4 2.8 8.1 7 10 4.2-1.9 7-5.6 7-10V6l-7-3Z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  plus: (
    <>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-4-4" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  check: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m8 12 2.5 2.5L16 9" />
    </>
  ),
  warning: (
    <>
      <path d="M12 3 2.8 20h18.4L12 3Z" />
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
    </>
  ),
  error: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m9 9 6 6" />
      <path d="m15 9-6 6" />
    </>
  ),
  skip: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m6 18 12-12" />
    </>
  ),
  activity: <path d="M3 12h4l2-6 4 12 2-6h6" />,
  files: (
    <>
      <path d="M7 3h7l4 4v12H7Z" />
      <path d="M14 3v5h5" />
      <path d="M4 7v14h11" />
    </>
  ),
  flask: (
    <>
      <path d="M9 3h6" />
      <path d="M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3" />
      <path d="M8 15h8" />
    </>
  ),
  report: (
    <>
      <path d="M6 3h9l4 4v14H6Z" />
      <path d="M14 3v5h5" />
      <path d="M9 13h7" />
      <path d="M9 17h5" />
    </>
  ),
  close: (
    <>
      <path d="m6 6 12 12" />
      <path d="m18 6-12 12" />
    </>
  ),
  download: (
    <>
      <path d="M12 3v12" />
      <path d="m7 10 5 5 5-5" />
      <path d="M5 21h14" />
    </>
  ),
  panel: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M15 4v16" />
    </>
  ),
};

export function Icon({
  name,
  ...props
}: { name: IconName } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {paths[name]}
    </svg>
  );
}
