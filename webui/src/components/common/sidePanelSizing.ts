import { useEffect, useState } from 'react';

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

/** Below this width the side panel cannot share the row with the canvas. */
export const NARROW_VIEWPORT_MAX_WIDTH = 767;
const NARROW_VIEWPORT_QUERY = `(max-width: ${NARROW_VIEWPORT_MAX_WIDTH}px)`;

export function isNarrowViewport() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia(NARROW_VIEWPORT_QUERY).matches;
}

/**
 * True on phone-width viewports (< 768px). Pages with a canvas + side panel
 * layout switch the panel to a full-width overlay there instead of squeezing
 * both columns into the same row.
 */
export function useNarrowViewport() {
  const [narrow, setNarrow] = useState(isNarrowViewport);
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const media = window.matchMedia(NARROW_VIEWPORT_QUERY);
    const onChange = () => setNarrow(media.matches);
    onChange();
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);
  return narrow;
}
