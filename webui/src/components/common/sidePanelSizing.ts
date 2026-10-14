export const SIDE_PANEL_MIN_WIDTH = 240;
export const SIDE_PANEL_INITIAL_RATIO = 0.4;
export const SIDE_PANEL_MAX_RATIO = 0.7;
export const DESKTOP_SIDEBAR_WIDTH = 256;

function getAvailableWidth() {
  if (typeof window === 'undefined') return 1024;
  const sidebarWidth = window.innerWidth >= 1024 ? DESKTOP_SIDEBAR_WIDTH : 0;
  return Math.max(SIDE_PANEL_MIN_WIDTH, window.innerWidth - sidebarWidth);
}

export function getInitialSidePanelWidth() {
  return Math.max(SIDE_PANEL_MIN_WIDTH, Math.round(getAvailableWidth() * SIDE_PANEL_INITIAL_RATIO));
}

export function getMaxSidePanelWidth() {
  return Math.max(SIDE_PANEL_MIN_WIDTH, Math.round(getAvailableWidth() * SIDE_PANEL_MAX_RATIO));
}
