/**
 * AgentSheet — 统一的 Agent 创建/编辑侧边面板
 *
 * 替代原有的 CreateAgentDialog / EditAgentDialog / AgentFormModal 三个组件，
 * 合并为单一的 EntitySheet 封装，支持：
 * - 表单模式（直接填写字段：名称、描述、System Prompt、模型、温度、Tools、Skills）
 * - Rex 对话模式（自然语言描述 → 一键提取配置到表单）
 * - 测试模式（在编辑时直接向 Agent 发消息验证效果）
 */

import { useState, useEffect } from 'react';
import { Bot, Sparkles, Lock } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { agentAPI, Agent } from '@/api/agent';
import { sessionApi } from '@/api/session';
import client from '@/api/client';
import EntitySheet, { useEntitySheet } from '@/components/common/EntitySheet';
import PillGroup from '@/components/common/PillGroup';
import { providerAPI, defaultModelAPI } from '@/api/provider';
import { toolAPI, Tool } from '@/api/tool';
import { skillAPI, Skill } from '@/api/skill';

interface AvailableModel {
  providerID: string;
  modelID: string;
  label: string;
}

// ─── Types ────────────────────────────────────────────────────────────────────

interface AgentFormData {
  name: string;
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
  const { t } = useTranslation('agent');
  const isEdit = !!agent;

  const [formData, setFormData] = useState<AgentFormData>({
    name: agent?.name ?? '',
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
  const [defaultModel, setDefaultModel] = useState<{ providerID: string; modelID: string } | null>(null);
  const [allTools, setAllTools] = useState<Tool[]>([]);
  const [allSkills, setAllSkills] = useState<Skill[]>([]);
  const [toolsLoading, setToolsLoading] = useState(true);
  const [skillsLoading, setSkillsLoading] = useState(true);

  // isPrimary derives from formData.mode so it reacts to mode changes in create mode
  const isPrimary = formData.mode === 'primary';

  useEffect(() => {
    providerAPI.list().then((r) => {
      const connectedSet = new Set<string>(r.data.connected ?? []);
      const list: AvailableModel[] = [];
      for (const provider of r.data.all) {
        if (!connectedSet.has(provider.id)) continue;
        for (const [modelId, modelInfo] of Object.entries(provider.models ?? {})) {
          list.push({ providerID: provider.id, modelID: modelId, label: (modelInfo as any).name || modelId });
        }
      }
      setAvailableModels(list);
    }).catch(() => {});

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

  const isNative = !!agent?.native;
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

  // ── Test: run agent with a prompt ─────────────────────────────────────────

  const handleRunTest = async (prompt: string): Promise<string> => {
    const res = await agentAPI.test(agent!.name, prompt);
    return res.data.sessionId;
  };

  // ── Rex: extract config from conversation ─────────────────────────────────

  const handleExtractFromRex = async (sessionId: string) => {
    const extractPrompt = `请将以上讨论的 Agent 配置整理输出为 JSON，只输出 JSON 对象，不要有任何其他文字：
\`\`\`json
{
  "name": "agent-名称（小写字母、数字和连字符）",
  "description": "简短英文描述（用于委派）",
  "description_cn": "中文界面展示（可选）",
  "prompt": "完整的 System Prompt 内容",
  "temperature": 0.7,
  "mode": "primary 或 subagent"
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
            setFormData((prev) => ({
              ...prev,  // preserve tools, skills, modelKey
              name: config.name || prev.name,
              description: config.description ?? prev.description,
              descriptionCn:
                (typeof config.description_cn === 'string'
                  ? config.description_cn
                  : typeof config.descriptionCn === 'string'
                    ? config.descriptionCn
                    : prev.descriptionCn),
              prompt: config.prompt || prev.prompt,
              temperature:
                typeof config.temperature === 'number' ? config.temperature : prev.temperature,
              mode:
                config.mode === 'primary' || config.mode === 'subagent'
                  ? config.mode
                  : prev.mode,
            }));
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
      rexSystemContext={buildRexContext(formData, isEdit)}
      rexWelcomeMessage={buildRexWelcome(isEdit, agent?.name)}
      submitDisabled={submitDisabled}
      submitLoading={loading}
      submitLabel={isEdit ? undefined : t('sheet.done')}
      hideForm={!isEdit}
      onClose={onClose}
      onSubmit={handleSubmit}
      onExtractFromRex={isEdit ? handleExtractFromRex : undefined}
      onRunTest={isEdit ? handleRunTest : undefined}
      defaultTestPrompt="你好，请介绍一下你自己以及你的主要功能。"
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
          {t('form.nativeReadOnlyPrefix')}<span className="font-semibold">{t('form.model')}</span>{t('form.nativeReadOnlyAnd')}<span className="font-semibold">{t('form.temperature')}</span>{t('form.nativeReadOnlySuffix')}
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
              <optgroup key={pID} label={pID}>
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

function buildRexContext(formData: AgentFormData, isEdit: boolean): string {
  if (!isEdit) {
    return `你是 Agent 创建助手。用户希望通过对话来创建一个新的子 Agent。

请使用 agent-builder skill 根据用户的需求生成子 Agent 配置文件（YAML + prompt 文件），保存到 ~/.flocks/plugins/agents/ 目录。

**创建流程：**
1. 先确认用户需求：Agent 名称、职责、能力边界、执行模式
2. 生成 prompt 文件（.prompt.md）和配置文件（.yaml）
3. 验证文件正确性

**重要约束：**
- Agent 名称必须是 kebab-case 格式
- mode 固定为 subagent
- 文件必须写入 ~/.flocks/plugins/agents/
- 不要与内置 Agent 名称冲突

请先引导用户描述需求，如果信息不够清晰可适当追问，然后一次性生成所有文件。`;
  }

  const promptPreview =
    formData.prompt.length > 200
      ? formData.prompt.slice(0, 200) + '...'
      : formData.prompt;

  return [
    `你是一个 Agent 配置专家，正在帮助用户修改一个 AI Agent。`,
    ``,
    `**当前配置状态：**`,
    `- 名称：${formData.name || '（未填写）'}`,
    `- 描述（英文）：${formData.description || '（未填写）'}`,
    `- 描述（中文）：${formData.descriptionCn || '（未填写）'}`,
    `- System Prompt：${promptPreview || '（未填写）'}`,
    `- 温度：${formData.temperature}`,
    `- 模式：${formData.mode === 'primary' ? 'Primary（主 Agent）' : 'Subagent（子 Agent）'}`,
    ``,
    `**Agent 字段说明：**`,
    `- **名称**：小写字母、数字和连字符，是 Agent 的唯一标识符`,
    `- **描述**：简短说明 Agent 的用途（30字以内）`,
    `- **System Prompt**：Agent 的核心指令，决定其行为、能力和风格`,
    `- **温度**：0-2，值越低越精准保守（安全分析推荐 0.2-0.5），越高越有创意`,
    `- **模式**：primary 直接与用户交互；subagent 由主 Agent 调用`,
    ``,
    `请根据用户的描述帮助他们修改 Agent 配置。`,
    `配置完成后，用户可以点击「从 Rex 提取配置」按钮，将配置自动填入表单。`,
    `届时你会被要求以 JSON 格式输出配置摘要，请确保 JSON 格式正确。`,
  ].join('\n');
}

function buildRexWelcome(isEdit: boolean, agentName?: string): string {
  if (isEdit) {
    return `你好！我来帮你修改 Agent **${agentName}** 的配置。

你可以告诉我：
- 想调整 System Prompt 的哪些部分？
- 需要改变 Agent 的行为风格？
- 温度或其他参数需要调整？

描述你的需求，我来帮你完善配置。配置好后，点击底部「从 Rex 提取配置」即可自动填入表单。`;
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
