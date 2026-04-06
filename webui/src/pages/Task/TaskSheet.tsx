/**
 * TaskSheet — 统一的任务创建/编辑侧边面板
 *
 * 合并原有的 CreateTaskDialog 和 EditScheduledTaskDialog，支持：
 * - 创建队列任务 / 定时任务（两种类型）
 * - 编辑现有定时任务
 * - Rex 对话模式（自然语言描述任务 → 填充表单）
 */

import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Calendar, Clock, ListTodo, Sparkles } from 'lucide-react';
import { taskAPI, Task, TaskType, TaskPriority, TaskCreateParams, ExecutionMode } from '@/api/task';
import { sessionApi } from '@/api/session';
import client from '@/api/client';
import { useToast } from '@/components/common/Toast';
import EntitySheet, { useEntitySheet } from '@/components/common/EntitySheet';
import PillGroup from '@/components/common/PillGroup';
import { useTaskRecords } from '@/hooks/useTasks';
import { describeCron, CRON_PRESETS, formatDuration, formatTime } from './helpers';
import { agentAPI, Agent } from '@/api/agent';
import { workflowAPI, Workflow } from '@/api/workflow';
import { getAgentDisplayDescription } from '@/utils/agentDisplay';
import { StatusBadge } from './components';

// ─── Types ────────────────────────────────────────────────────────────────────

interface TaskFormData {
  title: string;
  description: string;
  type: TaskType;
  priority: TaskPriority;
  executionMode: ExecutionMode;
  agentName: string;
  workflowID: string;
  userPrompt: string;
  // Scheduling (only for scheduled tasks)
  scheduleMode: 'recurring' | 'once';
  cron: string;
  runAt: string;
  timezone: string;
  cronDescription: string;
}

// ─── TaskSheet ────────────────────────────────────────────────────────────────

interface TaskSheetProps {
  /** undefined = 创建模式；传入 Task = 编辑模式（仅限定时任务） */
  task?: Task | null;
  /** 创建时的默认类型 */
  defaultType?: TaskType;
  onClose: () => void;
  onSaved: () => void;
}

export default function TaskSheet({ task, defaultType = 'queued', onClose, onSaved }: TaskSheetProps) {
  const isEdit = !!task;
  const { t } = useTranslation('task');
  const toast = useToast();

  const isRunOnce = task?.schedule?.runOnce ?? false;

  const [formData, setFormData] = useState<TaskFormData>(() => {
    if (task) {
      const preset = CRON_PRESETS.find((p) => p.value === task.schedule?.cron && p.value !== '__custom__');
      return {
        title: task.title,
        description: task.description ?? '',
        type: task.type ?? 'scheduled',
        priority: task.priority,
        executionMode: task.executionMode,
        agentName: task.agentName ?? 'rex',
        workflowID: task.workflowID ?? '',
        userPrompt: task.source?.userPrompt ?? '',
        scheduleMode: isRunOnce ? 'once' : 'recurring',
        cron: preset ? preset.value : (task.schedule?.cron ?? ''),
        runAt: (() => {
          const src = task.schedule?.runAt ?? task.schedule?.nextRun;
          if (!src) return '';
          const d = new Date(src);
          const pad = (n: number) => String(n).padStart(2, '0');
          return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
        })(),
        timezone: task.schedule?.timezone ?? 'Asia/Shanghai',
        cronDescription: task.schedule?.cronDescription ?? '',
      };
    }
    return {
      title: '',
      description: '',
      type: defaultType,
      priority: 'normal',
      executionMode: 'agent',
      agentName: 'rex',
      workflowID: '',
      userPrompt: '',
      scheduleMode: 'recurring',
      cron: '',
      runAt: '',
      timezone: 'Asia/Shanghai',
      cronDescription: '',
    };
  });

  const [loading, setLoading] = useState(false);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);

  useEffect(() => {
    agentAPI.list().then((res) => setAgents(res.data.filter((a) => !a.hidden))).catch(() => {});
    workflowAPI.list({ status: 'active' }).then((res) => setWorkflows(res.data)).catch(() => {});
  }, []);

  const isScheduled = formData.type === 'scheduled';
  const isOnce = isScheduled && formData.scheduleMode === 'once';
  const effectiveCron = (() => {
    const preset = CRON_PRESETS.find((p) => p.value === formData.cron && p.value !== '__custom__');
    return preset ? preset.value : formData.cron;
  })();

  const canSubmit =
    formData.title.trim() &&
    (!isScheduled || (isOnce ? formData.runAt : effectiveCron.trim()));

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setLoading(true);
    try {
      if (isEdit) {
        if (isRunOnce) {
          await taskAPI.update(task!.id, {
            title: formData.title.trim(),
            description: formData.description.trim(),
            priority: formData.priority,
            runOnce: true,
            runAt: formData.runAt ? new Date(formData.runAt).toISOString() : undefined,
            executionMode: formData.executionMode,
            agentName: formData.executionMode === 'agent' ? formData.agentName : undefined,
            workflowID: formData.executionMode === 'workflow' ? formData.workflowID : undefined,
            userPrompt: formData.userPrompt || undefined,
          });
        } else {
          await taskAPI.update(task!.id, {
            title: formData.title.trim(),
            description: formData.description.trim(),
            priority: formData.priority,
            cron: effectiveCron.trim(),
            cronDescription: formData.cronDescription.trim() || undefined,
            timezone: formData.timezone,
            executionMode: formData.executionMode,
            agentName: formData.executionMode === 'agent' ? formData.agentName : undefined,
            workflowID: formData.executionMode === 'workflow' ? formData.workflowID : undefined,
            userPrompt: formData.userPrompt || undefined,
          });
        }
      } else {
        const params: TaskCreateParams = {
          title: formData.title.trim(),
          description: formData.description,
          type: formData.type,
          priority: formData.priority,
          executionMode: formData.executionMode,
          agentName: formData.executionMode === 'agent' ? formData.agentName : undefined,
          workflowID: formData.executionMode === 'workflow' ? formData.workflowID : undefined,
          userPrompt: formData.userPrompt || undefined,
        };
        if (isOnce) {
          params.runOnce = true;
          params.runAt = formData.runAt ? new Date(formData.runAt).toISOString() : undefined;
        } else if (isScheduled) {
          params.cron = effectiveCron;
        }
        await taskAPI.create(params);
      }
      onSaved();
      onClose();
    } catch (err: unknown) {
      toast.error(t(isEdit ? 'taskSheet.saveFailed' : 'taskSheet.createFailed'), err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  // ── Rex: extract config from conversation ─────────────────────────────────

  const handleExtractFromRex = async (sessionId: string) => {
    const fields = isEdit && isRunOnce
      ? `{"title": "...", "description": "...", "priority": "urgent|high|normal|low", "executionMode": "agent|workflow", "agentName": "...", "userPrompt": "..."}`
      : isEdit
      ? `{"title": "...", "description": "...", "priority": "urgent|high|normal|low", "cron": "...", "executionMode": "agent|workflow", "agentName": "...", "userPrompt": "..."}`
      : `{"title": "...", "description": "...", "type": "queued|scheduled", "priority": "urgent|high|normal|low", "executionMode": "agent|workflow", "agentName": "...", "userPrompt": "...", "cron": "（定时任务的 cron 表达式，队列任务留空）"}`;

    const extractPrompt = `请将以上讨论的任务配置整理为 JSON，只输出 JSON 对象：
\`\`\`json
${fields}
\`\`\``;

    await client.post(`/api/session/${sessionId}/prompt_async`, {
      parts: [{ type: 'text', text: extractPrompt }],
    });

    const start = Date.now();
    const initialCount = (await sessionApi.getMessages(sessionId)).length;

    while (Date.now() - start < 60000) {
      await new Promise((r) => setTimeout(r, 1500));
      const messages = await sessionApi.getMessages(sessionId);

      if (messages.length > initialCount) {
        const lastAssistant = [...messages]
          .reverse()
          .find((m: any) => m.role === 'assistant' && m.finish);

        if (lastAssistant) {
          const text = (lastAssistant.parts ?? [])
            .filter((p: any) => p.type === 'text')
            .map((p: any) => p.text ?? '')
            .join('');

          const config = parseJsonFromText(text);
          if (config) {
            setFormData((prev) => ({
              ...prev,
              title: config.title || prev.title,
              description: config.description ?? prev.description,
              type: config.type || prev.type,
              priority: config.priority || prev.priority,
              executionMode: config.executionMode || prev.executionMode,
              agentName: config.agentName || prev.agentName,
              userPrompt: config.userPrompt ?? prev.userPrompt,
              cron: config.cron || prev.cron,
            }));
            return;
          }
        }
      }
    }

    throw new Error(t('taskSheet.extractTimeout'));
  };

  const icon = isScheduled ? <Calendar className="w-5 h-5" /> : <ListTodo className="w-5 h-5" />;

  return (
    <EntitySheet
      open
      mode={isEdit ? 'edit' : 'create'}
      entityType={t('taskSheet.entityType')}
      entityName={task?.title}
      icon={icon}
      rexSystemContext={buildRexContext(formData, isEdit)}
      rexWelcomeMessage={buildRexWelcome(isEdit, task?.title)}
      submitDisabled={!canSubmit || loading}
      submitLoading={loading}
      width={640}
      minWidth={480}
      maxWidth={960}
      onClose={onClose}
      onSubmit={handleSubmit}
      onExtractFromRex={handleExtractFromRex}
    >
      <TaskFormContent
        task={task ?? undefined}
        formData={formData}
        onChange={setFormData}
        isEdit={isEdit}
        isRunOnce={isRunOnce}
        effectiveCron={effectiveCron}
        agents={agents}
        workflows={workflows}
      />
    </EntitySheet>
  );
}

// ─── TaskFormContent ──────────────────────────────────────────────────────────

interface TaskFormContentProps {
  task?: Task;
  formData: TaskFormData;
  onChange: (data: TaskFormData) => void;
  isEdit: boolean;
  isRunOnce: boolean;
  effectiveCron: string;
  agents: Agent[];
  workflows: Workflow[];
}

function TaskFormContent({
  task,
  formData,
  onChange,
  isEdit,
  isRunOnce,
  effectiveCron,
  agents,
  workflows,
}: TaskFormContentProps) {
  const { t, i18n } = useTranslation('task');
  const { openRex } = useEntitySheet();
  const update = (fields: Partial<TaskFormData>) => onChange({ ...formData, ...fields });

  const isScheduled = formData.type === 'scheduled';
  const isOnce = isScheduled && formData.scheduleMode === 'once';
  const showCustomCron = !CRON_PRESETS.find(
    (p) => p.value === formData.cron && p.value !== '__custom__',
  );
  const { records, loading: recordsLoading } = useTaskRecords(
    task?.type === 'scheduled' ? task.id : undefined,
    { limit: 5 },
  );

  return (
    <div className="space-y-4">
      {/* Title */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.titleLabel')}</label>
        <input
          type="text"
          value={formData.title}
          onChange={(e) => update({ title: e.target.value })}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-slate-400 outline-none text-sm"
          placeholder={t('form.titlePlaceholder')}
        />
      </div>

      {/* Description */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.descLabel')}</label>
        <textarea
          value={formData.description}
          onChange={(e) => update({ description: e.target.value })}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-slate-400 outline-none resize-none text-sm"
          rows={2}
          placeholder={t('form.descPlaceholder')}
        />
      </div>

      {/* Priority + Type */}
      <div className="space-y-2.5">
        <div className="flex items-center gap-3">
          <span className="w-14 shrink-0 text-sm font-medium text-gray-700">{t('form.priorityLabel')}</span>
          <PillGroup
            options={[
              { value: 'urgent', label: t('form.urgentLabel'), activeClass: 'bg-amber-500 text-white border-amber-500' },
              { value: 'high', label: t('form.highLabel'), activeClass: 'bg-orange-500 text-white border-orange-500' },
              { value: 'normal', label: t('form.normalLabel'), activeClass: 'bg-slate-500 text-white border-slate-500' },
              { value: 'low', label: t('form.lowLabel'), activeClass: 'bg-gray-500 text-white border-gray-500' },
            ]}
            value={formData.priority}
            onChange={(v) => update({ priority: v })}
          />
        </div>
        <div className="flex items-center gap-3">
          <span className="w-14 shrink-0 text-sm font-medium text-gray-700">{t('form.typeLabel')}</span>
          {isEdit ? (
            <span className="px-3 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-700">
              {isRunOnce ? t('form.runOnceType') : t('form.recurringType')}
            </span>
          ) : (
            <PillGroup
              options={[
                { value: 'queued', label: t('form.queueOption'), activeClass: 'bg-sky-600 text-white border-sky-600' },
                { value: 'scheduled', label: t('form.scheduledOption'), activeClass: 'bg-purple-600 text-white border-purple-600' },
              ]}
              value={formData.type}
              onChange={(v) => update({ type: v, cron: '', scheduleMode: 'recurring', runAt: '' })}
            />
          )}
        </div>
      </div>

      {/* Scheduling config */}
      {isScheduled && (
        <div
          className={`rounded-xl p-4 space-y-3 border ${
            isOnce || isRunOnce
              ? 'bg-orange-50/60 border-orange-100'
              : 'bg-purple-50/60 border-purple-100'
          }`}
        >
          <p
            className={`text-sm font-semibold flex items-center gap-1.5 ${
              isOnce || isRunOnce ? 'text-orange-800' : 'text-purple-800'
            }`}
          >
            <Calendar className="w-4 h-4" /> {t('form.scheduleConfig')}
          </p>

          {/* Schedule mode toggle (create only) */}
          {!isEdit && (
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1.5">{t('form.frequencyLabel')}</label>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => update({ scheduleMode: 'recurring', cron: '', runAt: '' })}
                  className={`flex-1 py-2 text-sm rounded-lg border transition-colors font-medium ${
                    formData.scheduleMode === 'recurring'
                      ? 'bg-purple-600 text-white border-purple-600'
                      : 'bg-white text-gray-600 border-gray-300 hover:border-purple-400'
                  }`}
                >
                  {t('form.recurring')}
                </button>
                <button
                  type="button"
                  onClick={() => update({ scheduleMode: 'once', cron: '', runAt: '' })}
                  className={`flex-1 py-2 text-sm rounded-lg border transition-colors font-medium ${
                    formData.scheduleMode === 'once'
                      ? 'bg-orange-500 text-white border-orange-500'
                      : 'bg-white text-gray-600 border-gray-300 hover:border-orange-400'
                  }`}
                >
                  {t('form.runOnce')}
                </button>
              </div>
            </div>
          )}

          {/* Run-once mode: locked badge (edit) */}
          {isEdit && isRunOnce && (
            <div className="flex items-center gap-2 px-3 py-2 bg-white border border-orange-200 rounded-lg text-sm text-orange-700 font-medium">
              <Clock className="w-4 h-4" /> {t('form.runOnce')}
            </div>
          )}

          {/* Once: datetime picker */}
          {(isOnce || isRunOnce) && (
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('form.execTimeLabel')}</label>
              <input
                type="datetime-local"
                value={formData.runAt}
                onChange={(e) => update({ runAt: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-orange-400 outline-none text-sm bg-white"
              />
            </div>
          )}

          {/* Recurring: cron preset + custom input */}
          {!isOnce && !isRunOnce && (
            <>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1.5">
                  {isEdit ? t('form.freqRecurringLabel') : ''}
                </label>
                <select
                  value={
                    CRON_PRESETS.find((p) => p.value === formData.cron && p.value !== '__custom__')
                      ? formData.cron
                      : formData.cron
                      ? '__custom__'
                      : ''
                  }
                  onChange={(e) => {
                    if (e.target.value !== '__custom__') {
                      update({ cron: e.target.value });
                    } else {
                      update({ cron: '' });
                    }
                  }}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg outline-none text-sm bg-white focus:ring-2 focus:ring-purple-500"
                >
                  {!isEdit && <option value="">{t('form.selectFrequency')}</option>}
                  {CRON_PRESETS.filter((p) => p.value !== '__custom__').map((p) => (
                    <option key={p.value} value={p.value}>
                      {t(`cronPresets.${p.key}`)}
                    </option>
                  ))}
                  <option value="__custom__">{t('form.customOption')}</option>
                </select>
              </div>

              {showCustomCron && (
                <input
                  type="text"
                  value={formData.cron}
                  onChange={(e) => update({ cron: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500 outline-none font-mono text-sm"
                  placeholder="0 9 * * 1-5"
                />
              )}

              {effectiveCron && (
                <p className="text-xs text-purple-600 font-medium">{describeCron(effectiveCron)}</p>
              )}

              {/* Timezone + custom description (edit only) */}
              {isEdit && (
                <>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">{t('form.timezoneLabel')}</label>
                    <select
                      value={formData.timezone}
                      onChange={(e) => update({ timezone: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg outline-none text-sm bg-white"
                    >
                      <option value="Asia/Shanghai">{t('form.timezoneShanghai')}</option>
                      <option value="UTC">UTC</option>
                      <option value="America/New_York">America/New_York（UTC-5/4）</option>
                      <option value="Europe/London">Europe/London（UTC+0/1）</option>
                      <option value="Asia/Tokyo">Asia/Tokyo（UTC+9）</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">
                      {t('form.cronDescLabel')}
                      <span className="text-gray-400 font-normal ml-1">{t('form.cronDescHint')}</span>
                    </label>
                    <input
                      type="text"
                      value={formData.cronDescription}
                      onChange={(e) => update({ cronDescription: e.target.value })}
                      placeholder={effectiveCron ? describeCron(effectiveCron) : t('form.cronDescPlaceholder')}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500 outline-none text-sm bg-white"
                    />
                  </div>
                </>
              )}
            </>
          )}
        </div>
      )}

      {/* Execution mode + Agent/Workflow selector */}
      <div className="space-y-2.5">
        <div className="flex items-center gap-3">
          <span className="w-14 shrink-0 text-sm font-medium text-gray-700">{t('form.modeLabel')}</span>
          <PillGroup
            options={[
              { value: 'agent', label: 'Agent', activeClass: 'bg-slate-600 text-white border-slate-600' },
              { value: 'workflow', label: 'Workflow', activeClass: 'bg-purple-600 text-white border-purple-600' },
            ]}
            value={formData.executionMode}
            onChange={(v) => update({ executionMode: v })}
          />
        </div>
        {formData.executionMode === 'agent' ? (
          <div className="flex items-center gap-3">
            <span className="w-14 shrink-0 text-sm font-medium text-gray-700">Agent</span>
            <select
              value={formData.agentName}
              onChange={(e) => update({ agentName: e.target.value })}
              className="flex-1 px-3 py-1.5 border border-gray-300 rounded-lg outline-none text-sm focus:ring-2 focus:ring-slate-400"
            >
              {agents.length === 0 && (
                <option value={formData.agentName}>{formData.agentName || 'rex'}</option>
              )}
              {agents.map((a) => {
                const d = getAgentDisplayDescription(a, i18n.language);
                return (
                  <option key={a.name} value={a.name}>
                    {a.name}{d ? ` — ${d.slice(0, 30)}${d.length > 30 ? '…' : ''}` : ''}
                  </option>
                );
              })}
            </select>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <span className="w-14 shrink-0 text-sm font-medium text-gray-700">Workflow</span>
            <select
              value={formData.workflowID}
              onChange={(e) => update({ workflowID: e.target.value })}
              className="flex-1 px-3 py-1.5 border border-gray-300 rounded-lg outline-none text-sm focus:ring-2 focus:ring-purple-500"
            >
              <option value="">{t('form.selectWorkflow')}</option>
              {workflows.length === 0 && formData.workflowID && (
                <option value={formData.workflowID}>{formData.workflowID}</option>
              )}
              {workflows.map((wf) => (
                <option key={wf.id} value={wf.id}>
                  {wf.name}{wf.description ? ` — ${wf.description.slice(0, 30)}${wf.description.length > 30 ? '…' : ''}` : ''}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* User prompt with Rex assist */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-sm font-medium text-gray-700">
            {t('form.additionalInfoLabel')}
            <span className="text-gray-400 font-normal text-xs ml-1">{t('form.additionalInfoHint')}</span>
          </label>
          <button
            type="button"
            onClick={() =>
              openRex(
                formData.title
                  ? `帮我为「${formData.title}」这个任务写一个详细的执行指令，让 Agent 知道具体要做什么。`
                  : '帮我描述这个任务的具体执行指令，请先问我任务的目标和要求。',
              )
            }
            className="flex items-center gap-1 text-xs text-sky-600 hover:text-sky-700 transition-colors"
          >
            <Sparkles className="w-3.5 h-3.5" />
            {t('form.rexAssist')}
          </button>
        </div>
        <textarea
          value={formData.userPrompt}
          onChange={(e) => update({ userPrompt: e.target.value })}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-slate-400 outline-none resize-none text-sm"
          rows={3}
          placeholder={t('form.additionalInfoPlaceholder')}
        />
      </div>

      {task?.type === 'scheduled' && (
        <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4 space-y-3">
          <p className="text-xs text-slate-600 leading-5">{t('taskSheet.selfContainedHint')}</p>
          <div className="space-y-2">
            <div className="text-sm font-medium text-slate-800">{t('taskSheet.recentRuns')}</div>
            {recordsLoading ? (
              <div className="text-sm text-slate-500">{t('taskSheet.recentRunsLoading')}</div>
            ) : records.length === 0 ? (
              <div className="text-sm text-slate-500">{t('taskSheet.recentRunsEmpty')}</div>
            ) : (
              <div className="space-y-2">
                {records.map((record) => (
                  <div key={record.id} className="rounded-lg border border-slate-200 bg-white px-3 py-2">
                    <div className="flex items-center justify-between gap-3">
                      <StatusBadge status={record.status} />
                      <div className="text-xs text-slate-400">
                        {record.startedAt ? formatTime(record.startedAt) : '--'}
                      </div>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                      {record.durationMs != null && (
                        <span>{formatDuration(record.durationMs)}</span>
                      )}
                      {record.sessionID && (
                        <span className="font-mono text-[11px] text-slate-400">{record.sessionID}</span>
                      )}
                    </div>
                    {record.resultSummary && (
                      <div className="mt-1 text-xs text-slate-600 line-clamp-2">{record.resultSummary}</div>
                    )}
                    {record.error && (
                      <div className="mt-1 text-xs text-red-500 line-clamp-2">{record.error}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Rex context builders ─────────────────────────────────────────────────────

function buildRexContext(formData: TaskFormData, isEdit: boolean): string {
  const isScheduled = formData.type === 'scheduled';
  return [
    `你是一个任务配置专家，正在帮助用户${isEdit ? '修改' : '创建'}一个任务。`,
    ``,
    `**当前配置状态：**`,
    `- 标题：${formData.title || '（未填写）'}`,
    `- 描述：${formData.description || '（未填写）'}`,
    `- 类型：${isScheduled ? '定时任务' : '任务队列'}`,
    `- 优先级：${formData.priority}`,
    `- 执行模式：${formData.executionMode === 'agent' ? `Agent（${formData.agentName || 'rex'}）` : `Workflow（${formData.workflowID || '未指定'}）`}`,
    `- 任务补充信息：${formData.userPrompt || '（未填写）'}`,
    isScheduled ? `- 执行频率：${formData.cron ? describeCron(formData.cron) : '（未配置）'}` : '',
    ``,
    `**字段说明：**`,
    `- **标题**：任务的简短标题`,
    `- **任务补充信息（userPrompt）**：Agent 执行时的具体指令，说明做什么、怎么做、输出什么`,
    `- **类型**：queued（立即进入队列执行）或 scheduled（定时触发）`,
    `- **优先级**：urgent/high/normal/low`,
    `- **执行模式**：agent（使用指定 Agent）或 workflow（运行工作流）`,
    isScheduled ? `- **Cron**：标准5段 cron 表达式，例如 "0 9 * * 1-5" 表示工作日早9点` : '',
    ``,
    `请帮助用户明确任务的目标、执行指令，以及调度安排。`,
  ]
    .filter(Boolean)
    .join('\n');
}

function buildRexWelcome(isEdit: boolean, taskTitle?: string): string {
  if (isEdit) {
    return `你好！我来帮你修改任务「**${taskTitle}**」。

你想调整什么？比如：
- 修改执行指令或目标
- 调整调度时间或频率
- 更换执行的 Agent

告诉我你的需求，配置好后点击「从 Rex 提取配置」填入表单。`;
  }
  return `你好！我来帮你创建一个新任务。

请告诉我：
- 这个任务要**完成什么目标**？
- 需要分析哪些数据、执行什么操作？
- **什么时候执行**？（立即执行一次，还是定期循环，还是在特定时间？）

描述清楚后，我帮你整理成任务配置。完成后点击「从 Rex 提取配置」自动填入表单。`;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function parseJsonFromText(text: string): Record<string, any> | null {
  const fenced = text.match(/```(?:json)?\s*\n([\s\S]+?)\n```/);
  if (fenced) {
    try {
      return JSON.parse(fenced[1]);
    } catch {}
  }
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start !== -1 && end !== -1 && end > start) {
    try {
      return JSON.parse(text.slice(start, end + 1));
    } catch {}
  }
  return null;
}
