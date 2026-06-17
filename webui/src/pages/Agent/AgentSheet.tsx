/**
 * AgentSheet — 统一的 Agent 创建/编辑侧边面板
 *
 * 替代原有的 CreateAgentDialog / EditAgentDialog / AgentFormModal 三个组件，
 * 合并为单一的 EntitySheet 封装，支持：
 * - 表单模式（直接填写字段：名称、描述、System Prompt、模型、温度、Tools、Skills）
 * - Rex 对话模式（自然语言描述 → 一键提取配置到表单）
 * - 工作台模式（通过引导卡片让 Rex 协助创建、编辑和验证配置）
 */

import { useState, useEffect, useMemo } from 'react';
import { Bot, Sparkles, Lock } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { agentAPI, Agent } from '@/api/agent';
import { sessionApi } from '@/api/session';
import client from '@/api/client';
import EntitySheet, { useEntitySheet } from '@/components/common/EntitySheet';
import { buildGuidedCreateGroups } from '@/components/common/GuidedCreatePanel';
import { useRexComposerControls } from '@/components/common/useRexComposerControls';
import PillGroup from '@/components/common/PillGroup';
import { providerAPI, defaultModelAPI, modelV2API } from '@/api/provider';
import { toolAPI, Tool } from '@/api/tool';
import { skillAPI, Skill } from '@/api/skill';
import type { ModelDefinitionV2 } from '@/types';

interface AvailableModel {
  providerID: string;
  providerName: string;
  modelID: string;
  label: string;
}

// ─── Types ────────────────────────────────────────────────────────────────────

interface AgentFormData {
  name: string;
  nameCn: string;
  description: string;
  descriptionCn: string;
  prompt: string;
  temperature: number;
  mode: 'primary' | 'subagent';
  /** "providerID::modelID" or "" for system default */
  modelKey: string;
  tools: string[];
  skills: string[];
}

// ─── AgentSheet ───────────────────────────────────────────────────────────────

interface AgentSheetProps {
  /** null/undefined = 创建模式；传入 Agent 对象 = 编辑模式 */
  agent?: Agent | null;
  onClose: () => void;
  /** 创建或保存成功后调用（父组件刷新列表） */
  onSaved: () => void;
}

export default function AgentSheet({ agent, onClose, onSaved }: AgentSheetProps) {
  const { t } = useTranslation(['agent', 'common']);
  const isEdit = !!agent;
  const isNative = !!agent?.native;

  const [formData, setFormData] = useState<AgentFormData>({
    name: agent?.name ?? '',
    nameCn: agent?.nameCn ?? '',
    description: agent?.description ?? '',
    descriptionCn: agent?.descriptionCn ?? '',
    prompt: agent?.prompt ?? '',
    temperature: agent?.temperature ?? 0.7,
    mode: (agent?.mode as 'primary' | 'subagent') ?? 'subagent',
    modelKey: agent?.model ? `${agent.model.providerID}::${agent.model.modelID}` : '',
    tools: agent?.tools ?? [],
    skills: agent?.skills ?? [],
  });
  const [loading, setLoading] = useState(false);
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [defaultModel, setDefaultModel] = useState<{ providerID: string; modelID: string } | null>(null);
  const [allTools, setAllTools] = useState<Tool[]>([]);
  const [allSkills, setAllSkills] = useState<Skill[]>([]);
  const [toolsLoading, setToolsLoading] = useState(true);
  const [skillsLoading, setSkillsLoading] = useState(true);
  const createGuideGroups = useMemo(() => buildGuidedCreateGroups([
    { title: t('create.guideSectionTitle'), actions: t('create.guideActions', { returnObjects: true }) },
    { title: t('create.caseSectionTitle'), actions: t('create.caseActions', { returnObjects: true }) },
  ]), [t]);
  const editGuideGroups = useMemo(() => {
    const guideActionKey = isNative ? 'edit.nativeGuideActions' : 'edit.guideActions';
    const caseActionKey = isNative ? 'edit.nativeCaseActions' : 'edit.caseActions';
    const name = formData.name || agent?.name || 'Agent';

    return buildGuidedCreateGroups([
      {
        title: t('edit.guideSectionTitle'),
        actions: t(guideActionKey, {
          returnObjects: true,
          name,
        }),
      },
      {
        title: t('edit.caseSectionTitle'),
        actions: t(caseActionKey, {
          returnObjects: true,
          name,
        }),
      },
    ]);
  }, [agent?.name, formData.name, isNative, t]);
  const guideGroups = isEdit ? editGuideGroups : createGuideGroups;
  const rexComposerControls = useRexComposerControls();

  // isPrimary derives from formData.mode so it reacts to mode changes in create mode
  const isPrimary = formData.mode === 'primary';

  useEffect(() => {
    Promise.all([
      providerAPI.list(),
      modelV2API.listDefinitions({ enabled_only: true }),
    ]).then(([providersRes, modelsRes]) => {
      const connectedSet = new Set<string>(providersRes.data.connected ?? []);
      const providerById = new Map(
        providersRes.data.all
          .filter((provider) => connectedSet.has(provider.id))
          .map((provider) => [provider.id, provider]),
      );
      const enabledModels = (modelsRes.data.models ?? []) as ModelDefinitionV2[];
      const list: AvailableModel[] = enabledModels.flatMap((model) => {
        const provider = providerById.get(model.provider_id);
        if (!provider) return [];
        return [{
          providerID: provider.id,
          providerName: provider.name || provider.id,
          modelID: model.id,
          label: model.name || model.id,
        }];
      });
      setAvailableModels(list);
    }).catch(() => setAvailableModels([]))
      .finally(() => setModelsLoaded(true));

    defaultModelAPI.getResolved().then((r) => {
      const d = r.data;
      if (d.provider_id && d.model_id) {
        setDefaultModel({ providerID: d.provider_id, modelID: d.model_id });
      }
    }).catch(() => {});

    toolAPI.list()
      .then((r) => setAllTools(r.data.filter((tool) => tool.enabled)))
      .catch(() => {})
      .finally(() => setToolsLoading(false));

    skillAPI.list().then((r) => {
      const skills = r.data;
      setAllSkills(skills);
      // 主 Agent 且后端未配置任何 skill 时，默认全选
      const currentMode = agent?.mode ?? 'primary';
      if (currentMode === 'primary' && (agent?.skills ?? []).length === 0 && skills.length > 0) {
        setFormData((prev) => ({ ...prev, skills: skills.map((s) => s.name) }));
      }
    }).catch(() => {}).finally(() => setSkillsLoading(false));
  }, []);

  useEffect(() => {
    if (!modelsLoaded || !formData.modelKey) return;
    const selectedStillAvailable = availableModels.some(
      (model) => `${model.providerID}::${model.modelID}` === formData.modelKey,
    );
    if (!selectedStillAvailable) {
      setFormData((prev) => ({ ...prev, modelKey: '' }));
    }
  }, [availableModels, formData.modelKey, modelsLoaded]);

  const submitDisabled = false;

  const handleSubmit = async () => {
    if (!isEdit) {
      // 创建模式通过 AI编辑 tab 完成，表单页只关闭
      onSaved();
      onClose();
      return;
    }
    if (loading) return;
    setLoading(true);
    try {
      const model = formData.modelKey
        ? {
            providerID: formData.modelKey.split('::')[0],
            modelID: formData.modelKey.split('::')[1],
          }
        : undefined;

      if (isNative) {
        await agentAPI.updateModel(agent!.name, model ?? null, formData.temperature);
      } else {
        await agentAPI.update(agent!.name, {
          nameCn: formData.nameCn,
          description: formData.description || undefined,
          descriptionCn: formData.descriptionCn || undefined,
          prompt: formData.prompt,
          temperature: formData.temperature,
          model,
          tools: formData.tools,
          skills: formData.skills,
        });
      }
      onSaved();
      onClose();
    } catch (err: any) {
      alert(t('error.updateFailed', { detail: err.response?.data?.detail ?? err.message }));
    } finally {
      setLoading(false);
    }
  };

  // ── Rex: extract config from conversation ─────────────────────────────────

  const handleExtractFromRex = async (sessionId: string) => {
    const extractPrompt = isNative
      ? `请将以上讨论的内置 Agent 可保存配置整理输出为 JSON，只输出 JSON 对象，不要有任何其他文字。内置 Agent 只支持保存模型和温度，不要输出 description、prompt、tools、skills 等不可保存字段：
\`\`\`json
{
  "model": {
    "providerID": "模型 Provider ID（可选）",
    "modelID": "模型 ID（可选）"
  },
  "temperature": 0.7
}
\`\`\``
      : `请将以上讨论的 Agent 配置整理输出为 JSON，只输出 JSON 对象，不要有任何其他文字：
\`\`\`json
{
  "name": "agent-名称（小写字母、数字和连字符）",
  "name_cn": "中文名称（可选）",
  "description": "简短英文描述（用于委派）",
  "description_cn": "中文界面展示（可选）",
  "prompt": "完整的 System Prompt 内容",
  "model": {
    "providerID": "模型 Provider ID（可选）",
    "modelID": "模型 ID（可选）"
  },
  "temperature": 0.7,
  "mode": "primary 或 subagent",
  "tools": ["工具名称（可选）"],
  "skills": ["Skill 名称（可选）"]
}
\`\`\``;

    await client.post(`/api/session/${sessionId}/prompt_async`, {
      parts: [{ type: 'text', text: extractPrompt }],
    });

    const start = Date.now();
    const lastKnownCount = (await sessionApi.getMessages(sessionId)).length;

    while (Date.now() - start < 60000) {
      await new Promise((r) => setTimeout(r, 1500));
      const messages = await sessionApi.getMessages(sessionId);

      if (messages.length > lastKnownCount) {
        const lastAssistant = [...messages]
          .reverse()
          .find((m: any) => (m.info?.role ?? m.role) === 'assistant' && (m.info?.finish ?? m.finish));

        if (lastAssistant) {
          const text = (lastAssistant.parts ?? [])
            .filter((p: any) => p.type === 'text')
            .map((p: any) => p.text ?? '')
            .join('');

          const config = parseJsonFromText(text);
          if (config) {
            setFormData((prev) => {
              const modelKey = getModelKeyFromConfig(config, prev.modelKey);
              const temperature = typeof config.temperature === 'number' ? config.temperature : prev.temperature;

              if (isNative) {
                return {
                  ...prev,
                  modelKey,
                  temperature,
                };
              }

              return {
                ...prev,
                name: config.name || prev.name,
                nameCn:
                  (typeof config.name_cn === 'string'
                    ? config.name_cn
                    : typeof config.nameCn === 'string'
                      ? config.nameCn
                      : prev.nameCn),
                description: config.description ?? prev.description,
                descriptionCn:
                  (typeof config.description_cn === 'string'
                    ? config.description_cn
                    : typeof config.descriptionCn === 'string'
                      ? config.descriptionCn
                      : prev.descriptionCn),
                prompt: config.prompt || prev.prompt,
                modelKey,
                temperature,
                mode:
                  config.mode === 'primary' || config.mode === 'subagent'
                    ? config.mode
                    : prev.mode,
                tools: Array.isArray(config.tools)
                  ? config.tools.filter((tool: unknown): tool is string => typeof tool === 'string')
                  : prev.tools,
                skills: Array.isArray(config.skills)
                  ? config.skills.filter((skill: unknown): skill is string => typeof skill === 'string')
                  : prev.skills,
              };
            });
            return;
          }
        }
      }
    }

    throw new Error(t('error.extractTimeout'));
  };

  return (
    <EntitySheet
      open
      mode={isEdit ? 'edit' : 'create'}
      entityType="Agent"
      entityName={agent?.name}
      icon={<Bot className="w-5 h-5" />}
      rexSystemContext={buildRexContext(formData, isEdit, isNative)}
      rexWelcomeMessage={buildRexWelcome(isEdit, agent?.name, isNative)}
      rexGuideGroups={guideGroups}
      rexGuidePanelTitle={isEdit ? t('edit.guidePanelTitle') : t('create.guidePanelTitle')}
      rexGuidePanelDesc={isEdit
        ? t(isNative ? 'edit.nativeGuidePanelDesc' : 'edit.guidePanelDesc', { name: agent?.name ?? formData.name })
        : t('create.guidePanelDesc')}
      rexGuideEmptyTitle={isEdit ? t('edit.emptyStateTitle') : t('create.emptyStateTitle')}
      rexGuideIcon={<Bot className="h-5 w-5" />}
      initialTab={isEdit ? 'rex' : undefined}
      rexSessionStorageKey={isEdit && agent?.name ? `agent-edit:${agent.name}` : undefined}
      {...rexComposerControls}
      submitDisabled={submitDisabled}
      submitLoading={loading}
      submitLabel={isEdit ? undefined : t('sheet.done')}
      hideForm={!isEdit}
      onClose={onClose}
      onSubmit={handleSubmit}
      onExtractFromRex={isEdit ? handleExtractFromRex : undefined}
    >
      <AgentFormContent
        formData={formData}
        onChange={setFormData}
        nameEditable={!isEdit}
        nativeReadOnly={isNative}
        availableModels={availableModels}
        defaultModel={defaultModel}
        allTools={allTools}
        allSkills={allSkills}
        toolsLoading={toolsLoading}
        skillsLoading={skillsLoading}
        isPrimary={isPrimary}
      />
    </EntitySheet>
  );
}

// ─── AgentFormContent ─────────────────────────────────────────────────────────

interface AgentFormContentProps {
  formData: AgentFormData;
  onChange: (data: AgentFormData) => void;
  nameEditable: boolean;
  /** 内置 Agent 只读：除模型和温度外的所有字段不可编辑 */
  nativeReadOnly?: boolean;
  availableModels: AvailableModel[];
  defaultModel: { providerID: string; modelID: string } | null;
  allTools: Tool[];
  allSkills: Skill[];
  toolsLoading?: boolean;
  skillsLoading?: boolean;
  isPrimary: boolean;
}

function AgentFormContent({
  formData,
  onChange,
  nameEditable,
  nativeReadOnly = false,
  availableModels,
  defaultModel,
  allTools,
  allSkills,
  toolsLoading = false,
  skillsLoading = false,
  isPrimary,
}: AgentFormContentProps) {
  const { t } = useTranslation('agent');
  const { openRex } = useEntitySheet();
  const update = (fields: Partial<AgentFormData>) => onChange({ ...formData, ...fields });

  const modelsByProvider = availableModels.reduce<Record<string, AvailableModel[]>>((acc, m) => {
    if (!acc[m.providerID]) acc[m.providerID] = [];
    acc[m.providerID].push(m);
    return acc;
  }, {});
  const providerLabelById = availableModels.reduce<Record<string, string>>((acc, m) => {
    acc[m.providerID] = m.providerName;
    return acc;
  }, {});

  const defaultModelLabel = defaultModel
    ? `${defaultModel.modelID} (${defaultModel.providerID})`
    : t('form.systemDefault');

  const toolsByCategory = allTools.reduce<Record<string, Tool[]>>((acc, tool) => {
    const cat = tool.category || t('form.otherCategory');
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(tool);
    return acc;
  }, {});

  const toggleTool = (name: string) => {
    const selected = formData.tools.includes(name)
      ? formData.tools.filter((toolName) => toolName !== name)
      : [...formData.tools, name];
    update({ tools: selected });
  };

  const toggleSkill = (name: string) => {
    const selected = formData.skills.includes(name)
      ? formData.skills.filter((skillName) => skillName !== name)
      : [...formData.skills, name];
    update({ skills: selected });
  };

  return (
    <div className="space-y-4">
      {/* Native read-only banner */}
      {nativeReadOnly && (
        <div className="flex items-center gap-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700">
          <Lock className="w-3.5 h-3.5 shrink-0" />
          {t('form.nativeReadOnlyMessage')}
        </div>
      )}

      {/* Name */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          {t('form.name')} {nameEditable && <span className="text-slate-500">*</span>}
        </label>
        {nameEditable ? (
          <input
            type="text"
            value={formData.name}
            onChange={(e) => update({ name: e.target.value })}
            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
            placeholder="my-agent"
          />
        ) : (
          <div className="px-4 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-700 font-mono">
            {formData.name}
          </div>
        )}
      </div>

      {/* Chinese display name */}
      <div>
        <label className={`block text-sm font-medium mb-1 ${nativeReadOnly ? 'text-gray-500' : 'text-gray-700'}`}>
          {t('form.nameCn')}
        </label>
        <input
          type="text"
          value={formData.nameCn}
          onChange={(e) => update({ nameCn: e.target.value })}
          disabled={nativeReadOnly}
          className={`w-full px-4 py-2 border rounded-lg text-sm ${
            nativeReadOnly
              ? 'border-gray-300 bg-gray-100 text-gray-500 cursor-not-allowed'
              : 'border-gray-300 focus:outline-none focus:ring-2 focus:ring-slate-400'
          }`}
          placeholder={t('form.nameCnPlaceholder')}
        />
      </div>

      {/* Description (English) + Chinese UI */}
      <div>
        <label className={`block text-sm font-medium mb-1 ${nativeReadOnly ? 'text-gray-500' : 'text-gray-700'}`}>{t('form.description')}</label>
        <p className="text-xs text-gray-500 mb-1">{t('form.descriptionHint')}</p>
        <input
          type="text"
          value={formData.description}
          onChange={(e) => update({ description: e.target.value })}
          disabled={nativeReadOnly}
          className={`w-full px-4 py-2 border rounded-lg text-sm ${
            nativeReadOnly
              ? 'border-gray-300 bg-gray-100 text-gray-500 cursor-not-allowed'
              : 'border-gray-300 focus:outline-none focus:ring-2 focus:ring-slate-400'
          }`}
          placeholder={t('form.descriptionPlaceholder')}
        />
      </div>
      <div>
        <label className={`block text-sm font-medium mb-1 ${nativeReadOnly ? 'text-gray-500' : 'text-gray-700'}`}>{t('form.descriptionCn')}</label>
        <input
          type="text"
          value={formData.descriptionCn}
          onChange={(e) => update({ descriptionCn: e.target.value })}
          disabled={nativeReadOnly}
          className={`w-full px-4 py-2 border rounded-lg text-sm ${
            nativeReadOnly
              ? 'border-gray-300 bg-gray-100 text-gray-500 cursor-not-allowed'
              : 'border-gray-300 focus:outline-none focus:ring-2 focus:ring-slate-400'
          }`}
          placeholder={t('form.descriptionCnPlaceholder')}
        />
      </div>

      {/* System Prompt with Rex assist */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className={`text-sm font-medium ${nativeReadOnly ? 'text-gray-500' : 'text-gray-700'}`}>
            System Prompt {!nativeReadOnly && <span className="text-slate-500">*</span>}
          </label>
          {!nativeReadOnly && (
            <button
              type="button"
              onClick={() => openRex()}
              className="flex items-center gap-1 text-xs text-sky-700 hover:text-sky-900 transition-colors"
            >
              <Sparkles className="w-3.5 h-3.5" />
              {t('form.rexAssistWrite')}
            </button>
          )}
        </div>
        <textarea
          value={formData.prompt}
          onChange={(e) => update({ prompt: e.target.value })}
          disabled={nativeReadOnly}
          className={`w-full h-40 px-4 py-3 border rounded-lg resize-none font-mono text-sm ${
            nativeReadOnly
              ? 'border-gray-300 bg-gray-100 text-gray-500 cursor-not-allowed'
              : 'border-gray-300 focus:outline-none focus:ring-2 focus:ring-slate-400'
          }`}
          placeholder="You are a helpful assistant..."
        />
      </div>

      {/* Mode (create only) + Model + Temperature */}
      <div className="space-y-2.5">
        {/* Mode: 创建时固定为 subagent，编辑时显示当前模式（只读） */}
        {!nameEditable && (
          <div className="flex items-center gap-3">
            <span className="w-14 shrink-0 text-sm font-medium text-gray-700">{t('form.mode')}</span>
            <PillGroup
              options={[
                { value: 'primary', label: t('form.primaryModeLabel'), activeClass: 'bg-sky-600 text-white border-sky-600' },
                { value: 'subagent', label: t('form.subagentModeLabel'), activeClass: 'bg-purple-600 text-white border-purple-600' },
              ]}
              value={formData.mode}
              onChange={() => {}}
              disabled
            />
          </div>
        )}

        <div className="flex items-center gap-3">
          <span className="w-14 shrink-0 text-sm font-medium text-gray-700">{t('form.model')}</span>
          <select
            value={formData.modelKey}
            onChange={(e) => update({ modelKey: e.target.value })}
            className="flex-1 px-3 py-1.5 border border-gray-300 rounded-lg outline-none text-sm focus:ring-2 focus:ring-slate-400"
          >
            <option value="">— {defaultModelLabel} —</option>
            {Object.entries(modelsByProvider).map(([pID, pModels]) => (
              <optgroup key={pID} label={providerLabelById[pID] || pID}>
                {pModels.map((m) => (
                  <option key={`${m.providerID}::${m.modelID}`} value={`${m.providerID}::${m.modelID}`}>
                    {m.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-3">
          <span className="w-14 shrink-0 text-sm font-medium text-gray-700">{t('form.temperature')}</span>
          <input
            type="number"
            min="0"
            max="2"
            step="0.1"
            value={formData.temperature}
            onChange={(e) => update({ temperature: parseFloat(e.target.value) })}
            className="w-28 px-3 py-1.5 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
          />
        </div>

      </div>

      {/* Tools */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <label className={`text-sm font-medium ${nativeReadOnly ? 'text-gray-500' : 'text-gray-700'}`}>
            Tools
            {formData.tools.length > 0 && (
              <span className={`ml-2 px-1.5 py-0.5 text-xs rounded-full font-normal ${nativeReadOnly ? 'bg-gray-200 text-gray-500' : 'bg-slate-100 text-slate-700'}`}>
                {t('form.selected', { count: formData.tools.length })}
              </span>
            )}
          </label>
          {formData.tools.length > 0 && !nativeReadOnly && (
            <button
              type="button"
              onClick={() => update({ tools: [] })}
              className="text-xs text-gray-400 hover:text-gray-600 transition-colors"
            >
              {t('form.clearSelection')}
            </button>
          )}
        </div>
        {toolsLoading ? (
          <p className="text-sm text-gray-400 py-2 animate-pulse">{t('form.loadingTools')}</p>
        ) : allTools.length === 0 ? (
          <p className="text-sm text-gray-400 py-2">{t('form.noTools')}</p>
        ) : (
          <div className={`border rounded-lg divide-y max-h-80 overflow-y-auto pr-3 ${nativeReadOnly ? 'border-gray-300 bg-gray-100 select-none divide-gray-200' : 'border-gray-200 divide-gray-100'}`}>
            <label className={`flex items-center gap-3 px-3 py-2 sticky top-0 z-10 ${nativeReadOnly ? 'bg-gray-100 cursor-not-allowed' : 'bg-gray-50 cursor-pointer hover:bg-gray-100 transition-colors'}`}>
              <input
                type="checkbox"
                checked={formData.tools.length === allTools.length}
                disabled={nativeReadOnly}
                ref={(el) => {
                  if (el) el.indeterminate = formData.tools.length > 0 && formData.tools.length < allTools.length;
                }}
                onChange={() => {
                  update({ tools: formData.tools.length === allTools.length ? [] : allTools.map((tool) => tool.name) });
                }}
                className="h-4 w-4 rounded border-gray-300 text-slate-600 focus:ring-slate-400 shrink-0"
              />
              <span className={`text-sm font-medium ${nativeReadOnly ? 'text-gray-500' : 'text-gray-600'}`}>{t('form.selectAll')}</span>
            </label>
            {Object.entries(toolsByCategory).map(([category, tools]) => (
              <div key={category}>
                <div className={`px-3 py-1.5 text-xs font-medium uppercase tracking-wide ${nativeReadOnly ? 'bg-gray-100 text-gray-400' : 'bg-gray-50 text-gray-500'}`}>
                  {category}
                </div>
                {tools.map((tool) => {
                  const checked = formData.tools.includes(tool.name);
                  return (
                    <label
                      key={tool.name}
                      className={`flex items-start gap-3 px-3 py-2 ${
                        nativeReadOnly
                          ? 'cursor-not-allowed bg-gray-100'
                          : `cursor-pointer hover:bg-gray-50 transition-colors ${checked ? 'bg-sky-50/60' : ''}`
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={nativeReadOnly}
                        onChange={() => toggleTool(tool.name)}
                        className="mt-0.5 h-4 w-4 rounded border-gray-300 text-slate-600 focus:ring-slate-400 shrink-0"
                      />
                      <div className="min-w-0">
                        <span className={`text-sm font-medium ${nativeReadOnly ? 'text-gray-500' : 'text-gray-800'}`}>{tool.name}</span>
                        {tool.description && (
                          <p className={`text-xs mt-0.5 leading-snug line-clamp-1 ${nativeReadOnly ? 'text-gray-400' : 'text-gray-500'}`}>
                            {tool.description}
                          </p>
                        )}
                      </div>
                    </label>
                  );
                })}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Skills — 仅主 Agent 可见 */}
      {isPrimary && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className={`text-sm font-medium ${nativeReadOnly ? 'text-gray-500' : 'text-gray-700'}`}>
              Skills
              {formData.skills.length > 0 && (
                <span className={`ml-2 px-1.5 py-0.5 text-xs rounded-full font-normal ${nativeReadOnly ? 'bg-gray-200 text-gray-500' : 'bg-purple-100 text-purple-700'}`}>
                  {t('form.selected', { count: formData.skills.length })}
                </span>
              )}
            </label>
            {formData.skills.length > 0 && !nativeReadOnly && (
              <button
                type="button"
                onClick={() => update({ skills: [] })}
                className="text-xs text-gray-400 hover:text-gray-600 transition-colors"
              >
                {t('form.clearSelection')}
              </button>
            )}
          </div>
          {skillsLoading ? (
            <p className="text-sm text-gray-400 py-2 animate-pulse">{t('form.loadingSkills')}</p>
          ) : allSkills.length === 0 ? (
            <p className="text-sm text-gray-400 py-2">{t('form.noSkills')}</p>
          ) : (
            <div className={`border rounded-lg divide-y max-h-64 overflow-y-auto pr-3 ${nativeReadOnly ? 'border-gray-300 bg-gray-100 select-none divide-gray-200' : 'border-gray-200 divide-gray-100'}`}>
              <label className={`flex items-center gap-3 px-3 py-2 sticky top-0 z-10 ${nativeReadOnly ? 'bg-gray-100 cursor-not-allowed' : 'bg-gray-50 cursor-pointer hover:bg-gray-100 transition-colors'}`}>
                <input
                  type="checkbox"
                  checked={formData.skills.length === allSkills.length}
                  disabled={nativeReadOnly}
                  ref={(el) => {
                    if (el) el.indeterminate = formData.skills.length > 0 && formData.skills.length < allSkills.length;
                  }}
                  onChange={() => {
                    update({ skills: formData.skills.length === allSkills.length ? [] : allSkills.map((s) => s.name) });
                  }}
                  className="h-4 w-4 rounded border-gray-300 text-purple-600 focus:ring-purple-500 shrink-0"
                />
                <span className={`text-sm font-medium ${nativeReadOnly ? 'text-gray-500' : 'text-gray-600'}`}>{t('form.selectAll')}</span>
              </label>
              {allSkills.map((skill) => {
                const checked = formData.skills.includes(skill.name);
                return (
                  <label
                    key={skill.name}
                    className={`flex items-start gap-3 px-3 py-2 ${
                      nativeReadOnly
                        ? 'cursor-not-allowed bg-gray-100'
                        : `cursor-pointer hover:bg-gray-50 transition-colors ${checked ? 'bg-purple-50/50' : ''}`
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={nativeReadOnly}
                      onChange={() => toggleSkill(skill.name)}
                      className="mt-0.5 h-4 w-4 rounded border-gray-300 text-purple-600 focus:ring-purple-500 shrink-0"
                    />
                    <div className="min-w-0">
                      <span className={`text-sm font-medium ${nativeReadOnly ? 'text-gray-500' : 'text-gray-800'}`}>{skill.name}</span>
                      {skill.description && (
                        <p className={`text-xs mt-0.5 leading-snug line-clamp-2 ${nativeReadOnly ? 'text-gray-400' : 'text-gray-500'}`}>
                          {skill.description}
                        </p>
                      )}
                    </div>
                  </label>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Rex context builders ─────────────────────────────────────────────────────

function buildRexContext(formData: AgentFormData, isEdit: boolean, isNative = false): string {
  if (!isEdit) {
    return `你是 Agent 创建助手。用户希望通过对话来创建一个新的子 Agent。

请先加载并遵守项目内 .flocks/plugins/skills/agent-builder（agent-builder skill），再根据用户需求生成子 Agent 配置文件（agent.yaml + prompt.md），保存到 ~/.flocks/plugins/agents/<name>/ 目录。

**创建流程：**
1. 先确认用户需求：Agent 名称、职责、能力边界、执行模式
2. 生成 prompt.md 和 agent.yaml，目录名必须与 Agent name 一致
3. 验证 YAML、目录结构、名称唯一性和工具名

**重要约束：**
- 必须先加载 .flocks/plugins/skills/agent-builder
- Agent 名称必须是 kebab-case 格式
- 如果用户提供中文名称，请写入 name_cn 字段
- mode 固定为 subagent
- 文件必须写入 ~/.flocks/plugins/agents/<name>/，禁止创建 agents/<name>.yaml 这类扁平文件
- 新 Agent 优先使用 tools: allowlist，不要随意使用 permission 通配规则
- 不要与内置 Agent 名称冲突

请先引导用户描述需求，如果信息不够清晰可适当追问，然后一次性生成所有文件。`;
  }

  const promptPreview =
    formData.prompt.length > 200
      ? formData.prompt.slice(0, 200) + '...'
      : formData.prompt;
  const toolsSummary = formData.tools.length > 0 ? formData.tools.join(', ') : '（未配置）';
  const skillsSummary = formData.skills.length > 0 ? formData.skills.join(', ') : '（未配置）';
  const modelSummary = formData.modelKey || '（系统默认）';
  const editableFieldLines = isNative
    ? [
        `**可保存字段说明：**`,
        `- **模型**：可建议切换到更适合当前职责的模型，但需要给出 Provider ID 和 Model ID 才能自动提取。`,
        `- **温度**：0-2，值越低越精准保守（安全分析推荐 0.2-0.5），越高越有创意。`,
        `- **验证建议**：可以设计测试输入、预期输出和失败判据，帮助用户保存后验证效果。`,
        `- 内置 Agent 的描述、System Prompt、Tools、Skills 由系统维护，不要建议提取或覆盖这些字段。`,
      ]
    : [
        `**可编辑字段说明：**`,
        `- **描述**：简短说明 Agent 的用途，英文描述用于委派和模型上下文。`,
        `- **System Prompt**：Agent 的核心指令，决定其行为、能力边界、输出格式和风格。`,
        `- **模型 / 温度**：模型决定能力边界；温度 0-2，值越低越精准保守，越高越有创意。`,
        `- **Tools / Skills**：只保留任务确实需要的能力，优先使用最小权限。`,
        `- **模式**：仅展示当前类型；编辑时不要建议修改 primary/subagent 模式。`,
      ];

  return [
    `你是一个 Agent 编辑引导助手，正在帮助用户修改一个已有 AI Agent。`,
    `你的目标不是直接大改配置，而是先理解当前配置、追问修改意图，再给出可应用到表单的修改方案。`,
    isNative
      ? `这是内置 Agent：只能保存模型和温度。若用户要求修改职责、Prompt、Tools 或 Skills，请明确说明这些字段当前不可保存，只能给出验证建议或外部配置建议。`
      : `如果用户希望改动 Agent 文件或重新生成 prompt.md/agent.yaml，请先加载并遵守项目内 .flocks/plugins/skills/agent-builder（agent-builder skill）。`,
    ``,
    `**当前配置状态：**`,
    `- 名称：${formData.name || '（未填写）'}`,
    `- 描述（英文）：${formData.description || '（未填写）'}`,
    `- 描述（中文）：${formData.descriptionCn || '（未填写）'}`,
    `- System Prompt：${promptPreview || '（未填写）'}`,
    `- 模型：${modelSummary}`,
    `- 温度：${formData.temperature}`,
    `- 模式：${formData.mode === 'primary' ? 'Primary（主 Agent）' : 'Subagent（子 Agent）'}`,
    `- Tools：${toolsSummary}`,
    `- Skills：${skillsSummary}`,
    `- 是否内置 Agent：${isNative ? '是。内置 Agent 仅支持修改模型和温度，其他配置由系统维护。' : '否，可修改描述、Prompt、温度、Tools 和 Skills。'}`,
    ``,
    `**编辑流程：**`,
    `1. 先确认用户想解决的问题：职责变更、行为风格、工具权限、输出格式或模型参数。`,
    `2. 对照当前配置说明建议修改哪些字段，并指出不建议修改的边界。`,
    `3. 必要时一次只问一个关键问题，避免一开始就输出大段配置。`,
    `4. 用户确认后，输出可被「从 Rex 提取配置」解析的 JSON 配置摘要。`,
    ``,
    ...editableFieldLines,
    ``,
    `配置完成后，用户会点击引导按钮「从 Rex 提取配置」，将配置自动填入表单。届时你会被要求只输出 JSON，请确保 JSON 格式正确。`,
  ].join('\n');
}

function buildRexWelcome(isEdit: boolean, agentName?: string, isNative = false): string {
  if (isEdit) {
    if (isNative) {
      return `你好！我来帮你修改内置 Agent **${agentName}** 的可保存配置。

你可以从下方选择一个编辑入口，也可以直接描述你想调整的地方，比如：

- 检查当前模型是否适合这个 Agent
- 调低温度，让输出更稳定保守
- 设计测试输入和验收标准

注意：这是内置 Agent，当前只支持保存模型和温度。`;
    }

    return `你好！我来帮你修改 Agent **${agentName}** 的配置。

你可以从下方选择一个编辑入口，也可以直接描述你想改的地方，比如：

- 调整职责边界或委派触发条件
- 优化 System Prompt 和输出格式
- 收敛工具 / Skill 权限
- 调整温度并验证效果

配置好后，点击引导按钮「从 Rex 提取配置」即可自动填入表单。`;
  }
  return `你好！我来帮你创建一个新的子 Agent。

请告诉我你需要什么样的 Agent，比如：

- **名称**：如 \`threat-analyst\`（小写 + 短横线）
- **职责**：这个 Agent 负责做什么
- **能力范围**：它需要访问哪些工具（只读分析 / 代码执行 / 网络搜索等）

描述越清晰，生成的 Agent 越准确。`;
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

function getModelKeyFromConfig(config: Record<string, any>, fallback: string): string {
  if (typeof config.modelKey === 'string' && config.modelKey.includes('::')) {
    return config.modelKey;
  }

  const model = config.model;
  if (!model || typeof model !== 'object') {
    return fallback;
  }

  const raw = model as Record<string, unknown>;
  const providerID = typeof raw.providerID === 'string'
    ? raw.providerID
    : typeof raw.provider_id === 'string'
      ? raw.provider_id
      : '';
  const modelID = typeof raw.modelID === 'string'
    ? raw.modelID
    : typeof raw.model_id === 'string'
      ? raw.model_id
      : '';

  return providerID && modelID ? `${providerID}::${modelID}` : fallback;
}
