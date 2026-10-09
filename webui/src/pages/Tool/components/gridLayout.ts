/**
 * Shared grid template for the three "service" tab tables — MCP, API, and
 * Local.  All three render the same column shape (icon · name+description ·
 * type · status · stats · actions) so they live behind one constant; if the
 * design changes, all three tabs (and the catalog rows inside the API tab)
 * stay aligned automatically.
 *
 * Every data column uses ``minmax(min, fr)`` so any leftover width on wide
 * screens is shared *proportionally* across the table rather than dumped
 * into a single gap between "name" and the right-hand columns — that was
 * the visual problem with the previous ``minmax(0, 1fr)`` name column,
 * which ate all the extra space and left short names visually orphaned
 * from the type badge.  Long names truncate via ``truncate`` on the inner
 * button.
 *
 *   1) 32px                       icon
 *   2) minmax(220px, 3fr)         name + description (dominant, truncates)
 *   3) minmax(56px,  0.4fr)       type badge (MCP / API / Local)
 *   4) minmax(80px,  0.5fr)       status badge (active / disabled / error)
 *   5) minmax(110px, 0.9fr)       stats (params count, latency, tools…)
 *   6) minmax(180px, 1.2fr)       actions cluster (manage + power + delete)
 *
 * Ratio total = 6.0fr.  On a 1920px viewport this gives roughly:
 *   name ~700 / type ~95 / status ~120 / stats ~215 / actions ~285,
 * which keeps the small badge columns tidy without orphaning the name.
 */
export const SERVICE_TAB_GRID_COLS =
  '32px minmax(220px, 3fr) minmax(56px, 0.4fr) minmax(80px, 0.5fr) minmax(110px, 0.9fr) minmax(180px, 1.2fr)';

/**
 * Responsive row classes shared by the same four row renderers.
 *
 * From ``sm`` up the row is the grid above (min-w keeps the six columns
 * intact; the wrapper scrolls horizontally when the viewport is narrower).
 * Below ``sm`` (phones, 375px) a 760px grid only shows the icon + name and
 * clips the type/status badges and the actions off the right edge, so the
 * row becomes a two-line flex layout instead:
 *
 *   line 1: icon · name+description (fills, truncates) · type · status
 *   line 2: stats (natural width) · actions (fills the rest, right-aligned)
 */
export const SERVICE_TAB_ROW_CLASS = 'flex flex-wrap sm:grid sm:min-w-[760px] items-center gap-3 px-4 py-3';
// basis-2/5 (not 0) so the line-1 hypothetical width always exceeds what is left once
// stats/actions try to join it; the name then grows into the remaining line-1 space.
export const SERVICE_TAB_NAME_CELL = 'min-w-0 flex-1 basis-2/5 sm:flex-none sm:basis-auto';
export const SERVICE_TAB_BADGE_CELL = 'shrink-0 text-center';
// Line 2: stats keep their natural width, the actions cluster takes the rest so
// wide buttons ("启用服务器" + icon buttons) stay on one line.
// basis-1/4 keeps stats off line 1 even when they are a single short counter,
// so every row has the same two-line shape.
export const SERVICE_TAB_STATS_CELL = 'flex items-center justify-start sm:justify-end shrink-0 basis-1/4 sm:basis-auto';
export const SERVICE_TAB_ACTIONS_CELL = 'flex items-center justify-end flex-1 basis-0 sm:flex-none sm:basis-auto';

/**
 * Card view (plugin view toggle): each row becomes a two-column card -- icon ·
 * name on the first line, the remaining cells spanning both columns below.
 */
export const SERVICE_TAB_CARD_GRID_COLS = '32px minmax(0, 1fr)';
export const SERVICE_TAB_CARD_CLASS =
  'grid items-center gap-3 px-4 py-3 rounded-control border border-line [&>:nth-child(5)]:col-span-2 [&>:nth-child(6)]:col-span-2';
