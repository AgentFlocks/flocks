import { createContext } from 'react';

/**
 * Whether the KeepAlive pane a component sits in is the one on screen. Hidden
 * panes stay mounted, so anything with a document-wide side effect (a page's
 * temporary theme, for one) reads this instead of relying on unmount.
 * Defaults to true for content rendered outside KeepAlivePanes.
 */
export const PaneActiveContext = createContext(true);
