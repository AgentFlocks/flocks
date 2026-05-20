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
