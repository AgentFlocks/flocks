export const CSS = `
.soc-cases { color:var(--text-primary); background:var(--bg-canvas); min-height:100%; font:var(--font-body)/var(--line-body) var(--font-sans); padding:24px; }
.soc-cases * { box-sizing:border-box; }
.soc-cases button,.soc-cases input,.soc-cases select,.soc-cases textarea { font:inherit; }
.soc-cases button,.soc-cases a.sc-button { background:var(--bg-surface); color:var(--text-primary); border:1px solid var(--border-control); border-radius:var(--radius-control); min-height:var(--control-md); padding:6px 12px; font-weight:var(--weight-medium); line-height:20px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; justify-content:center; gap:8px; transition:background-color var(--motion-fast) var(--motion-easing),border-color var(--motion-fast) var(--motion-easing); }
.soc-cases button:hover:not(:disabled),.soc-cases a.sc-button:hover { background:var(--bg-hover); }
.soc-cases :is(button,a,summary,input[type=checkbox]):focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:var(--focus-offset); }
.soc-cases button.sc-primary { background:var(--action-primary); border-color:transparent; color:var(--action-onPrimary); }
.soc-cases button.sc-primary:hover:not(:disabled) { background:var(--action-hover); }
.soc-cases button.sc-primary:active:not(:disabled) { background:var(--action-pressed); }
.soc-cases button:disabled,.soc-cases input:disabled,.soc-cases select:disabled,.soc-cases textarea:disabled { cursor:not-allowed; opacity:1; background:var(--disabled-bg); color:var(--disabled-text); border-color:var(--border-subtle); }
.soc-cases h1,.soc-cases h2,.soc-cases h3,.soc-cases h4,.soc-cases p { margin:0; }
.soc-cases h1 { font-size:24px; line-height:32px; font-weight:var(--weight-bold); }
.soc-cases h2 { font-size:20px; line-height:28px; font-weight:var(--weight-semibold); }
.soc-cases h3,.soc-cases h4 { font-size:16px; line-height:24px; font-weight:var(--weight-semibold); }
.sc-heading,.sc-row { display:flex; gap:12px; align-items:center; justify-content:space-between; flex-wrap:wrap; }
.sc-heading { margin-bottom:16px; }
.sc-heading>div,.sc-row>* { min-width:0; overflow-wrap:anywhere; }
.sc-muted { color:var(--text-muted); font-size:12px; line-height:18px; }
.sc-heading p { margin-top:4px; }
.sc-buttons { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
.sc-layout { display:grid; grid-template-columns:280px minmax(0,1fr); gap:24px; align-items:start; }
.sc-panel { min-width:0; background:var(--bg-surface); border:1px solid var(--border-subtle); border-radius:var(--radius-card); overflow:hidden; }
.sc-list-head { padding:16px; border-bottom:1px solid var(--border-subtle); }
.sc-list-head input { margin-top:12px; }
.soc-cases input,.soc-cases textarea,.soc-cases select { display:block; width:100%; min-width:0; border:1px solid var(--border-control); border-radius:var(--radius-control); background:var(--bg-surface); color:var(--text-primary); padding:6px 12px; }
.soc-cases input:not([type=checkbox]),.soc-cases select { height:var(--control-md); }
.soc-cases input::placeholder,.soc-cases textarea::placeholder { color:var(--text-placeholder); opacity:1; }
.soc-cases input:not([type=checkbox]):focus-visible,.soc-cases textarea:focus-visible,.soc-cases select:focus-visible { outline:none; box-shadow:none; border-color:var(--action-primary); }
.soc-cases textarea { resize:vertical; min-height:84px; padding-block:8px; }
.soc-cases input[type=checkbox] { width:16px; height:16px; min-height:0; padding:0; display:inline-block; flex:none; accent-color:var(--action-primary); }
.soc-cases button.sc-case { width:100%; border:0; border-radius:0; border-bottom:1px solid var(--border-subtle); text-align:left; display:block; padding:16px; }
.soc-cases button.sc-case.active { background:var(--bg-selected); box-shadow:inset 3px 0 var(--focus-ring); }
.soc-cases button.sc-case:focus-visible { outline-offset:calc(-1 * var(--focus-width)) !important; }
.sc-case strong { display:block; font-size:14px; line-height:22px; overflow-wrap:anywhere; margin:8px 0; }
.sc-badge { display:inline-flex; align-items:center; padding:2px 8px; border-radius:var(--radius-sm); background:var(--bg-subtle); color:var(--text-secondary); font-size:12px; line-height:20px; font-weight:var(--weight-medium); white-space:nowrap; }
.sc-badge.high,.sc-badge.failed,.sc-badge.rejected,.sc-badge.critical { background:var(--status-dangerBg); color:var(--status-danger); }
.sc-badge.medium,.sc-badge.pending,.sc-badge.unverified,.sc-badge.unknown,.sc-badge.inconclusive,.sc-badge.running,.sc-badge.blocked { background:var(--status-warningBg); color:var(--status-warning); }
.sc-badge.verified,.sc-badge.closed,.sc-badge.approved,.sc-badge.supported { background:var(--status-successBg); color:var(--status-success); }
.sc-badge.investigating,.sc-badge.responding,.sc-badge.succeeded { background:var(--status-infoBg); color:var(--status-info); }
.sc-detail-head { padding:24px; border-bottom:1px solid var(--border-subtle); }
.sc-detail-head h2 { margin:12px 0 8px; overflow-wrap:anywhere; }
.sc-detail-head p { white-space:pre-wrap; overflow-wrap:anywhere; }
.sc-progress { display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }
.sc-progress span { font-size:12px; line-height:18px; color:var(--text-muted); }
.sc-progress span.current { font-weight:var(--weight-semibold); color:var(--accent-brand); }
.sc-tabs { display:flex; gap:4px; border-bottom:1px solid var(--border-subtle); padding:0 24px; overflow:auto; }
.soc-cases .sc-tabs button { min-height:44px; border:0; border-bottom:2px solid transparent; border-radius:0; background:transparent; color:var(--text-secondary); white-space:nowrap; padding:8px 12px; }
.soc-cases .sc-tabs button:hover { background:var(--bg-hover); }
.soc-cases .sc-tabs button[aria-selected=true] { border-bottom-color:var(--action-primary); color:var(--text-primary); font-weight:var(--weight-semibold); }
.soc-cases .sc-tabs button:focus-visible,.soc-cases .sc-content:focus-visible { outline-offset:calc(-1 * var(--focus-width)) !important; }
.sc-content { padding:24px; display:grid; gap:24px; }
.sc-section { display:grid; gap:12px; min-width:0; }
.sc-card { background:var(--bg-surface); border:1px solid var(--border-subtle); border-radius:var(--radius-card); padding:16px; display:grid; gap:12px; min-width:0; }
.sc-card p,.sc-card dd,.sc-pre { white-space:pre-wrap; overflow-wrap:anywhere; }
.sc-card dl { margin:0; display:grid; grid-template-columns:88px minmax(0,1fr); gap:8px 12px; }
.sc-card dt { color:var(--text-secondary); }
.sc-card dd { margin:0; }
.sc-statuses { display:flex; flex-wrap:wrap; gap:12px; padding:8px 0; }
.sc-statuses label { font-size:12px; line-height:18px; color:var(--text-secondary); display:flex; align-items:center; gap:8px; }
.sc-empty { padding:32px 24px; color:var(--text-muted); text-align:center; }
.sc-notice { padding:12px 16px; border:1px solid var(--border-subtle); border-radius:var(--radius-control); background:var(--status-infoBg); color:var(--status-info); margin-bottom:16px; overflow-wrap:anywhere; }
.sc-notice.error { background:var(--status-dangerBg); color:var(--status-danger); }
.sc-notice .sc-buttons { margin-top:12px; }
.sc-session { display:grid; gap:8px; padding:16px; background:var(--bg-subtle); border-radius:var(--radius-card); }
.sc-session label { font-weight:var(--weight-medium); }
.sc-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }
.sc-modal-backdrop { position:fixed; inset:0; z-index:1000; background:var(--overlay-backdrop); display:flex; justify-content:center; align-items:center; padding:24px; }
.sc-modal { display:flex; flex-direction:column; background:var(--bg-surface); border:1px solid var(--border-subtle); border-radius:var(--radius-overlay); width:var(--overlay-modal); max-width:100%; max-height:calc(100dvh - 48px); overflow:hidden; box-shadow:var(--shadow-overlay); }
.sc-modal-header { flex:none; padding:24px; border-bottom:1px solid var(--border-subtle); }
.sc-modal-header p { margin-top:8px; overflow-wrap:anywhere; }
.sc-modal form { min-height:0; display:flex; flex-direction:column; }
.sc-modal-body { min-height:0; overflow:auto; padding:24px; display:grid; gap:16px; }
.sc-field { display:grid; gap:8px; min-width:0; }
.sc-field>span,.sc-field legend { font-weight:var(--weight-medium); }
.sc-field small { color:var(--text-secondary); font-size:12px; line-height:18px; }
.sc-evidence { margin:0; padding:0; border:0; }
.sc-evidence legend { margin-bottom:8px; }
.sc-evidence-options { border:1px solid var(--border-subtle); border-radius:var(--radius-control); max-height:200px; overflow:auto; }
.sc-evidence-options label { display:flex; align-items:flex-start; gap:8px; padding:12px; border-bottom:1px solid var(--border-subtle); overflow-wrap:anywhere; }
.sc-evidence-options label:last-child { border-bottom:0; }
.sc-evidence-options span { min-width:0; }
.sc-evidence-options small { display:block; color:var(--text-muted); }
.sc-modal-footer { flex:none; display:flex; flex-wrap:wrap; gap:8px; justify-content:flex-end; padding:16px 24px; border-top:1px solid var(--border-subtle); }
.sc-pre { font:12px/18px var(--font-mono); color:var(--text-primary); background:var(--bg-subtle); border-radius:var(--radius-control); padding:12px; max-height:220px; overflow:auto; margin:4px 0 0; }
.sc-timeline { border-left:2px solid var(--border-subtle); margin-left:4px; padding-left:24px; }
.sc-timeline article { position:relative; padding-bottom:24px; }
.sc-timeline article:before { content:''; position:absolute; width:8px; height:8px; border-radius:var(--radius-pill); background:var(--text-muted); left:-29px; top:6px; }
.sc-footer { padding:12px 16px; display:flex; justify-content:space-between; align-items:center; gap:8px; }
@media(max-width:1050px) { .soc-cases { padding:16px; } .sc-layout { grid-template-columns:240px minmax(0,1fr); gap:16px; } .sc-content,.sc-detail-head { padding:16px; } }
@media(max-width:767px) { .sc-layout { grid-template-columns:minmax(0,1fr); } .sc-list { max-height:340px; overflow:auto; } .sc-grid { grid-template-columns:1fr; } .sc-modal-backdrop { padding:16px; } .sc-modal { max-height:calc(100dvh - 32px); } .sc-modal-body,.sc-modal-header,.sc-modal-footer { padding:16px; } .sc-tabs { padding:0 4px; } }
@media(max-width:767px),(pointer:coarse) { .soc-cases button,.soc-cases a.sc-button,.soc-cases input:not([type=checkbox]),.soc-cases select,.soc-cases .sc-tabs button { min-height:var(--control-touch); } .sc-evidence-options label { min-height:var(--control-touch); } }
`;
