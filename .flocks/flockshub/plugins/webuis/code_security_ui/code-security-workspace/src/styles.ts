const styles = String.raw`
.code-security-workspace {
  --cs-surface: #f4f6f8;
  --cs-surface-elevated: #ffffff;
  --cs-surface-subtle: #eef2f6;
  --cs-surface-selected: #e8f1fb;
  --cs-border: #d5dbe3;
  --cs-border-strong: #aab5c2;
  --cs-text: #17212b;
  --cs-text-secondary: #506071;
  --cs-text-muted: #6b7887;
  --cs-primary: #1769aa;
  --cs-primary-hover: #12578e;
  --cs-on-primary: #ffffff;
  --cs-info: #1769aa;
  --cs-info-soft: #e6f1fa;
  --cs-success: #25724a;
  --cs-success-soft: #e6f4ec;
  --cs-warning: #8a5b09;
  --cs-warning-soft: #fff3d6;
  --cs-danger: #b4232c;
  --cs-danger-soft: #fbeaec;
  --cs-muted-soft: #edf0f3;
  --cs-severity-critical: #a9192c;
  --cs-severity-high: #c24b13;
  --cs-severity-medium: #9a690b;
  --cs-severity-low: #356a8a;
  --cs-focus: #287cc1;
  --cs-shadow-drawer: 0 18px 48px rgb(25 35 45 / 0.22);
  background: var(--cs-surface);
  color: var(--cs-text);
  display: grid;
  grid-template-columns: 280px minmax(520px, 1fr) 420px;
  height: 100%;
  min-height: calc(100dvh - 64px);
  overflow: hidden;
  position: relative;
  width: 100%;
}

.dark .code-security-workspace {
  --cs-surface: var(--flocks-dark-app, #252c35);
  --cs-surface-elevated: var(--flocks-dark-surface, #303842);
  --cs-surface-subtle: var(--flocks-dark-surface-soft, #3a434e);
  --cs-surface-selected: #334b63;
  --cs-border: var(--flocks-dark-border, #4a5563);
  --cs-border-strong: var(--flocks-dark-border-strong, #5a6573);
  --cs-text: var(--flocks-dark-text, #d7dee8);
  --cs-text-secondary: var(--flocks-dark-text-muted, #b8c2cc);
  --cs-text-muted: var(--flocks-dark-text-subtle, #9aa7b4);
  --cs-primary: var(--flocks-dark-accent, #539bf5);
  --cs-primary-hover: #78b4f7;
  --cs-on-primary: #132337;
  --cs-info: #73b7f5;
  --cs-info-soft: #253f56;
  --cs-success: #67c58f;
  --cs-success-soft: #264536;
  --cs-warning: #efbd62;
  --cs-warning-soft: #4c402a;
  --cs-danger: #ff8991;
  --cs-danger-soft: #522f34;
  --cs-muted-soft: #3b444e;
  --cs-severity-critical: #ff899c;
  --cs-severity-high: #ff9b68;
  --cs-severity-medium: #eec66f;
  --cs-severity-low: #83bfdf;
  --cs-focus: #78b9f5;
  --cs-shadow-drawer: var(--flocks-dark-shadow, 0 18px 48px rgb(10 14 18 / 0.45));
}

.code-security-workspace *,
.code-security-workspace *::before,
.code-security-workspace *::after {
  box-sizing: border-box;
}

.code-security-workspace button,
.code-security-workspace input,
.code-security-workspace select,
.code-security-workspace textarea {
  color: inherit;
  font: inherit;
}

.code-security-workspace button,
.code-security-workspace a,
.code-security-workspace select,
.code-security-workspace summary {
  touch-action: manipulation;
}

.code-security-workspace button,
.code-security-workspace summary,
.code-security-workspace select {
  cursor: pointer;
}

.code-security-workspace button:focus-visible,
.code-security-workspace a:focus-visible,
.code-security-workspace input:focus-visible,
.code-security-workspace select:focus-visible,
.code-security-workspace textarea:focus-visible,
.code-security-workspace summary:focus-visible,
.code-security-workspace [tabindex]:focus-visible {
  outline: 3px solid var(--cs-focus);
  outline-offset: 2px;
}

.cs-visually-hidden,
.cs-live-region {
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  height: 1px;
  overflow: hidden;
  position: absolute;
  white-space: nowrap;
  width: 1px;
}

.cs-eyebrow,
.cs-kicker {
  color: var(--cs-text-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  line-height: 16px;
  margin: 0;
  text-transform: uppercase;
}

.cs-tabular,
.code-security-workspace code,
.code-security-workspace time {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-variant-numeric: tabular-nums;
}

.code-security-workspace code {
  overflow-wrap: anywhere;
}

.cs-button {
  align-items: center;
  background: transparent;
  border: 1px solid var(--cs-border-strong);
  border-radius: 8px;
  color: var(--cs-text);
  display: inline-flex;
  font-size: 13px;
  font-weight: 650;
  gap: 8px;
  justify-content: center;
  min-height: 38px;
  padding: 8px 13px;
  text-decoration: none;
  transition: background-color 140ms ease, border-color 140ms ease, color 140ms ease, opacity 140ms ease;
}

.cs-button svg,
.cs-icon-button svg,
.cs-status svg,
.cs-search svg,
.cs-download-link svg {
  flex: 0 0 auto;
  height: 17px;
  width: 17px;
}

.cs-button:hover:not(:disabled) { background: var(--cs-surface-subtle); }
.cs-button:disabled { cursor: not-allowed; opacity: 0.48; }
.cs-button--primary { background: var(--cs-primary); border-color: var(--cs-primary); color: var(--cs-on-primary); }
.cs-button--primary:hover:not(:disabled) { background: var(--cs-primary-hover); border-color: var(--cs-primary-hover); }
.cs-button--secondary { background: var(--cs-surface-elevated); }
.cs-button--danger { background: var(--cs-surface-elevated); border-color: var(--cs-danger); color: var(--cs-danger); }
.cs-button--danger:hover:not(:disabled) { background: var(--cs-danger-soft); }
.cs-button--full { width: 100%; }

.cs-icon-button {
  align-items: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 8px;
  display: inline-flex;
  height: 36px;
  justify-content: center;
  padding: 0;
  width: 36px;
}

.cs-icon-button:hover { background: var(--cs-surface-subtle); border-color: var(--cs-border); }

.cs-scan-panel,
.cs-inspector {
  background: var(--cs-surface-elevated);
  min-width: 0;
  overflow: hidden;
}

.cs-scan-panel {
  border-right: 1px solid var(--cs-border);
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 12px;
}

.cs-panel-heading,
.cs-inspector__header,
.cs-section-heading,
.cs-subsection-heading,
.cs-current-phase__header {
  align-items: center;
  display: flex;
  justify-content: space-between;
}

.cs-panel-heading h2,
.cs-inspector__header h2,
.cs-section-heading h2,
.cs-scan-header h1,
.cs-current-phase h3,
.cs-subsection-heading h3 {
  color: var(--cs-text);
  margin: 0;
}

.cs-panel-heading h2,
.cs-inspector__header h2,
.cs-section-heading h2,
.cs-scan-header h1 {
  font-size: 18px;
  font-weight: 650;
  line-height: 24px;
}

.cs-count {
  align-items: center;
  background: var(--cs-muted-soft);
  border-radius: 999px;
  color: var(--cs-text-secondary);
  display: inline-flex;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  height: 24px;
  justify-content: center;
  min-width: 24px;
  padding: 0 7px;
}

.cs-search {
  align-items: center;
  background: var(--cs-surface);
  border: 1px solid var(--cs-border);
  border-radius: 8px;
  color: var(--cs-text-muted);
  display: flex;
  gap: 8px;
  min-height: 38px;
  padding: 0 10px;
}

.cs-search input {
  background: transparent;
  border: 0;
  color: var(--cs-text);
  min-width: 0;
  outline: 0;
  width: 100%;
}

.cs-search:focus-within { border-color: var(--cs-focus); box-shadow: 0 0 0 2px color-mix(in srgb, var(--cs-focus) 22%, transparent); }

.cs-filter-label {
  display: grid;
  gap: 5px;
}

.cs-filter-label > span {
  color: var(--cs-text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.code-security-workspace select,
.code-security-workspace input,
.code-security-workspace textarea {
  background: var(--cs-surface-elevated);
  border: 1px solid var(--cs-border);
  border-radius: 8px;
}

.cs-filter-label select,
.cs-events select {
  min-height: 36px;
  padding: 0 9px;
}

.cs-scan-list {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
  margin: 0 -4px;
  min-height: 0;
  overflow-y: auto;
  padding: 0 4px 12px;
  scrollbar-gutter: stable;
}

.cs-scan-list__items { display: grid; gap: 4px; }
.cs-scan-list__virtual { position: relative; }
.cs-scan-item.is-virtual { height: 102px; left: 0; position: absolute; top: 0; }
.cs-load-more-scans,
.cs-load-older {
  background: var(--cs-surface-elevated);
  border: 1px solid var(--cs-border);
  border-radius: 8px;
  color: var(--cs-primary);
  font-size: 12px;
  font-weight: 650;
  min-height: 38px;
  padding: 7px 12px;
}
.cs-load-more-scans { margin-top: 4px; width: 100%; }
.cs-load-more-scans:disabled,
.cs-load-older:disabled { color: var(--cs-text-muted); cursor: wait; }

.cs-scan-item {
  background: transparent;
  border: 1px solid transparent;
  border-radius: 8px;
  color: inherit;
  display: grid;
  gap: 7px;
  min-height: 102px;
  padding: 11px 10px 11px 13px;
  position: relative;
  text-align: left;
  transition: background-color 140ms ease, border-color 140ms ease;
  width: 100%;
}

.cs-scan-item::before {
  background: transparent;
  border-radius: 3px;
  bottom: 10px;
  content: '';
  left: 3px;
  position: absolute;
  top: 10px;
  width: 3px;
}

.cs-scan-item:hover { background: var(--cs-surface-subtle); }
.cs-scan-item.is-selected { background: var(--cs-surface-selected); border-color: var(--cs-border); }
.cs-scan-item.is-selected::before { background: var(--cs-primary); }
.cs-scan-item__top,
.cs-scan-item__bottom,
.cs-scan-item__meta { align-items: center; display: flex; gap: 7px; justify-content: space-between; min-width: 0; }
.cs-scan-item__top strong { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cs-scan-item__meta { color: var(--cs-text-secondary); font-size: 12px; justify-content: flex-start; }
.cs-scan-item__bottom { color: var(--cs-text-muted); font-size: 11px; }
.cs-scan-item__bottom code { max-width: 132px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.cs-status {
  align-items: center;
  border: 1px solid currentColor;
  border-radius: 999px;
  color: var(--cs-text-secondary);
  display: inline-flex;
  flex: 0 0 auto;
  font-size: 11px;
  font-weight: 650;
  gap: 4px;
  line-height: 18px;
  padding: 0 7px 0 5px;
  white-space: nowrap;
}

.cs-status svg { height: 13px; width: 13px; }
.cs-status--running, .cs-status--preparing, .cs-status--cancelling { color: var(--cs-info); }
.cs-status--completed { color: var(--cs-success); }
.cs-status--partial, .cs-status--interrupted { color: var(--cs-warning); }
.cs-status--failed { color: var(--cs-danger); }
.cs-status--cancelled, .cs-status--skipped, .cs-status--pending { color: var(--cs-text-muted); }

.cs-mode-tag,
.cs-value-tag,
.cs-artifact-state,
.cs-severity {
  background: var(--cs-muted-soft);
  border-radius: 999px;
  color: var(--cs-text-secondary);
  display: inline-flex;
  font-size: 11px;
  font-weight: 650;
  line-height: 20px;
  padding: 0 8px;
  white-space: nowrap;
}

.cs-value-tag--partial { background: var(--cs-warning-soft); color: var(--cs-warning); }

.cs-main-column {
  min-width: 0;
  overflow-y: auto;
  position: relative;
  scrollbar-gutter: stable;
}

.cs-scan-header {
  align-items: center;
  background: color-mix(in srgb, var(--cs-surface-elevated) 94%, transparent);
  border-bottom: 1px solid var(--cs-border);
  display: grid;
  gap: 16px;
  grid-template-columns: minmax(0, 1fr) auto;
  padding: 14px 20px;
  position: sticky;
  top: 0;
  z-index: 10;
}

.cs-scan-title { min-width: 0; }
.cs-scan-title__line, .cs-header-meta, .cs-header-actions { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; }
.cs-scan-title__line h1 { min-width: 0; overflow-wrap: anywhere; }
.cs-header-meta { color: var(--cs-text-secondary); font-size: 12px; margin-top: 5px; }
.cs-header-meta > * { align-items: center; display: inline-flex; min-height: 20px; }
.cs-header-actions { justify-content: flex-end; }
.cs-mobile-scan-select { display: none; }
.cs-inspector-trigger { display: none; }
.cs-header-new-audit { display: none; }
.cs-scan-drawer-trigger, .cs-scan-panel__close, .cs-scan-panel-scrim { display: none; }
.cs-panel-heading__actions { align-items: center; display: flex; gap: 6px; }

.cs-connection,
.cs-page-error {
  align-items: center;
  border-bottom: 1px solid var(--cs-border);
  display: flex;
  font-size: 13px;
  gap: 8px;
  justify-content: center;
  min-height: 40px;
  padding: 8px 16px;
}

.cs-connection { background: var(--cs-warning-soft); color: var(--cs-warning); }
.cs-connection svg { height: 16px; width: 16px; }
.cs-connection button, .cs-page-error button { background: transparent; border: 0; color: inherit; font-weight: 700; text-decoration: underline; }
.cs-page-error { background: var(--cs-danger-soft); color: var(--cs-danger); justify-content: space-between; }

.cs-failure-card {
  align-items: flex-start;
  background: var(--cs-danger-soft);
  border: 1px solid color-mix(in srgb, var(--cs-danger) 35%, var(--cs-border));
  border-radius: 10px;
  color: var(--cs-danger);
  display: flex;
  gap: 12px;
  margin: 16px 20px 0;
  padding: 14px;
}

.cs-failure-card > svg { height: 22px; width: 22px; }
.cs-failure-card h2 { font-size: 14px; margin: 0 0 4px; }
.cs-failure-card p { color: var(--cs-text); font-size: 13px; margin: 0 0 5px; }

.cs-execution { display: grid; gap: 20px; padding: 20px; }
.cs-section-heading { align-items: flex-end; }
.cs-section-heading > p { color: var(--cs-text-muted); font-size: 12px; margin: 0; }

.cs-phase-rail {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(4, minmax(140px, 1fr));
}

.cs-phase-step {
  background: var(--cs-surface-elevated);
  border: 1px solid var(--cs-border);
  border-radius: 8px;
  color: inherit;
  display: grid;
  gap: 7px;
  min-height: 92px;
  padding: 10px;
  text-align: left;
  transition: background-color 140ms ease, border-color 140ms ease;
}

.cs-phase-step:hover { background: var(--cs-surface-subtle); }
.cs-phase-step.is-selected { border-color: var(--cs-primary); box-shadow: inset 0 -3px var(--cs-primary); }
.cs-phase-step strong { font-size: 13px; }
.cs-phase-step > span:last-child { color: var(--cs-text-muted); font-size: 11px; }
.cs-phase-step .cs-status { justify-self: start; }

.cs-current-phase,
.cs-workers,
.cs-duration,
.cs-events,
.cs-overview > section,
.cs-candidate-card {
  background: var(--cs-surface-elevated);
  border: 1px solid var(--cs-border);
  border-radius: 10px;
}

.cs-current-phase { padding: 16px; }
.cs-current-phase h3, .cs-subsection-heading h3, .cs-overview h3 { font-size: 14px; font-weight: 650; line-height: 20px; }
.cs-metric-grid { display: grid; gap: 12px; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 16px 0 0; }
.cs-metric-grid > div { border-left: 2px solid var(--cs-border); min-width: 0; padding-left: 10px; }
.cs-metric-grid dt, .cs-definition-list dt, .cs-state-list dt { color: var(--cs-text-muted); font-size: 11px; }
.cs-metric-grid dd, .cs-definition-list dd, .cs-state-list dd { font-size: 13px; font-weight: 600; margin: 4px 0 0; overflow-wrap: anywhere; }

.cs-callout { border-radius: 8px; font-size: 13px; margin: 14px 0 0; padding: 10px 12px; }
.cs-callout--muted { background: var(--cs-muted-soft); color: var(--cs-text-secondary); }
.cs-callout--warning { background: var(--cs-warning-soft); color: var(--cs-warning); }

.cs-workers { overflow: hidden; }
.cs-worker-list { display: grid; gap: 10px; grid-template-columns: repeat(2, minmax(0, 1fr)); padding: 12px; }
.cs-worker-card { border: 1px solid var(--cs-border); border-radius: 8px; display: grid; gap: 12px; min-width: 0; padding: 12px; }
.cs-worker-card__heading { align-items: flex-start; display: flex; gap: 10px; justify-content: space-between; }
.cs-worker-card__heading > div { display: grid; gap: 3px; min-width: 0; }
.cs-worker-card__heading strong { font-size: 13px; }
.cs-worker-card__heading code { color: var(--cs-text-muted); font-size: 10px; overflow: hidden; text-overflow: ellipsis; }
.cs-worker-card dl { display: grid; gap: 8px; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; }
.cs-worker-card dl > div { min-width: 0; }
.cs-worker-card dt { color: var(--cs-text-muted); font-size: 10px; }
.cs-worker-card dd { font-size: 12px; font-weight: 600; margin: 3px 0 0; }
.cs-worker-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.cs-worker-tags code { background: var(--cs-surface-subtle); color: var(--cs-text-secondary); font-size: 10px; padding: 3px 6px; }
.cs-worker-paths { border-top: 1px solid var(--cs-border); padding-top: 8px; }
.cs-worker-paths summary { color: var(--cs-primary); cursor: pointer; font-size: 11px; font-weight: 650; }
.cs-worker-paths ul { display: grid; gap: 5px; list-style: none; margin: 8px 0 0; max-height: 160px; overflow-y: auto; padding: 0; }
.cs-worker-paths li { min-width: 0; }
.cs-worker-paths li code { display: block; font-size: 10px; overflow-wrap: anywhere; }
.cs-worker-paths p { color: var(--cs-text-muted); font-size: 10px; margin: 8px 0 0; }

.cs-duration,
.cs-events { overflow: hidden; }
.cs-subsection-heading { border-bottom: 1px solid var(--cs-border); min-height: 48px; padding: 10px 14px; }
.cs-subsection-heading > span, .cs-subsection-heading div > span { color: var(--cs-text-muted); font-size: 11px; }
.cs-table-wrap { overflow-x: auto; }
.cs-duration table { border-collapse: collapse; font-size: 12px; min-width: 680px; width: 100%; }
.cs-duration th, .cs-duration td { border-bottom: 1px solid var(--cs-border); height: 40px; padding: 6px 12px; text-align: left; }
.cs-duration thead th { background: var(--cs-surface-subtle); color: var(--cs-text-secondary); font-size: 11px; font-weight: 650; }
.cs-duration tbody th { font-weight: 600; }
.cs-duration tbody tr:last-child > * { border-bottom: 0; }

.cs-events { position: relative; }
.cs-events__heading { flex-wrap: wrap; gap: 8px; }
.cs-events__heading > div { display: grid; gap: 2px; }
.cs-load-older { align-self: flex-start; }
.cs-event-filters { display: flex; flex-wrap: wrap; gap: 6px; }
.cs-event-filters select { max-width: 150px; }
.cs-event-viewport { min-height: 220px; overflow-y: auto; position: relative; scrollbar-gutter: stable; }
.cs-event-virtual { position: relative; }
.cs-event-row {
  align-items: center;
  background: var(--cs-surface-elevated);
  border-bottom: 1px solid var(--cs-border);
  display: grid;
  gap: 10px;
  grid-template-columns: 66px 8px minmax(0, 1fr) auto;
  min-height: 72px;
  padding: 8px 12px;
  width: 100%;
}

.cs-event-virtual .cs-event-row { left: 0; position: absolute; top: 0; }
.cs-event-row > time { color: var(--cs-text-muted); font-size: 11px; }
.cs-event-row__marker { background: var(--cs-info); border-radius: 999px; height: 8px; width: 8px; }
.cs-event-row--warning .cs-event-row__marker { background: var(--cs-warning); }
.cs-event-row--error .cs-event-row__marker { background: var(--cs-danger); }
.cs-event-row strong { display: block; font-size: 12px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cs-event-row p { color: var(--cs-text-muted); font-size: 11px; margin: 4px 0 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cs-copy-button { background: transparent; border: 0; color: var(--cs-text-muted); font-size: 10px; min-height: 32px; min-width: 32px; }
.cs-new-events { background: var(--cs-primary); border: 0; border-radius: 999px; bottom: 14px; color: var(--cs-on-primary); font-size: 12px; font-weight: 650; left: 50%; min-height: 34px; padding: 0 14px; position: absolute; transform: translateX(-50%); }

.cs-inspector {
  border-left: 1px solid var(--cs-border);
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
}

.cs-inspector__header { border-bottom: 1px solid var(--cs-border); min-height: 66px; padding: 12px 16px; }
.cs-inspector__actions { align-items: center; display: flex; gap: 8px; }
.cs-inspector__refresh {
  background: transparent;
  border: 1px solid var(--cs-border);
  border-radius: 7px;
  color: var(--cs-primary);
  font-size: 12px;
  font-weight: 650;
  min-height: 36px;
  padding: 0 10px;
}
.cs-inspector__refresh:disabled { color: var(--cs-text-muted); cursor: wait; }
.cs-artifact-refresh-error { background: var(--cs-warning-soft); border: 1px solid var(--cs-warning); border-radius: 8px; color: var(--cs-text); font-size: 12px; line-height: 18px; margin: 0 0 12px; padding: 9px 10px; }
.cs-inspector__close { display: none; }
.cs-artifact-tabs { border-bottom: 1px solid var(--cs-border); display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); padding: 8px; }
.cs-artifact-tabs button { align-items: center; background: transparent; border: 0; border-radius: 7px; color: var(--cs-text-secondary); display: flex; font-size: 12px; justify-content: space-between; min-height: 38px; padding: 6px 8px; text-align: left; }
.cs-artifact-tabs button:hover { background: var(--cs-surface-subtle); }
.cs-artifact-tabs button.is-selected { background: var(--cs-surface-selected); color: var(--cs-text); font-weight: 650; }
.cs-artifact-state { font-size: 9px; line-height: 17px; padding: 0 5px; }
.cs-artifact-state--sealed { background: var(--cs-success-soft); color: var(--cs-success); }
.cs-artifact-state--invalid { background: var(--cs-danger-soft); color: var(--cs-danger); }
.cs-artifact-state--pending { color: var(--cs-text-muted); }
.cs-inspector__body { min-height: 0; overflow-y: auto; padding: 14px; scrollbar-gutter: stable; }

.cs-overview { display: grid; gap: 12px; }
.cs-overview > section { padding: 14px; }
.cs-overview h3 { margin: 0 0 12px; }
.cs-state-list, .cs-definition-list { display: grid; gap: 0; margin: 0; }
.cs-state-list > div, .cs-definition-list > div { align-items: center; border-bottom: 1px solid var(--cs-border); display: flex; justify-content: space-between; min-height: 38px; padding: 6px 0; }
.cs-state-list > div:last-child, .cs-definition-list > div:last-child { border-bottom: 0; }
.cs-helper { color: var(--cs-text-muted); font-size: 12px; line-height: 18px; margin: 12px 0 0; }
.cs-download-list { display: grid; gap: 6px; }
.cs-download-link { align-items: center; border: 1px solid var(--cs-border); border-radius: 8px; color: var(--cs-text); display: flex; gap: 9px; min-height: 48px; padding: 7px 10px; text-decoration: none; }
.cs-download-link:hover { background: var(--cs-surface-subtle); border-color: var(--cs-border-strong); }
.cs-download-link span { display: grid; gap: 2px; min-width: 0; }
.cs-download-link strong { font-size: 12px; overflow-wrap: anywhere; }
.cs-download-link small { color: var(--cs-text-muted); font-size: 10px; }

.cs-severity--critical { background: var(--cs-danger-soft); color: var(--cs-severity-critical); }
.cs-severity--high { background: color-mix(in srgb, var(--cs-severity-high) 15%, transparent); color: var(--cs-severity-high); }
.cs-severity--medium { background: var(--cs-warning-soft); color: var(--cs-severity-medium); }
.cs-severity--low { background: var(--cs-info-soft); color: var(--cs-severity-low); }
.cs-candidate-list { display: grid; gap: 10px; }
.cs-candidate-card { padding: 12px; }
.cs-candidate-card__top { align-items: center; display: flex; justify-content: space-between; }
.cs-candidate-card h3 { font-size: 13px; line-height: 19px; margin: 10px 0 5px; }
.cs-candidate-card p { color: var(--cs-text-secondary); font-size: 12px; line-height: 18px; margin: 0; }
.cs-candidate-card dl { display: grid; gap: 5px; margin: 10px 0 0; }
.cs-candidate-card dl > div { display: flex; font-size: 11px; justify-content: space-between; }
.cs-candidate-card dt { color: var(--cs-text-muted); }
.cs-candidate-card dd { margin: 0; }
.cs-evidence-links { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.cs-evidence-links button { background: var(--cs-info-soft); border: 1px solid color-mix(in srgb, var(--cs-info) 30%, var(--cs-border)); border-radius: 7px; color: var(--cs-info); font-size: 11px; min-height: 34px; padding: 6px 9px; text-align: left; }
.cs-evidence-viewer { background: var(--cs-surface-elevated); border: 1px solid var(--cs-primary); border-radius: 9px; padding: 12px; }
.cs-evidence-viewer .cs-subsection-heading > div { display: grid; gap: 3px; min-width: 0; }
.cs-evidence-viewer pre { background: var(--cs-surface-subtle); border: 1px solid var(--cs-border); border-radius: 7px; margin: 10px 0 0; max-height: 360px; overflow: auto; padding: 10px; }
.cs-evidence-viewer pre code { font-size: 11px; line-height: 18px; white-space: pre; }

.cs-structured-list { display: grid; gap: 8px; }
.cs-structured-object { background: var(--cs-surface-elevated); border: 1px solid var(--cs-border); border-radius: 8px; margin: 0; overflow: hidden; }
.cs-structured-object > div { border-bottom: 1px solid var(--cs-border); display: grid; gap: 6px; padding: 9px 10px; }
.cs-structured-object > div:last-child { border-bottom: 0; }
.cs-structured-object dt { color: var(--cs-text-muted); font-size: 10px; font-weight: 700; text-transform: uppercase; }
.cs-structured-object dd { font-size: 12px; line-height: 18px; margin: 0; overflow-wrap: anywhere; }
.cs-report-preview { background: var(--cs-surface-subtle); border: 1px solid var(--cs-border); border-radius: 8px; color: var(--cs-text); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 19px; margin: 0; overflow: auto; padding: 12px; white-space: pre-wrap; }

.cs-inline-empty { color: var(--cs-text-muted); font-size: 13px; line-height: 20px; margin: 0; padding: 24px; text-align: center; }
.cs-empty-state { align-items: center; display: flex; flex-direction: column; justify-content: center; margin: auto; max-width: 560px; min-height: calc(100dvh - 140px); padding: 40px 24px; text-align: center; }
.cs-empty-state__icon { align-items: center; background: var(--cs-info-soft); border-radius: 12px; color: var(--cs-info); display: inline-flex; height: 56px; justify-content: center; width: 56px; }
.cs-empty-state__icon svg { height: 30px; width: 30px; }
.cs-empty-state h1, .cs-empty-state h3 { font-size: 20px; margin: 18px 0 8px; }
.cs-empty-state p { color: var(--cs-text-secondary); font-size: 14px; line-height: 22px; margin: 0 0 18px; max-width: 480px; }
.cs-empty-state--compact { min-height: 280px; padding: 24px 14px; }
.cs-empty-state--compact > svg { color: var(--cs-text-muted); height: 30px; width: 30px; }
.cs-empty-state--compact h3 { font-size: 15px; }
.cs-error-state { background: var(--cs-warning-soft); border: 1px solid color-mix(in srgb, var(--cs-warning) 35%, var(--cs-border)); border-radius: 9px; color: var(--cs-warning); padding: 16px; }
.cs-error-state > svg { height: 24px; width: 24px; }
.cs-error-state h3 { font-size: 14px; margin: 8px 0 4px; }
.cs-error-state p { color: var(--cs-text-secondary); font-size: 12px; line-height: 18px; margin: 4px 0; }

.cs-skeleton-stack, .cs-workspace-skeleton > * { display: grid; gap: 10px; }
.cs-skeleton-stack span, .cs-workspace-skeleton span { animation: cs-pulse 1.2s ease-in-out infinite; background: var(--cs-surface-subtle); border-radius: 8px; display: block; height: 72px; }
.cs-skeleton-stack span:first-child { height: 130px; }
.cs-workspace-skeleton > * { background: var(--cs-surface-elevated); border-right: 1px solid var(--cs-border); padding: 16px; }
.cs-workspace-skeleton > section span:nth-child(2) { height: 180px; }
@keyframes cs-pulse { 50% { opacity: 0.55; } }

.cs-drawer-layer { inset: 0; position: fixed; z-index: 1000; }
.cs-drawer-scrim, .cs-inspector-scrim { background: rgb(12 18 24 / 0.48); border: 0; inset: 0; padding: 0; position: absolute; }
.cs-new-audit {
  background: var(--cs-surface-elevated);
  bottom: 0;
  box-shadow: var(--cs-shadow-drawer);
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  max-width: 620px;
  position: absolute;
  right: 0;
  top: 0;
  transform: translateX(0);
  width: min(620px, 92vw);
}

.cs-new-audit > header { align-items: center; border-bottom: 1px solid var(--cs-border); display: flex; justify-content: space-between; padding: 16px 20px; }
.cs-new-audit h2 { font-size: 18px; line-height: 24px; margin: 0; }
.cs-new-audit form { overflow-y: auto; padding: 0 20px 88px; }
.cs-new-audit fieldset { border: 0; border-bottom: 1px solid var(--cs-border); display: grid; gap: 14px; margin: 0; padding: 20px 0; }
.cs-new-audit legend { font-size: 14px; font-weight: 700; padding: 0; }
.cs-field { display: grid; gap: 6px; }
.cs-field > span { font-size: 12px; font-weight: 650; }
.cs-field b { color: var(--cs-danger); }
.cs-field input, .cs-field select, .cs-field textarea { min-height: 42px; padding: 9px 11px; resize: vertical; width: 100%; }
.cs-field textarea { min-height: 76px; }
.cs-field input[aria-invalid='true'], .cs-field select[aria-invalid='true'] { border-color: var(--cs-danger); }
.cs-field small { color: var(--cs-text-muted); font-size: 11px; line-height: 17px; }
.cs-field small[role='alert'] { color: var(--cs-danger); }
.cs-advanced { border: 1px solid var(--cs-border); border-radius: 8px; overflow: hidden; }
.cs-advanced summary { background: var(--cs-surface-subtle); font-size: 12px; font-weight: 650; padding: 10px 12px; }
.cs-advanced .cs-field { padding: 12px; }
.cs-toggle { align-items: center; display: flex; justify-content: space-between; }
.cs-toggle > span { display: grid; gap: 3px; }
.cs-toggle strong { font-size: 13px; }
.cs-toggle small { color: var(--cs-text-muted); font-size: 11px; }
.cs-toggle input { accent-color: var(--cs-primary); height: 22px; width: 40px; }
.cs-dynamic-confirm { align-items: flex-start; background: var(--cs-warning-soft); border: 1px solid color-mix(in srgb, var(--cs-warning) 35%, var(--cs-border)); border-radius: 9px; color: var(--cs-warning); display: flex; gap: 10px; padding: 12px; }
.cs-dynamic-confirm > svg { flex: 0 0 auto; height: 22px; width: 22px; }
.cs-dynamic-confirm h3 { font-size: 13px; line-height: 19px; margin: 0; }
.cs-dynamic-confirm p { color: var(--cs-text-secondary); font-size: 11px; line-height: 17px; margin: 6px 0; }
.cs-dynamic-confirm label { align-items: flex-start; color: var(--cs-text); display: flex; font-size: 12px; gap: 8px; line-height: 18px; margin-top: 10px; }
.cs-dynamic-confirm label input { accent-color: var(--cs-primary); flex: 0 0 auto; height: 18px; margin-top: 1px; width: 18px; }
.cs-dynamic-confirm small { color: var(--cs-danger); display: block; font-size: 11px; margin-top: 6px; }
.cs-form-errors { background: var(--cs-danger-soft); border: 1px solid color-mix(in srgb, var(--cs-danger) 35%, var(--cs-border)); border-radius: 8px; color: var(--cs-danger); margin-top: 16px; padding: 12px; }
.cs-form-errors h3 { font-size: 13px; margin: 0 0 5px; }
.cs-form-errors p, .cs-form-errors ul { font-size: 12px; margin: 5px 0; }
.cs-form-errors a { color: inherit; }
.cs-new-audit form > footer { align-items: center; background: var(--cs-surface-elevated); border-top: 1px solid var(--cs-border); bottom: 0; display: flex; gap: 8px; justify-content: flex-end; left: 0; padding: 12px 20px; position: absolute; right: 0; }

.cs-inspector-scrim { display: none; position: fixed; z-index: 35; }

@media (max-width: 1439px) {
  .code-security-workspace { grid-template-columns: 240px minmax(0, 1fr); }
  .cs-inspector {
    bottom: 0;
    box-shadow: var(--cs-shadow-drawer);
    max-width: 520px;
    position: fixed;
    right: 0;
    top: 0;
    transform: translateX(102%);
    transition: transform 250ms ease;
    width: min(520px, 92vw);
    z-index: 40;
  }
  .cs-inspector.is-open { transform: translateX(0); }
  .cs-inspector__close, .cs-inspector-trigger { display: inline-flex; }
  .cs-inspector-scrim { display: block; }
}

@media (max-width: 1023px) {
  .code-security-workspace { display: block; overflow-y: auto; }
  .cs-scan-panel {
    bottom: 0;
    box-shadow: var(--cs-shadow-drawer);
    display: flex;
    left: 0;
    max-width: 340px;
    position: fixed;
    top: 0;
    transform: translateX(-102%);
    transition: transform 250ms ease;
    width: min(340px, 88vw);
    z-index: 50;
  }
  .cs-scan-panel.is-open { transform: translateX(0); }
  .cs-scan-panel__close, .cs-scan-drawer-trigger { display: inline-flex; }
  .cs-scan-panel-scrim { background: rgb(12 18 24 / 0.48); border: 0; display: block; inset: 0; padding: 0; position: fixed; z-index: 45; }
  .cs-main-column { min-height: 100%; overflow: visible; }
  .cs-scan-header { grid-template-columns: auto minmax(0, 1fr) auto; }
  .cs-phase-rail { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 767px) {
  .code-security-workspace { font-size: 16px; min-height: 100dvh; }
  .cs-scan-panel, .cs-scan-panel-scrim, .cs-scan-drawer-trigger { display: none; }
  .cs-button { min-height: 44px; }
  .cs-icon-button { height: 44px; width: 44px; }
  .cs-scan-header { align-items: stretch; gap: 12px; grid-template-columns: 1fr; padding: 12px 16px; position: relative; }
  .cs-mobile-scan-select { display: grid; gap: 4px; grid-column: 1 / -1; }
  .cs-mobile-scan-select label { color: var(--cs-text-muted); font-size: 11px; font-weight: 650; }
  .cs-mobile-scan-select select { min-height: 44px; padding: 0 10px; width: 100%; }
  .cs-mobile-load-more { width: 100%; }
  .cs-scan-title__line h1 { font-size: 18px; }
  .cs-header-meta { font-size: 12px; }
  .cs-header-meta code:last-child { display: none; }
  .cs-header-actions { justify-content: stretch; }
  .cs-header-actions > * { flex: 1; }
  .cs-header-new-audit { display: inline-flex; }
  .cs-execution { gap: 16px; padding: 16px; }
  .cs-section-heading { align-items: flex-start; display: grid; gap: 4px; }
  .cs-section-heading > p { display: none; }
  .cs-phase-rail { grid-template-columns: 1fr; }
  .cs-phase-step { grid-template-columns: auto minmax(0, 1fr) auto; min-height: 60px; }
  .cs-phase-step > span:last-child { align-self: center; text-align: right; }
  .cs-metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .cs-worker-list { grid-template-columns: 1fr; }
  .cs-duration table { display: block; min-width: 0; }
  .cs-duration thead { display: none; }
  .cs-duration tbody { display: grid; gap: 8px; padding: 10px; }
  .cs-duration tr { border: 1px solid var(--cs-border); border-radius: 8px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); overflow: hidden; }
  .cs-duration th, .cs-duration td { border-bottom: 1px solid var(--cs-border); height: auto; min-height: 40px; padding: 8px; }
  .cs-duration tbody th { grid-column: 1 / -1; }
  .cs-event-row { grid-template-columns: 58px 8px minmax(0, 1fr); }
  .cs-event-filters { width: 100%; }
  .cs-event-filters label { flex: 1 1 110px; }
  .cs-event-filters select { max-width: none; width: 100%; }
  .cs-copy-button { display: none; }
  .cs-inspector { max-width: none; width: 100vw; }
  .cs-artifact-tabs { display: flex; overflow-x: auto; }
  .cs-artifact-tabs button { flex: 0 0 auto; gap: 8px; min-height: 44px; }
  .cs-new-audit { max-width: none; width: 100vw; }
  .cs-new-audit form { padding-left: 16px; padding-right: 16px; }
  .cs-field input, .cs-field select, .cs-field textarea { font-size: 16px; min-height: 44px; }
  .cs-toggle { min-height: 52px; }
  .cs-drawer-scrim { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .code-security-workspace *,
  .code-security-workspace *::before,
  .code-security-workspace *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}
`;

export default styles;
