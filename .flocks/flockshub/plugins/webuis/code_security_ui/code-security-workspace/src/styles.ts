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
  min-height: 102px;
  position: relative;
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
.cs-scan-item__select {
  background: transparent;
  border: 0;
  display: grid;
  gap: 7px;
  min-height: 100px;
  padding: 11px 10px 11px 13px;
  text-align: left;
  width: 100%;
}
.cs-scan-item__top { padding-right: 100px; }
.cs-scan-item__top,
.cs-scan-item__bottom,
.cs-scan-item__meta { align-items: center; display: flex; gap: 7px; justify-content: space-between; min-width: 0; }
.cs-scan-item__top strong { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cs-scan-item__meta { color: var(--cs-text-secondary); font-size: 12px; justify-content: flex-start; }
.cs-scan-item__bottom { color: var(--cs-text-muted); font-size: 11px; }
.cs-scan-item__bottom code { max-width: 132px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cs-scan-item__actions { align-items: center; display: flex; gap: 3px; position: absolute; right: 7px; top: 9px; }
.cs-scan-item__delete {
  align-items: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 7px;
  color: var(--cs-text-muted);
  display: inline-flex;
  height: 28px;
  justify-content: center;
  padding: 0;
  width: 28px;
}
.cs-scan-item__delete svg { height: 15px; width: 15px; }
.cs-scan-item__delete:hover:not(:disabled) { background: var(--cs-danger-soft); border-color: color-mix(in srgb, var(--cs-danger) 35%, var(--cs-border)); color: var(--cs-danger); }
.cs-scan-item__delete:disabled { cursor: not-allowed; opacity: 0.34; }

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
.cs-status--partial, .cs-status--interrupted, .cs-status--not_runnable { color: var(--cs-warning); }
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
.cs-final-findings { align-items: center; background: var(--cs-surface-elevated); border: 1px solid var(--cs-border); border-radius: 8px; display: flex; gap: 16px; justify-content: space-between; min-width: 172px; padding: 8px 10px; }
.cs-final-findings > div { display: grid; gap: 2px; }
.cs-final-findings > div > span { color: var(--cs-text); font-size: 11px; font-weight: 650; }
.cs-final-findings > div > small { color: var(--cs-text-muted); font-size: 10px; overflow-wrap: anywhere; }
.cs-final-findings > strong { color: var(--cs-text); font-size: 24px; font-weight: 700; line-height: 26px; white-space: nowrap; }
.cs-final-findings > strong > small { color: var(--cs-text-secondary); font-size: 10px; font-weight: 600; margin-left: 3px; }

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
.cs-phase-step strong { font-size: 13px; overflow-wrap: anywhere; }
.cs-phase-step > span:last-child { color: var(--cs-text-muted); font-size: 11px; }
.cs-phase-step .cs-status { justify-self: start; }

.cs-current-phase,
.cs-phase-summary,
.cs-workers,
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

.cs-phase-summary { overflow: hidden; }
.cs-phase-summary__heading > div { display: grid; gap: 2px; }
.cs-phase-summary__heading h3 { align-items: center; display: flex; gap: 7px; }
.cs-phase-summary__heading h3 svg { color: var(--cs-success); height: 17px; width: 17px; }
.cs-artifact-bundle--invalid .cs-phase-summary__heading h3 svg { color: var(--cs-danger); }
.cs-artifact-bundle--pending .cs-phase-summary__heading h3 svg { color: var(--cs-info); }
.cs-phase-summary__grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; padding: 14px; }
.cs-phase-summary__grid > div { border-right: 1px solid var(--cs-border); min-width: 0; padding: 0 14px; }
.cs-phase-summary__grid > div:first-child { padding-left: 0; }
.cs-phase-summary__grid > div:last-child { border-right: 0; padding-right: 0; }
.cs-phase-summary__grid dt { color: var(--cs-text-muted); font-size: 11px; }
.cs-phase-summary__grid dd { color: var(--cs-text); font-size: 14px; font-weight: 650; margin: 5px 0 0; min-width: 0; }
.cs-phase-summary__grid code { display: block; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cs-phase-summary__note { background: var(--cs-surface-subtle); border-top: 1px solid var(--cs-border); color: var(--cs-text-secondary); font-size: 12px; line-height: 18px; margin: 0; padding: 10px 14px; }
.cs-phase-summary__note--danger { background: var(--cs-danger-soft); color: var(--cs-danger); }
.cs-adjudication-summary .cs-phase-summary__heading h3 svg { color: var(--cs-primary); }
.cs-adjudication-summary__execution { padding: 12px 14px 0; }
.cs-adjudication-summary__content { display: grid; gap: 14px; padding: 14px; }
.cs-adjudication-group { border: 1px solid var(--cs-border); border-radius: 8px; min-width: 0; overflow: hidden; }
.cs-adjudication-group__header { align-items: center; background: var(--cs-surface-subtle); border-bottom: 1px solid var(--cs-border); display: flex; gap: 12px; justify-content: space-between; padding: 10px 12px; }
.cs-adjudication-group__header > div { align-items: center; display: flex; gap: 9px; min-width: 0; }
.cs-adjudication-group__icon { align-items: center; border-radius: 7px; display: inline-flex; flex: 0 0 auto; height: 30px; justify-content: center; width: 30px; }
.cs-adjudication-group__icon svg { height: 17px; width: 17px; }
.cs-adjudication-group--accepted .cs-adjudication-group__icon { background: var(--cs-success-soft); color: var(--cs-success); }
.cs-adjudication-group--rejected .cs-adjudication-group__icon { background: var(--cs-danger-soft); color: var(--cs-danger); }
.cs-adjudication-group__header h4 { font-size: 12px; line-height: 18px; margin: 0; }
.cs-adjudication-group__header p { color: var(--cs-text-muted); font-size: 10px; line-height: 16px; margin: 1px 0 0; }
.cs-adjudication-group__header > strong { align-items: baseline; border-radius: 999px; display: inline-flex; flex: 0 0 auto; font-size: 13px; gap: 2px; min-width: 38px; padding: 3px 8px; justify-content: center; }
.cs-adjudication-group__header > strong small { font-size: 9px; font-weight: 600; }
.cs-adjudication-group--accepted .cs-adjudication-group__header > strong { background: var(--cs-success-soft); color: var(--cs-success); }
.cs-adjudication-group--rejected .cs-adjudication-group__header > strong { background: var(--cs-danger-soft); color: var(--cs-danger); }
.cs-adjudication-group > p { color: var(--cs-text-muted); font-size: 12px; line-height: 18px; margin: 0; padding: 14px 11px; }
.cs-adjudication-group ul { background: var(--cs-surface-subtle); display: grid; gap: 8px; list-style: none; margin: 0; padding: 10px; }
.cs-adjudication-group--accepted ul, .cs-adjudication-group--rejected ul { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.cs-adjudication-group li { background: var(--cs-surface-elevated); border: 1px solid var(--cs-border); border-left-width: 3px; border-radius: 7px; display: grid; min-width: 0; overflow: hidden; }
.cs-adjudication-group--accepted li { border-left-color: var(--cs-success); }
.cs-adjudication-group li.is-expanded { grid-column: 1 / -1; }
.cs-adjudication-group--rejected li { border-left-color: var(--cs-danger); }
.cs-adjudication-candidate__heading { align-items: start; display: grid; gap: 9px; grid-template-columns: auto minmax(0, 1fr) auto; min-width: 0; }
.cs-adjudication-candidate__heading > div, .cs-adjudication-candidate__copy { display: grid; gap: 3px; min-width: 0; }
.cs-adjudication-candidate__heading strong { font-size: 12px; line-height: 18px; overflow-wrap: anywhere; }
.cs-adjudication-candidate__heading code { color: var(--cs-text-muted); font-size: 10px; line-height: 16px; overflow-wrap: anywhere; white-space: normal; }
.cs-adjudication-candidate__toggle { background: transparent; border: 0; color: inherit; cursor: pointer; font: inherit; min-height: 68px; padding: 11px; text-align: left; transition: background 120ms ease; width: 100%; }
.cs-adjudication-candidate__toggle:hover { background: var(--cs-surface-subtle); }
.cs-adjudication-candidate__toggle:focus-visible { outline: 2px solid var(--cs-focus); outline-offset: -3px; }
.cs-adjudication-candidate__index { align-items: center; background: var(--cs-muted-soft); border-radius: 999px; color: var(--cs-text-secondary); display: inline-flex; font-size: 10px; font-weight: 650; height: 22px; justify-content: center; width: 22px; }
.cs-adjudication-candidate__actions { align-items: end; display: grid; gap: 6px; justify-items: end; }
.cs-adjudication-candidate__status { border-radius: 999px; font-size: 10px; font-weight: 650; line-height: 20px; padding: 0 7px; white-space: nowrap; }
.cs-adjudication-group--accepted .cs-adjudication-candidate__status { background: var(--cs-success-soft); color: var(--cs-success); }
.cs-adjudication-group--rejected .cs-adjudication-candidate__status { background: var(--cs-danger-soft); color: var(--cs-danger); }
.cs-adjudication-candidate__disclosure { align-items: center; color: var(--cs-primary); display: inline-flex; font-size: 10px; font-weight: 650; gap: 3px; white-space: nowrap; }
.cs-adjudication-group--rejected .cs-adjudication-candidate__disclosure { color: var(--cs-danger); }
.cs-adjudication-candidate__disclosure svg { height: 14px; transition: transform 120ms ease; width: 14px; }
.cs-adjudication-group li.is-expanded .cs-adjudication-candidate__disclosure svg { transform: rotate(180deg); }
.cs-adjudication-evidence { background: var(--cs-surface-subtle); border-top: 1px solid var(--cs-border); display: grid; gap: 12px; padding: 12px; }
.cs-adjudication-evidence h5 { align-items: center; display: flex; font-size: 11px; gap: 7px; line-height: 18px; margin: 0; }
.cs-adjudication-evidence h5 > span { background: var(--cs-muted-soft); border-radius: 999px; color: var(--cs-text-muted); font-size: 9px; line-height: 18px; padding: 0 6px; }
.cs-adjudication-evidence__decision { border-radius: 7px; padding: 10px 11px; }
.cs-adjudication-evidence__decision--rejected { background: var(--cs-danger-soft); }
.cs-adjudication-evidence__decision--rejected h5 { color: var(--cs-danger); }
.cs-adjudication-evidence__rationale { background: var(--cs-info-soft); border-radius: 7px; padding: 10px 11px; }
.cs-adjudication-evidence__rationale h5 { color: var(--cs-info); }
.cs-adjudication-evidence__decision p, .cs-adjudication-evidence__rationale p { color: var(--cs-text-secondary); font-size: 12px; line-height: 19px; margin: 5px 0 0; overflow-wrap: anywhere; white-space: pre-wrap; }
.cs-adjudication-evidence__code { display: grid; gap: 8px; min-width: 0; }
.cs-adjudication-evidence__items { display: grid; gap: 8px; }
.cs-adjudication-evidence__items article { background: var(--cs-surface-elevated); border: 1px solid var(--cs-border); border-radius: 7px; min-width: 0; overflow: hidden; }
.cs-adjudication-evidence__items header { align-items: center; border-bottom: 1px solid var(--cs-border); display: flex; gap: 10px; justify-content: space-between; min-height: 34px; padding: 6px 9px; }
.cs-adjudication-evidence__items header strong { flex: 0 0 auto; font-size: 10px; }
.cs-adjudication-evidence__items header code { color: var(--cs-text-secondary); font-size: 10px; overflow-wrap: anywhere; text-align: right; }
.cs-adjudication-evidence__items pre { background: var(--cs-code-bg, var(--cs-surface-subtle)); margin: 0; overflow-x: auto; padding: 10px; }
.cs-adjudication-evidence__items pre:focus-visible { outline: 2px solid var(--cs-focus); outline-offset: -2px; }
.cs-adjudication-evidence__items pre code { font-size: 11px; line-height: 18px; white-space: pre; }
.cs-adjudication-evidence__items article > small { border-top: 1px solid var(--cs-border); color: var(--cs-text-muted); display: block; font-size: 9px; padding: 5px 9px; }
.cs-adjudication-evidence__message { color: var(--cs-text-muted); font-size: 11px; line-height: 18px; margin: 0; padding: 6px 0; }
.cs-adjudication-evidence__error { align-items: center; background: var(--cs-danger-soft); border-radius: 7px; display: flex; gap: 10px; justify-content: space-between; padding: 8px 10px; }
.cs-adjudication-evidence__error p { color: var(--cs-danger); font-size: 11px; line-height: 18px; margin: 0; }
.cs-adjudication-evidence__error button { background: var(--cs-surface-elevated); border: 1px solid var(--cs-border); border-radius: 6px; color: var(--cs-text); flex: 0 0 auto; font-size: 10px; min-height: 30px; padding: 0 9px; }
.cs-adjudication-evidence__error button:focus-visible { outline: 2px solid var(--cs-focus); outline-offset: 2px; }
.cs-adjudication-summary__rescan { display: grid; gap: 14px; padding: 14px; }
.cs-adjudication-summary__rescan > div { min-width: 0; }
.cs-adjudication-summary__rescan > div:first-child { background: var(--cs-warning-soft); border-radius: 8px; display: grid; gap: 4px; padding: 11px 12px; }
.cs-adjudication-summary__rescan > div:first-child strong { color: var(--cs-warning); font-size: 13px; }
.cs-adjudication-summary__rescan h4 { font-size: 12px; margin: 0 0 6px; }
.cs-adjudication-summary__rescan p, .cs-adjudication-summary__rescan ol { color: var(--cs-text-secondary); font-size: 12px; line-height: 19px; margin: 0; overflow-wrap: anywhere; }
.cs-adjudication-summary__rescan ol { display: grid; gap: 5px; padding-left: 20px; }
.cs-adjudication-summary__paths { display: flex; flex-wrap: wrap; gap: 6px; list-style: none; margin: 0; padding: 0; }
.cs-adjudication-summary__paths code { background: var(--cs-surface-subtle); border: 1px solid var(--cs-border); border-radius: 5px; display: block; font-size: 10px; max-width: 100%; overflow-wrap: anywhere; padding: 4px 7px; }

.cs-workers { overflow: hidden; }
.cs-worker-list { align-items: start; display: grid; gap: 10px; grid-template-columns: repeat(2, minmax(0, 1fr)); padding: 12px; }
.cs-worker-card { align-content: start; border: 1px solid var(--cs-border); border-radius: 8px; display: grid; gap: 12px; min-width: 0; padding: 12px; }
.cs-worker-card__heading { align-items: flex-start; display: flex; gap: 10px; justify-content: space-between; }
.cs-worker-card__identity { display: grid; flex: 1 1 auto; gap: 3px; min-width: 0; }
.cs-worker-card__title { align-items: baseline; display: flex; flex-wrap: wrap; gap: 3px 8px; min-width: 0; }
.cs-worker-card__heading strong { font-size: 13px; }
.cs-worker-card__identity > code { color: var(--cs-text-muted); font-size: 10px; overflow: hidden; text-overflow: ellipsis; }
.cs-worker-model { align-items: baseline; border-left: 1px solid var(--cs-border-strong); display: flex; flex: 1 1 180px; flex-wrap: wrap; gap: 2px 7px; min-width: 0; padding-left: 8px; }
.cs-worker-model code { color: var(--cs-text-secondary); font-size: 10px; line-height: 16px; overflow-wrap: anywhere; white-space: normal; }
.cs-worker-model small { color: var(--cs-text-muted); font-size: 9px; line-height: 15px; white-space: nowrap; }
.cs-execution-model { align-items: flex-end; background: var(--cs-surface-subtle); border-radius: 7px; display: flex; flex-wrap: wrap; gap: 6px 12px; justify-content: space-between; min-width: 0; padding: 8px 10px; }
.cs-execution-model > div { display: grid; flex: 1 1 180px; gap: 2px; min-width: 0; }
.cs-execution-model span { color: var(--cs-text-muted); font-size: 10px; line-height: 16px; }
.cs-execution-model code { color: var(--cs-text-secondary); font-size: 11px; line-height: 17px; overflow-wrap: anywhere; white-space: normal; }
.cs-worker-candidate { background: var(--cs-surface-subtle); border-radius: 7px; display: grid; gap: 5px; padding: 9px 10px; }
.cs-worker-candidate__heading { align-items: flex-start; display: flex; gap: 8px; justify-content: space-between; }
.cs-worker-candidate h4 { font-size: 12px; line-height: 18px; margin: 0; overflow-wrap: anywhere; }
.cs-worker-candidate > code { color: var(--cs-text-muted); font-size: 10px; overflow: hidden; text-overflow: ellipsis; }
.cs-worker-candidate__meta { display: flex; flex: 0 0 auto; flex-wrap: wrap; gap: 5px; justify-content: flex-end; }
.cs-worker-verdict { border-radius: 999px; display: inline-flex; font-size: 10px; font-weight: 650; line-height: 20px; padding: 0 7px; white-space: nowrap; }
.cs-worker-verdict--confirmed { background: var(--cs-success-soft); color: var(--cs-success); }
.cs-worker-verdict--rejected { background: var(--cs-danger-soft); color: var(--cs-danger); }
.cs-worker-verdict--insufficient_evidence { background: var(--cs-warning-soft); color: var(--cs-warning); }
.cs-worker-verdict--pending { background: var(--cs-muted-soft); color: var(--cs-text-muted); }
.cs-worker-card dl { display: grid; gap: 8px; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; }
.cs-worker-card dl > div { min-width: 0; }
.cs-worker-card dt { color: var(--cs-text-muted); font-size: 10px; }
.cs-worker-card dd { font-size: 12px; font-weight: 600; margin: 3px 0 0; }
.cs-worker-activity { align-items: center; display: flex; flex-wrap: wrap; gap: 6px; }
.cs-worker-activity span { border: 1px solid var(--cs-border); border-radius: 999px; color: var(--cs-text-muted); flex: 0 0 auto; font-size: 10px; line-height: 20px; padding: 0 7px; }
.cs-worker-activity strong { color: var(--cs-text-secondary); font-variant-numeric: tabular-nums; }
.cs-worker-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.cs-worker-tags code { background: var(--cs-surface-subtle); color: var(--cs-text-secondary); font-size: 10px; padding: 3px 6px; }
.cs-worker-details { border-top: 1px solid var(--cs-border); padding-top: 8px; }
.cs-worker-details summary { color: var(--cs-primary); cursor: pointer; font-size: 11px; font-weight: 650; line-height: 32px; min-height: 32px; }
.cs-worker-details summary:focus-visible { outline: 2px solid var(--cs-focus); outline-offset: 3px; }
.cs-worker-rationale[open] summary { margin-bottom: 8px; }
.cs-worker-rationale__content { background: var(--cs-surface-subtle); border-radius: 6px; color: var(--cs-text-secondary); font-size: 12px; line-height: 19px; max-height: min(240px, 35vh); overflow-wrap: anywhere; overflow-y: auto; overscroll-behavior: contain; padding: 10px; scrollbar-gutter: stable; white-space: pre-wrap; }
.cs-worker-rationale__content:focus-visible { outline: 2px solid var(--cs-focus); outline-offset: -2px; }
.cs-worker-rationale__content > p { margin: 0; }
.cs-worker-details > small { color: var(--cs-text-muted); display: block; font-size: 10px; margin-top: 5px; }
.cs-worker-attempts ol { display: grid; gap: 6px; list-style: none; margin: 8px 0 0; padding: 0; }
.cs-worker-attempts li { background: var(--cs-surface-subtle); border-radius: 6px; display: grid; gap: 3px; min-width: 0; padding: 7px 9px; }
.cs-worker-attempts li > div { align-items: baseline; display: flex; gap: 7px; justify-content: space-between; }
.cs-worker-attempts li strong { font-size: 11px; }
.cs-worker-attempts li small { color: var(--cs-text-muted); font-size: 9px; }
.cs-worker-attempts li code { color: var(--cs-text-secondary); font-size: 10px; overflow-wrap: anywhere; white-space: normal; }
.cs-worker-paths ul { display: grid; gap: 5px; list-style: none; margin: 8px 0 0; max-height: 160px; overflow-y: auto; padding: 0; }
.cs-worker-paths li { min-width: 0; }
.cs-worker-paths li code { display: block; font-size: 10px; overflow-wrap: anywhere; }
.cs-worker-paths > p { color: var(--cs-text-muted); font-size: 10px; }

.cs-events { overflow: hidden; }
.cs-subsection-heading { border-bottom: 1px solid var(--cs-border); min-height: 48px; padding: 10px 14px; }
.cs-subsection-heading > span, .cs-subsection-heading div > span { color: var(--cs-text-muted); font-size: 11px; }

.cs-events { position: relative; }
.cs-events__heading { align-items: flex-end; flex-wrap: wrap; gap: 12px; }
.cs-events__heading > div:first-child { display: grid; gap: 2px; }
.cs-load-older { align-self: flex-start; }
.cs-event-filters { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; margin-left: auto; }
.cs-event-filter { flex: 0 1 auto; max-width: 100%; }
.cs-event-filter--phase { width: 128px; }
.cs-event-filter--worker { width: 148px; }
.cs-event-filter--level { width: 112px; }
.cs-event-filters select { max-width: 100%; width: 100%; }
.cs-event-viewport { min-height: 220px; overflow-y: auto; position: relative; scrollbar-gutter: stable; }
.cs-event-virtual { position: relative; }
.cs-event-row {
  align-items: center;
  background: var(--cs-surface-elevated);
  border-bottom: 1px solid var(--cs-border);
  display: grid;
  gap: 10px;
  grid-template-columns: 66px 8px minmax(0, 1fr) auto 32px;
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
.cs-event-row__group { color: var(--cs-text-secondary); font-variant-numeric: tabular-nums; margin-left: 8px; }
.cs-event-row__metric { background: var(--cs-surface-subtle); border-radius: 999px; color: var(--cs-text-secondary); font-size: 10px; font-weight: 650; padding: 4px 8px; white-space: nowrap; }
.cs-copy-button { align-items: center; background: transparent; border: 0; border-radius: 6px; color: var(--cs-text-muted); display: inline-flex; justify-content: center; min-height: 32px; min-width: 32px; }
.cs-copy-button:hover { background: var(--cs-surface-subtle); color: var(--cs-text); }
.cs-copy-button svg { height: 14px; width: 14px; }
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
.cs-artifact-tabs button { align-items: flex-start; background: transparent; border: 0; border-radius: 7px; color: var(--cs-text-secondary); display: flex; font-size: 12px; gap: 8px; justify-content: space-between; min-height: 38px; padding: 6px 8px; text-align: left; }
.cs-artifact-tabs button > span:first-child { min-width: 0; overflow-wrap: anywhere; }
.cs-artifact-tabs button:hover { background: var(--cs-surface-subtle); }
.cs-artifact-tabs button.is-selected { background: var(--cs-surface-selected); color: var(--cs-text); font-weight: 650; }
.cs-artifact-state { flex: 0 0 auto; font-size: 9px; line-height: 17px; padding: 0 5px; }
.cs-artifact-state--sealed { background: var(--cs-success-soft); color: var(--cs-success); }
.cs-artifact-state--invalid { background: var(--cs-danger-soft); color: var(--cs-danger); }
.cs-artifact-state--pending, .cs-artifact-state--disabled { background: var(--cs-muted-soft); color: var(--cs-text-muted); }
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
.cs-report-markdown { background: var(--cs-surface-elevated); border: 1px solid var(--cs-border); border-radius: 8px; color: var(--cs-text); font-size: 13px; line-height: 1.7; overflow-wrap: anywhere; padding: 16px; }
.cs-report-markdown > :first-child { margin-top: 0; }
.cs-report-markdown > :last-child { margin-bottom: 0; }
.cs-report-markdown h1, .cs-report-markdown h2, .cs-report-markdown h3, .cs-report-markdown h4, .cs-report-markdown h5, .cs-report-markdown h6 { color: var(--cs-text); font-weight: 700; line-height: 1.35; }
.cs-report-markdown h1 { font-size: 19px; margin: 0 0 16px; }
.cs-report-markdown h2 { border-bottom: 1px solid var(--cs-border); font-size: 16px; margin: 24px 0 10px; padding-bottom: 6px; }
.cs-report-markdown h3 { font-size: 14px; margin: 20px 0 8px; }
.cs-report-markdown h4, .cs-report-markdown h5, .cs-report-markdown h6 { font-size: 13px; margin: 16px 0 6px; }
.cs-report-markdown p { margin: 8px 0; }
.cs-report-markdown ul, .cs-report-markdown ol { margin: 8px 0; padding-left: 22px; }
.cs-report-markdown ul { list-style: disc; }
.cs-report-markdown ol { list-style: decimal; }
.cs-report-markdown li { margin: 4px 0; }
.cs-report-markdown li > p { margin: 2px 0; }
.cs-report-markdown .contains-task-list { list-style: none; padding-left: 0; }
.cs-report-markdown a { color: var(--cs-primary); text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 2px; }
.cs-report-markdown a:hover { color: var(--cs-primary-hover); }
.cs-report-markdown code { background: var(--cs-surface-subtle); border: 1px solid var(--cs-border); border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em; padding: 1px 4px; }
.cs-report-markdown pre { background: var(--cs-surface-subtle); border: 1px solid var(--cs-border); border-radius: 7px; margin: 12px 0; max-width: 100%; overflow: auto; padding: 10px 12px; }
.cs-report-markdown pre code { background: transparent; border: 0; border-radius: 0; display: block; font-size: 11px; line-height: 18px; padding: 0; white-space: pre; }
.cs-report-markdown blockquote { border-left: 3px solid var(--cs-border-strong); color: var(--cs-text-secondary); margin: 12px 0; padding: 2px 0 2px 12px; }
.cs-report-markdown table { border-collapse: collapse; display: block; margin: 12px 0; max-width: 100%; overflow-x: auto; width: max-content; }
.cs-report-markdown th, .cs-report-markdown td { border: 1px solid var(--cs-border); min-width: 84px; padding: 6px 8px; text-align: left; vertical-align: top; }
.cs-report-markdown th { background: var(--cs-surface-subtle); font-weight: 650; }
.cs-report-markdown hr { border: 0; border-top: 1px solid var(--cs-border); margin: 20px 0; }
.cs-report-markdown input[type='checkbox'] { accent-color: var(--cs-primary); margin: 0 6px 0 0; }
.cs-report-markdown img { height: auto; max-width: 100%; }
.cs-report-fallback { background: var(--cs-surface-subtle); border: 1px solid var(--cs-border); border-radius: 8px; color: var(--cs-text); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 19px; margin: 0; overflow: auto; padding: 12px; white-space: pre-wrap; }

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
.cs-detail-skeleton { align-content: start; min-height: 100%; padding: 20px; }
.cs-workspace-skeleton > * { background: var(--cs-surface-elevated); border-right: 1px solid var(--cs-border); padding: 16px; }
.cs-workspace-skeleton > section span:nth-child(2) { height: 180px; }
@keyframes cs-pulse { 50% { opacity: 0.55; } }

.cs-drawer-layer { inset: 0; position: fixed; z-index: 1000; }
.cs-drawer-scrim, .cs-inspector-scrim { background: rgb(12 18 24 / 0.48); border: 0; inset: 0; padding: 0; position: absolute; }
.cs-delete-dialog-layer { align-items: center; display: flex; inset: 0; justify-content: center; padding: 20px; position: fixed; z-index: 1100; }
.cs-delete-dialog-scrim { background: rgb(12 18 24 / 0.5); border: 0; inset: 0; padding: 0; position: absolute; }
.cs-delete-dialog {
  background: var(--cs-surface-elevated);
  border: 1px solid var(--cs-border);
  border-radius: 10px;
  box-shadow: var(--cs-shadow-drawer);
  display: grid;
  gap: 14px;
  max-width: 460px;
  padding: 22px;
  position: relative;
  width: 100%;
}
.cs-delete-dialog__icon { align-items: center; background: var(--cs-danger-soft); border-radius: 9px; color: var(--cs-danger); display: inline-flex; height: 40px; justify-content: center; width: 40px; }
.cs-delete-dialog__icon svg { height: 20px; width: 20px; }
.cs-delete-dialog h2 { font-size: 18px; line-height: 24px; margin: 2px 0 0; }
.cs-delete-dialog > p { color: var(--cs-text-secondary); font-size: 13px; line-height: 20px; margin: 0; }
.cs-delete-dialog > code { background: var(--cs-surface-subtle); border: 1px solid var(--cs-border); border-radius: 7px; color: var(--cs-text-secondary); font-size: 11px; padding: 8px 10px; }
.cs-delete-dialog .cs-delete-dialog__error { background: var(--cs-danger-soft); border-radius: 7px; color: var(--cs-danger); padding: 9px 10px; }
.cs-delete-dialog footer { display: flex; gap: 8px; justify-content: flex-end; margin-top: 2px; }
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
.cs-field > span, .cs-field > label { font-size: 12px; font-weight: 650; }
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
  .cs-section-heading { align-items: stretch; display: grid; gap: 12px; }
  .cs-final-findings { min-width: 0; }
  .cs-phase-rail { grid-template-columns: 1fr; }
  .cs-phase-step { grid-template-columns: auto minmax(0, 1fr) auto; min-height: 60px; }
  .cs-phase-step > span:last-child { align-self: center; text-align: right; }
  .cs-metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .cs-phase-summary__grid { grid-template-columns: repeat(2, minmax(0, 1fr)); row-gap: 14px; }
  .cs-phase-summary__grid > div:nth-child(2) { border-right: 0; padding-right: 0; }
  .cs-phase-summary__grid > div:nth-child(3) { padding-left: 0; }
  .cs-adjudication-group--accepted ul, .cs-adjudication-group--rejected ul { grid-template-columns: 1fr; }
  .cs-worker-list { grid-template-columns: 1fr; }
  .cs-event-row { grid-template-columns: 58px 8px minmax(0, 1fr) auto; }
  .cs-event-filters { justify-content: flex-start; margin-left: 0; width: 100%; }
  .cs-event-filters label { flex: 1 1 110px; min-width: 0; width: auto; }
  .cs-event-filters select { max-width: none; width: 100%; }
  .cs-copy-button { display: none; }
  .cs-inspector { max-width: none; width: 100vw; }
  .cs-artifact-tabs { display: flex; overflow-x: auto; }
  .cs-artifact-tabs button { flex: 0 0 auto; gap: 8px; min-height: 44px; }
  .cs-report-markdown { font-size: 16px; }
  .cs-report-markdown h1 { font-size: 22px; }
  .cs-report-markdown h2 { font-size: 19px; }
  .cs-report-markdown h3 { font-size: 17px; }
  .cs-report-markdown pre code { font-size: 13px; line-height: 20px; }
  .cs-new-audit { max-width: none; width: 100vw; }
  .cs-new-audit form { padding-left: 16px; padding-right: 16px; }
  .cs-field input, .cs-field select, .cs-field textarea { font-size: 16px; min-height: 44px; }
  .cs-toggle { min-height: 52px; }
  .cs-drawer-scrim { display: none; }
  .cs-delete-dialog-layer { padding: 16px; }
  .cs-delete-dialog { padding: 18px; }
  .cs-delete-dialog footer { display: grid; grid-template-columns: 1fr 1fr; }
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
/* Workbench presentation: retain existing audit behavior and evidence viewers. */
.code-security-workspace.cs-workbench {
  --cs-border: #e8ebef;
  --cs-surface: #ffffff;
  --cs-surface-subtle: #f6f7f9;
  --cs-surface-selected: #e5edff;
  --cs-overview-tint: #f0f5ff;
  --cs-chrome: #f0f3f9;
  --cs-text: #202630;
  --cs-text-secondary: #626c7a;
  --cs-primary: #245fbe;
  --cs-primary-hover: #1c4c98;
  grid-template-columns: 224px minmax(0, 1fr);
}
.dark .code-security-workspace.cs-workbench {
  --cs-border: #424954;
  --cs-surface: #252c35;
  --cs-surface-subtle: #303842;
  --cs-surface-selected: #344967;
  --cs-overview-tint: #28384d;
  --cs-chrome: #29323f;
  --cs-text: #edf0f5;
  --cs-text-secondary: #abb4c2;
  --cs-primary: #8ebcff;
  --cs-primary-hover: #b5d2ff;
}
.cs-workbench [hidden] {
  display: none !important;
}
.cs-workbench button:focus-visible,
.cs-workbench summary:focus-visible {
  outline: 2px solid var(--cs-focus);
  outline-offset: 3px;
}
.cs-workbench .cs-button--secondary,
.cs-workbench .cs-icon-button {
  background: transparent;
  border-color: transparent;
  box-shadow: none;
}
.cs-workbench .cs-main-column {
  min-width: 0;
  background: var(--cs-surface);
}
.cs-workbench .cs-home-link {
  background: none;
  border: none;
  color: var(--cs-text-secondary);
  text-align: left;
  padding: 12px 22px;
  cursor: pointer;
}
.cs-workbench .cs-scan-header {
  background: var(--cs-surface);
  padding: 16px 24px;
  border: 0;
}
.cs-workbench .cs-header-meta {
  font-size: 12px;
}
.cs-workbench .cs-audit-metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  padding: 24px 32px 28px;
  gap: 24px;
  border-bottom: 1px solid var(--cs-border);
}
.cs-audit-metrics > div {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.cs-audit-metrics span,
.cs-audit-metrics small {
  font-size: 12px;
  color: var(--cs-text-secondary);
}
.cs-audit-metrics strong {
  font-size: 30px;
  line-height: 1.2;
  letter-spacing: -0.8px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.cs-audit-metrics .cs-metric-text {
  font-size: 15px;
  overflow-wrap: anywhere;
}
.cs-workbench .cs-execution {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 248px;
  gap: 0;
  padding: 0;
  align-items: start;
}
.cs-workbench .cs-execution > .cs-section-heading {
  grid-column: 1 / -1;
  grid-row: 1;
  padding: 12px 28px 0;
}
.cs-workbench .cs-execution > .cs-section-heading .cs-final-findings {
  display: none;
}
.cs-workbench .cs-execution > .cs-section-heading h2 {
  font-size: 12px;
  color: var(--cs-text-secondary);
  font-weight: 500;
}
.cs-workbench .cs-phase-rail {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 28px 16px;
  border-left: 1px solid var(--cs-border);
  overflow: visible;
  background: transparent;
}
.cs-workbench .cs-phase-step {
  width: 100%;
  min-width: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  box-shadow: none;
  text-align: left;
  padding: 12px;
}
.cs-workbench .cs-phase-step.is-selected {
  background: var(--cs-surface-selected);
  box-shadow: none;
}
.cs-workbench .cs-stage-session {
  grid-column: 1;
  grid-row: 2;
  padding: 30px clamp(16px, 3vw, 48px);
  min-width: 0;
}
.cs-session-identity {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 26px;
}
.cs-session-identity > span {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #f04449;
  color: white;
  font-weight: 600;
}
.cs-session-identity small {
  color: var(--cs-text-secondary);
}
.cs-session-identity button {
  margin-left: auto;
  border: 0;
  background: none;
  color: var(--cs-primary);
  cursor: pointer;
}
.cs-workbench .cs-current-phase {
  background: transparent;
  border: 0;
  padding: 0;
  box-shadow: none;
}
.cs-workbench .cs-metric-grid {
  gap: 12px;
  margin: 18px 0;
}
.cs-workbench .cs-metric-grid > div {
  border: 0;
  padding: 0;
  background: none;
}
.cs-workbench .cs-stage-details {
  margin: 20px 0;
}
.cs-stage-details > summary {
  cursor: pointer;
  color: var(--cs-text-secondary);
  padding: 10px 0;
}
.cs-workbench .cs-event-stream,
.cs-workbench .cs-worker-card,
.cs-workbench .cs-worker-list {
  border: 0;
  background: transparent;
  box-shadow: none;
}
.cs-workbench .cs-qa-composer {
  margin: 12px 284px 24px 32px;
  background: var(--cs-surface-subtle);
  border-radius: 20px;
  padding: 16px 20px;
}
.cs-qa-composer textarea {
  width: 100%;
  border: 0;
  background: transparent;
  resize: none;
  min-height: 64px;
  color: var(--cs-text-secondary);
}
.cs-qa-composer span {
  font-size: 11px;
  color: var(--cs-text-secondary);
}
.cs-workbench .cs-inspector {
  position: fixed;
  inset: 0 0 0 auto;
  width: min(520px, 100vw);
  z-index: 40;
  transform: translateX(100%);
  visibility: hidden;
  box-shadow: var(--cs-shadow-drawer);
}
.cs-workbench .cs-inspector.is-open {
  transform: translateX(0);
  visibility: visible;
}
.cs-workbench .cs-inspector__close,
.cs-workbench .cs-inspector-trigger {
  display: inline-flex;
}
.cs-workbench .cs-inspector-scrim {
  display: block;
}
.cs-workbench.cs-home-view {
  display: block;
  overflow-y: auto;
  background: var(--cs-surface);
}
.cs-project-home {
  margin: 48px auto;
  padding: 0;
  max-width: 1240px;
  width: calc(100% - 80px);
  background: transparent;
  border-radius: 0;
}
.cs-project-home > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding-bottom: 36px;
}
.cs-project-home h1 {
  font-size: 26px;
  letter-spacing: -0.6px;
  font-weight: 600;
  margin: 0;
}
.cs-project-home h2 {
  font-size: 15px;
  margin: 0;
}
.cs-project-home p {
  font-size: 14px;
  margin: 10px 0 0;
  color: var(--cs-text-secondary);
}
.cs-project-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 0;
  border-top: 1px solid var(--cs-border);
}
.cs-project-toolbar > div {
  display: flex;
  gap: 12px;
}
.cs-project-toolbar input,
.cs-project-toolbar select {
  border: 0;
  background: var(--cs-surface-subtle);
  color: var(--cs-text);
  border-radius: 7px;
  padding: 10px 12px;
}
.cs-project-table-scroll {
  overflow-x: auto;
}
.cs-project-table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
  font-size: 13px;
  white-space: nowrap;
}
.cs-project-table th {
  font-weight: 500;
  color: var(--cs-text-secondary);
  background: transparent;
  font-size: 12px;
}
.cs-project-table th,
.cs-project-table td {
  padding: 20px 16px;
  border-bottom: 1px solid var(--cs-border);
}
.cs-project-table td:first-child {
  max-width: 320px;
}
.cs-project-table td small {
  display: block;
  color: var(--cs-text-secondary);
  font-size: 12px;
  margin-top: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cs-project-home table button,
.cs-project-home footer button {
  border: 0;
  background: none;
  color: var(--cs-primary);
  cursor: pointer;
  padding: 6px;
}
.cs-project-home footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  color: var(--cs-text-secondary);
  font-size: 12px;
  margin-top: 20px;
}
.cs-project-home footer > div {
  display: flex;
  align-items: center;
  gap: 12px;
}
.cs-project-home button:disabled {
  opacity: 0.4;
  cursor: default;
}
.cs-home-empty {
  text-align: center;
  padding: 64px 20px;
}
.cs-history-records {
  margin-top: 24px;
  color: var(--cs-text-secondary);
  font-size: 13px;
}
.cs-history-records button {
  display: flex;
  gap: 20px;
  background: none;
  border: 0;
  padding: 12px;
  color: var(--cs-text);
  cursor: pointer;
}
.cs-workbench .cs-drawer-layer {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.cs-workbench .cs-new-audit {
  display: block;
  max-width: 760px;
  position: relative;
  inset: auto;
  width: min(760px, 100%);
  height: auto;
  max-height: calc(100dvh - 48px);
  border-radius: 16px;
  border: 0;
  overflow-y: auto;
  box-shadow: var(--cs-shadow-drawer);
}
.cs-workbench .cs-new-audit > header {
  border: 0;
  padding: 24px 28px 12px;
}
.cs-workbench .cs-new-audit form {
  overflow: visible;
}
.cs-workbench .cs-new-audit fieldset {
  border: 0;
  background: none;
}
.cs-workbench .cs-new-audit input:not([type="checkbox"]),
.cs-workbench .cs-new-audit textarea,
.cs-workbench .cs-new-audit select {
  background: var(--cs-surface-subtle);
  border-color: transparent;
}
.cs-workbench .cs-advanced {
  border: 0;
  background: none;
}
.cs-mode-switch {
  display: flex;
  justify-content: center;
  gap: 32px;
  padding: 10px 0 20px;
}
.cs-mode-switch button {
  padding: 10px 0;
  background: none;
  border: 0;
  border-bottom: 2px solid transparent;
  color: var(--cs-text-secondary);
  cursor: pointer;
}
.cs-mode-switch button[aria-selected="true"] {
  color: var(--cs-text);
  border-bottom-color: var(--cs-text);
}
.cs-auto-config {
  padding: 28px 40px 40px;
  text-align: center;
}
.cs-auto-config p {
  font-size: 13px;
  color: var(--cs-text-secondary);
  line-height: 1.7;
}
.cs-auto-config textarea {
  width: 100%;
  min-height: 140px;
  border: 0;
  border-radius: 20px;
  background: var(--cs-surface-subtle);
  padding: 18px;
  color: var(--cs-text);
  margin: 24px 0 6px;
}
@media (max-width: 1100px) {
  .code-security-workspace.cs-workbench {
    grid-template-columns: 220px minmax(0, 1fr);
  }
  .cs-workbench .cs-execution {
    grid-template-columns: minmax(0, 1fr) 210px;
  }
  .cs-workbench .cs-qa-composer {
    margin-right: 230px;
  }
  .cs-workbench .cs-audit-metrics {
    gap: 12px;
    padding: 16px;
  }
}
@media (max-width: 1023px) {
  .code-security-workspace.cs-workbench {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 700px) {
  .cs-project-home {
    margin: 12px;
    width: calc(100% - 24px);
    padding: 20px 16px;
  }
  .cs-project-toolbar,
  .cs-project-home > header {
    align-items: flex-start;
    flex-direction: column;
  }
  .cs-project-toolbar > div {
    flex-wrap: wrap;
  }
  .cs-workbench .cs-audit-metrics {
    display: flex;
    overflow-x: auto;
  }
  .cs-audit-metrics > div {
    min-width: 110px;
  }
  .cs-workbench .cs-execution {
    display: flex;
    flex-direction: column;
  }
  .cs-workbench .cs-phase-rail {
    flex-direction: row;
    overflow-x: auto;
    width: 100%;
    border-left: 0;
    padding: 12px;
  }
  .cs-workbench .cs-phase-step {
    min-width: 155px;
  }
  .cs-workbench .cs-stage-session {
    width: 100%;
    padding: 22px 16px;
  }
  .cs-workbench .cs-qa-composer {
    margin: 16px;
  }
  .cs-workbench .cs-drawer-layer {
    padding: 10px;
  }
  .cs-workbench .cs-new-audit {
    max-height: calc(100dvh - 20px);
  }
}

.cs-home-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.cs-register-project {
  padding: 20px;
  background: var(--cs-surface-subtle);
  border-radius: 12px;
  margin-bottom: 20px;
}
.cs-register-project label {
  display: grid;
  gap: 8px;
  margin: 16px 0;
  font-size: 13px;
}
.cs-register-project input {
  padding: 12px;
  border: 0;
  background: var(--cs-surface-elevated);
  color: var(--cs-text);
  border-radius: 6px;
}
.cs-workbench .cs-events {
  border: 0;
  background: transparent;
  padding: 0;
}
.cs-workbench .cs-event-filters select {
  border-color: transparent;
  background: transparent;
}
.cs-workbench .cs-event-row {
  background: transparent;
  border-bottom-color: var(--cs-border);
}
.cs-workbench .cs-advanced summary {
  background: transparent;
}

.cs-context-rail {
  grid-column: 2;
  grid-row: 2;
  border-left: 1px solid var(--cs-border);
  padding: 24px 16px;
  min-width: 0;
}
.cs-context-rail h3 {
  font-size: 13px;
  margin: 0 12px 14px;
  font-weight: 600;
}
.cs-workbench .cs-context-rail .cs-phase-rail {
  border: 0;
  padding: 0;
}
.cs-context-artifacts {
  margin-top: 32px;
}
.cs-context-artifacts > div {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin: 18px 12px;
  font-size: 12px;
}
.cs-context-artifacts small {
  color: var(--cs-text-secondary);
}
.cs-context-artifacts button {
  color: var(--cs-primary);
  border: 0;
  background: transparent;
  padding: 10px 12px;
  cursor: pointer;
}
@media (max-width: 700px) {
  .cs-context-rail {
    width: 100%;
    border: 0;
    padding: 12px;
  }
  .cs-context-artifacts {
    display: none;
  }
  .cs-context-rail .cs-phase-rail {
    overflow-x: auto;
  }
}

.cs-workbench .cs-button--primary {
  color: var(--cs-on-primary);
}
.cs-workbench .cs-new-audit form {
  padding-bottom: 0;
}
.cs-workbench .cs-new-audit form > footer {
  position: sticky;
  bottom: 0;
  margin: 0 -20px;
  border: 0;
  padding: 16px 20px;
}
.cs-workbench .cs-qa-composer {
  position: sticky;
  bottom: 16px;
  z-index: 4;
  box-shadow: 0 0 0 8px var(--cs-surface);
}
.cs-workbench .cs-event-viewport {
  height: min(260px, 32vh) !important;
}
.cs-session-steps {
  margin-top: 24px;
}
.cs-session-steps > summary {
  cursor: pointer;
  font-size: 13px;
  color: var(--cs-text-secondary);
  padding: 10px 0;
}
.cs-workbench .cs-event-row {
  border: 0;
}
.cs-workbench .cs-status {
  border-color: transparent;
}
.cs-workbench .cs-scan-item {
  border-color: transparent;
}
.cs-workbench .cs-scan-search,
.cs-workbench .cs-scan-filters select {
  border-color: transparent;
  background: var(--cs-surface-subtle);
}

.cs-workbench .cs-scan-item,
.cs-workbench .cs-scan-item__select {
  min-height: 74px;
}
.cs-workbench .cs-scan-item.is-virtual {
  height: 74px;
}
.cs-workbench .cs-scan-item__select {
  align-content: center;
  gap: 10px;
  padding: 12px 10px;
}
.cs-workbench .cs-scan-item__top {
  padding-right: 48px;
}
.cs-workbench .cs-scan-item__bottom code {
  display: none;
}
.cs-workbench .cs-scan-item__bottom time {
  position: absolute;
  top: 12px;
  right: 10px;
  font-size: 10px;
}
.cs-workbench .cs-scan-item__actions {
  top: auto;
  bottom: 8px;
}
.cs-workbench .cs-scan-item__meta {
  padding-right: 90px;
  font-size: 11px;
}
.cs-workbench .cs-scan-item.is-selected::before {
  display: none;
}

@media (max-width: 700px) {
  .cs-workbench .cs-phase-step {
    flex: 0 0 160px;
    grid-template-columns: 1fr;
    min-height: 108px;
    align-content: start;
  }
  .cs-workbench .cs-phase-step > span:last-child {
    text-align: left;
    align-self: start;
  }
  .cs-workbench .cs-header-actions > * {
    font-size: 12px;
    white-space: nowrap;
    padding: 8px;
  }
  .cs-workbench .cs-qa-composer {
    position: static;
  }
  .cs-workbench .cs-drawer-scrim {
    display: block;
  }
}

.cs-native-sessions { margin: 24px 0; font-size: 13px; }
.cs-native-sessions h3 { font-size: 13px; }
.cs-native-session > summary,.cs-native-tool > summary { cursor: pointer; color: var(--cs-text-secondary); padding: 10px 0; }
.cs-native-message { padding: 12px 0; line-height: 1.7; overflow-wrap: anywhere; }
.cs-native-message > small { color: var(--cs-text-secondary); }
.cs-native-message--user { padding: 14px; border-radius: 12px; background: var(--cs-surface-subtle); }
.cs-native-tool pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; font-size: 11px; }
.cs-result-conversation { margin: 12px 284px 24px 32px; min-width: 0; }
.cs-workbench .cs-result-conversation .cs-qa-composer { margin: 20px 0 0; position: static; }
.cs-qa-composer > div { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
.cs-answer-turn { padding: 20px 0; font-size: 14px; line-height: 1.7; overflow-wrap: anywhere; }
.cs-user-question { padding: 14px 18px; border-radius: 16px; background: var(--cs-surface-subtle); margin-bottom: 20px; white-space: pre-wrap; }
.cs-answer-sources { display: flex; flex-wrap: wrap; gap: 12px; font-size: 12px; }
.cs-answer-sources button,.cs-native-sessions button { border: 0; background: none; color: var(--cs-primary); cursor: pointer; }
.cs-configuration-messages { text-align: left; max-height: 300px; overflow-y: auto; }
.cs-configuration-message { padding: 12px; line-height: 1.6; white-space: pre-wrap; }
.cs-configuration-message--user { background: var(--cs-surface-subtle); border-radius: 12px; }
.cs-configuration-message small { color: var(--cs-text-secondary); }
@media(max-width:1100px) {.cs-result-conversation { margin-right:230px; }}
@media(max-width:700px) {.cs-result-conversation { margin:16px; }}
/* Audit navigation and overview use a quiet, flat visual hierarchy.
   Session identity, tool steps and message rendering retain the workbench style. */
.cs-workbench .cs-button { border-radius: 7px; font-size: 13px; font-weight: 500; min-height: 38px; }
.cs-workbench .cs-button--primary { background: var(--cs-primary); border-color: var(--cs-primary); }
.cs-workbench .cs-button--primary:hover:not(:disabled) { background: var(--cs-primary-hover); }
.dark .cs-workbench .cs-button--primary { color: #162238; }
.cs-workbench .cs-button--secondary:hover:not(:disabled),
.cs-workbench .cs-icon-button:hover:not(:disabled) { background: var(--cs-surface-subtle); }
.cs-workbench .cs-scan-panel { background: var(--cs-chrome); gap: 10px; padding: 24px 14px 16px; }
.cs-workbench .cs-panel-heading { padding: 0 8px 12px; }
.cs-workbench .cs-panel-heading h2 { font-size: 16px; font-weight: 600; }
.cs-workbench .cs-eyebrow { font-size: 12px; font-weight: 400; margin-bottom: 6px; }
.cs-workbench .cs-scan-panel > .cs-button { justify-content: flex-start; padding: 9px 12px; width: 100%; margin: 0; }
.cs-workbench .cs-scan-panel > .cs-button--full:last-of-type { background: var(--cs-surface); margin: 4px 0 10px; }
.cs-workbench .cs-search { border-color: transparent; background: var(--cs-surface); min-height: 38px; }
.cs-workbench .cs-search input { border: 0; border-radius: 0; background: none; font-size: 12px; box-shadow: none; }
.cs-workbench .cs-filter-label { grid-template-columns: auto 1fr; align-items: center; gap: 8px; padding: 2px 6px 8px; }
.cs-workbench .cs-filter-label > span { font-weight: 400; }
.cs-workbench .cs-filter-label select { background: transparent; border: 0; font-size: 12px; padding: 0 4px; width: 100%; }
.cs-workbench .cs-scan-item { border: 0; border-radius: 6px; }
.cs-workbench .cs-scan-item.is-selected { background: var(--cs-surface-selected); }
.cs-workbench .cs-scan-item.is-selected::before { background: var(--cs-primary); width: 3px; }
.cs-workbench .cs-scan-item__top strong { font-weight: 500; }
.cs-breadcrumbs { display: flex; align-items: center; gap: 2px; padding: 12px 24px 0; }
.cs-workbench .cs-breadcrumbs .cs-home-link { padding: 6px 8px; font-size: 12px; }
.cs-breadcrumbs > span { color: var(--cs-text-muted); font-size: 12px; }
.cs-workbench .cs-scan-header { padding: 20px 32px 20px; border-bottom: 1px solid var(--cs-border); }
.cs-workbench .cs-scan-header h1 { font-size: 22px; font-weight: 600; letter-spacing: -0.4px; }
.cs-workbench .cs-scan-header .cs-button--danger { background: transparent; border-color: transparent; color: var(--cs-text-secondary); }
.cs-workbench .cs-scan-header .cs-button--danger:hover { color: var(--cs-danger); background: var(--cs-danger-soft); }
.cs-workbench .cs-audit-metrics > div + div { padding-left: 24px; border-left: 1px solid var(--cs-border); }
.cs-workbench .cs-audit-metrics small { font-size: 12px; line-height: 1.5; }
.cs-workbench .cs-audit-metrics .cs-metric-text { letter-spacing: 0; }
.cs-workbench .cs-execution > .cs-section-heading { padding: 20px 32px 0; }
.cs-workbench .cs-execution > .cs-section-heading h2 { font-size: 13px; }
.cs-workbench .cs-context-rail .cs-phase-rail { counter-reset: audit-stage; gap: 4px; }
.cs-workbench .cs-context-rail .cs-phase-step { position: relative; display: grid; grid-template-columns: 1fr; gap: 6px; min-height: 86px; padding: 12px 10px 12px 38px; border-radius: 6px; }
.cs-workbench .cs-context-rail .cs-phase-step::before { counter-increment: audit-stage; content: counter(audit-stage, decimal-leading-zero); position: absolute; top: 14px; left: 12px; font-size: 11px; font-variant-numeric: tabular-nums; color: var(--cs-text-secondary); }
.cs-workbench .cs-context-rail .cs-phase-step > strong { grid-row: 1; font-size: 13px; font-weight: 500; }
.cs-workbench .cs-context-rail .cs-phase-step > .cs-status { grid-row: 2; }
.cs-workbench .cs-context-rail .cs-phase-step > span:last-child { font-size: 11px; text-align: left; }
.cs-workbench .cs-context-rail .cs-phase-step.is-selected { background: var(--cs-surface-selected); }
.cs-workbench .cs-context-rail .cs-phase-step.is-selected::before { color: var(--cs-primary); }
.cs-context-artifacts { padding-top: 24px; border-top: 1px solid var(--cs-border); margin-top: 24px; }
.cs-context-artifacts > div { margin: 16px 12px; gap: 12px; }
.cs-context-artifacts small { font-size: 11px; }
.cs-project-home .cs-project-toolbar input { width: 260px; }
.cs-project-home .cs-project-toolbar input, .cs-project-home .cs-project-toolbar select { min-height: 38px; font-size: 13px; }
.cs-project-home .cs-project-toolbar small { margin-left: 10px; color: var(--cs-text-secondary); font-size: 12px; font-weight: 400; }
.cs-project-table thead th { padding-top: 12px; padding-bottom: 12px; }
.cs-project-table td:first-child strong { font-size: 14px; font-weight: 500; }
.cs-project-table td:first-child, .cs-project-table th:first-child { padding-left: 0; }
.cs-project-table td:last-child, .cs-project-table th:last-child { text-align: right; padding-right: 0; }
.cs-project-table tbody tr:hover { background: var(--cs-surface-subtle); }
.cs-project-table tbody td { font-variant-numeric: tabular-nums; }
.cs-project-home > footer { margin-top: 24px; }
.cs-project-home > .cs-home-link { padding: 0 0 24px; font-size: 12px; }
.cs-workbench .cs-new-audit { border-radius: 12px; max-width: 720px; }
.cs-workbench .cs-new-audit > header { padding: 28px 32px 16px; }
.cs-workbench .cs-new-audit h2 { font-size: 21px; font-weight: 600; }
.cs-workbench .cs-mode-switch { gap: 28px; padding-bottom: 12px; }
.cs-workbench .cs-mode-switch button { font-size: 14px; }
.cs-workbench .cs-mode-switch button[aria-selected="true"] { color: var(--cs-primary); border-bottom-color: var(--cs-primary); }
.cs-workbench .cs-new-audit form { padding-left: 32px; padding-right: 32px; }
.cs-workbench .cs-new-audit fieldset { padding: 20px 0; }
.cs-workbench .cs-new-audit form > footer { margin: 0; padding: 20px 0; }
.cs-workbench .cs-new-audit .cs-field > label { font-weight: 500; }
@media (max-width: 1100px) and (min-width: 701px) {
  .code-security-workspace.cs-workbench { grid-template-columns: 200px minmax(0, 1fr); }
  .cs-workbench .cs-audit-metrics { padding: 24px; gap: 12px; }
  .cs-workbench .cs-audit-metrics > div + div { padding-left: 12px; }
}
@media (max-width: 700px) {
  .code-security-workspace.cs-workbench { grid-template-columns: minmax(0, 1fr); }
  .cs-project-home { width: auto; margin: 24px 20px; padding: 0; }
  .cs-project-home > header { align-items: flex-start; padding-bottom: 24px; }
  .cs-project-home h1 { font-size: 22px; }
  .cs-project-home .cs-project-toolbar { align-items: stretch; }
  .cs-project-home .cs-project-toolbar > div { flex-wrap: wrap; }
  .cs-project-home .cs-project-toolbar input { width: 100%; min-width: 0; }
  .cs-breadcrumbs { padding: 10px 12px 0; }
  .cs-workbench .cs-scan-header { padding: 16px; }
  .cs-workbench .cs-audit-metrics { padding: 20px 16px; gap: 20px; }
  .cs-workbench .cs-audit-metrics > div + div { padding-left: 0; border-left: 0; }
  .cs-workbench .cs-context-rail .cs-phase-step { flex: 0 0 180px; min-height: 96px; }
  .cs-workbench .cs-new-audit > header { padding: 24px 20px 12px; }
  .cs-workbench .cs-new-audit form { padding-left: 20px; padding-right: 20px; }
}

/* Color belongs to the audit chrome; the conversation stays on a neutral canvas. */
.cs-workbench .cs-breadcrumbs { border-top: 3px solid var(--cs-primary); background: linear-gradient(100deg, var(--cs-overview-tint), var(--cs-surface) 70%); padding-top: 10px; }
.cs-workbench .cs-audit-metrics { background: linear-gradient(100deg, var(--cs-overview-tint), var(--cs-surface) 110%); padding-top: 24px; padding-bottom: 26px; }
.cs-workbench .cs-audit-metrics strong { color: var(--cs-primary); }
.cs-workbench .cs-audit-metrics .cs-metric-findings.has-findings strong { color: var(--cs-danger); }
.cs-workbench .cs-audit-metrics .cs-metric-text { color: var(--cs-text); }
.cs-workbench .cs-audit-metrics > div + div { border-color: color-mix(in srgb, var(--cs-primary) 14%, transparent); }
.cs-workbench .cs-execution > .cs-section-heading h2 { display: flex; align-items: center; gap: 8px; color: var(--cs-text); font-weight: 600; }
.cs-workbench .cs-execution > .cs-section-heading h2::before { content: ""; width: 3px; height: 14px; background: var(--cs-primary); border-radius: 2px; }
.cs-workbench .cs-context-rail { background: linear-gradient(180deg, var(--cs-overview-tint), var(--cs-surface) 400px); margin: 16px 16px 0 0; border: 0; border-radius: 12px; }
.cs-workbench .cs-context-rail .cs-phase-step.is-selected { background: var(--cs-surface); box-shadow: inset 3px 0 var(--cs-primary), 0 3px 12px color-mix(in srgb, var(--cs-primary) 8%, transparent); }
.cs-workbench .cs-context-rail .cs-phase-step.is-selected > strong { color: var(--cs-primary); font-weight: 600; }
.cs-workbench.cs-home-view { background: linear-gradient(180deg, var(--cs-overview-tint), var(--cs-surface) 440px); }
.cs-project-home > header { padding: 28px 28px 32px; margin: -12px -28px 16px; border-top: 3px solid var(--cs-primary); background: linear-gradient(110deg, var(--cs-surface), color-mix(in srgb, var(--cs-overview-tint) 50%, transparent)); border-radius: 0 0 12px 12px; }
.cs-project-home > header h1 { color: var(--cs-text); }
.cs-project-home .cs-project-toolbar { border-top: 0; }
.cs-project-home .cs-project-table th { background: var(--cs-overview-tint); }
.cs-project-home .cs-project-table th:first-child { padding-left: 16px; border-radius: 8px 0 0 8px; }
.cs-project-home .cs-project-table th:last-child { padding-right: 16px; border-radius: 0 8px 8px 0; }
.cs-project-home .cs-project-table td:first-child { padding-left: 16px; }
.cs-project-home .cs-project-table td:last-child { padding-right: 16px; }
.cs-project-home .cs-project-table tbody { background: var(--cs-surface); }
.cs-project-home .cs-project-table tbody tr:hover { background: var(--cs-overview-tint); }
.cs-workbench .cs-new-audit > header { background: linear-gradient(110deg, var(--cs-overview-tint), var(--cs-surface)); }
@media(max-width:700px) {
  .cs-project-home > header { margin: 0 0 12px; padding: 20px 0; background: transparent; }
  .cs-workbench .cs-context-rail { margin: 12px 12px 0; width: calc(100% - 24px); }
}

.cs-project-home .cs-project-path { font-size: 12px; overflow-wrap: anywhere; margin-top: 14px; }
.cs-project-home > header .cs-eyebrow { color: var(--cs-primary); margin: 0 0 10px; font-size: 12px; }
.cs-breadcrumbs { flex-wrap: wrap; }
.code-security-workspace.cs-workbench.cs-task-view { grid-template-columns: minmax(0, 1fr); }
.cs-task-view .cs-main-column { grid-column: 1 / -1; }
.cs-task-view .cs-scan-header { grid-template-columns: minmax(0, 1fr) auto; }
.cs-project-table .cs-record-delete { margin-left: 12px; color: var(--cs-text-secondary); vertical-align: middle; }
.cs-record-delete svg { width: 16px; height: 16px; }
@media(max-width:767px) { .cs-task-view .cs-scan-header { grid-template-columns: 1fr; } }
`;

export default styles;
