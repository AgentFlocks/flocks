import { useState, useEffect, useRef, useCallback } from 'react';
import { Workflow as WorkflowIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Workflow, WorkflowJSON } from '@/api/workflow';
import FlowCanvas from '../WorkflowDetail/FlowCanvas';
import CreateTopBar from './CreateTopBar';
import CreateRightPanel from './CreateRightPanel';

const PANEL_MIN = 240;
const PANEL_RATIO = 0.40;

const EMPTY_WORKFLOW_JSON: WorkflowJSON = {
  start: '',
  nodes: [],
  edges: [],
};

function getInitialPanelWidth() {
  const sidebarWidth = window.innerWidth >= 1024 ? 256 : 0;
  const available = window.innerWidth - sidebarWidth;
  return Math.max(PANEL_MIN, Math.round(available * PANEL_RATIO));
}

export default function WorkflowCreate() {
  const { t } = useTranslation('workflow');
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const [panelWidth, setPanelWidth] = useState(getInitialPanelWidth);
  const dragging = useRef(false);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(0);

  useEffect(() => {
    const onResize = () => {
      const sidebarWidth = window.innerWidth >= 1024 ? 256 : 0;
      const maxAllowed = Math.round((window.innerWidth - sidebarWidth) * 0.7);
      setPanelWidth((w) => Math.min(w, Math.max(PANEL_MIN, maxAllowed)));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragging.current = true;
      dragStartX.current = e.clientX;
      dragStartWidth.current = panelWidth;

      const sidebarWidth = window.innerWidth >= 1024 ? 256 : 0;
      const panelMax = Math.round((window.innerWidth - sidebarWidth) * 0.7);

      const onMove = (ev: MouseEvent) => {
        if (!dragging.current) return;
        const delta = dragStartX.current - ev.clientX;
        setPanelWidth(Math.min(panelMax, Math.max(PANEL_MIN, dragStartWidth.current + delta)));
      };
      const onUp = () => {
        dragging.current = false;
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
      };
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    },
    [panelWidth],
  );

  const handleWorkflowCreated = useCallback((newWorkflow: Workflow) => {
    setWorkflow(newWorkflow);
  }, []);

  return (
    <div className="flex flex-col h-full bg-gray-50 overflow-hidden">
      <CreateTopBar
        workflow={workflow}
        panelOpen={panelOpen}
        onTogglePanel={() => setPanelOpen((v) => !v)}
      />

      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* 左侧画布 */}
        <div className="flex-1 min-w-0 relative">
          <FlowCanvas
            workflowJson={workflow?.workflowJson ?? EMPTY_WORKFLOW_JSON}
            editable={false}
          />
          {/* 空状态遮罩 */}
          {!workflow && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 pointer-events-none">
              <div className="flex flex-col items-center gap-3 bg-white/90 backdrop-blur-sm rounded-2xl border border-dashed border-gray-300 px-10 py-8 shadow-sm">
                <div className="flex items-center justify-center w-14 h-14 rounded-xl bg-gray-50 border border-gray-200">
                  <WorkflowIcon className="w-7 h-7 text-gray-300" />
                </div>
                <div className="text-center">
                  <p className="text-sm font-medium text-gray-500">{t('create.canvasTitle')}</p>
                  <p className="text-xs text-gray-400 mt-1 max-w-[200px] leading-relaxed">
                    {t('create.canvasHint')}
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* 拖动分隔条 */}
        {panelOpen && (
          <div
            onMouseDown={onDragStart}
            className="w-1 flex-shrink-0 bg-gray-200 hover:bg-red-400 active:bg-red-500 cursor-col-resize transition-colors duration-150 relative group"
            title={t('detail.dragAdjust')}
          >
            <div className="absolute inset-y-0 -left-1.5 -right-1.5" />
          </div>
        )}

        {/* 右侧面板 */}
        <CreateRightPanel
          workflow={workflow}
          open={panelOpen}
          width={panelWidth}
          onWorkflowCreated={handleWorkflowCreated}
        />
      </div>
    </div>
  );
}
