/**
 * SkillSheet — unified Skill create/edit/view side panel
 *
 * Wraps EntitySheet and supports:
 * - View mode (read-only: name, description, content)
 * - Form mode (direct field editing: name, description, content)
 * - Rex chat mode (natural language → extract config into form)
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { BookOpen, Lock, Pencil, Eye, Save, Loader2, Trash2 } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { skillAPI, Skill } from '@/api/skill';
import { useToast } from '@/components/common/Toast';
import EntitySheet from '@/components/common/EntitySheet';
import { getLocalizedSkillDescription } from './skillDisplay';

interface SkillFormData {
  name: string;
  description: string;
  descriptionCn: string;
  content: string;
}

function buildRexContext(isEdit: boolean, formData: SkillFormData): string {
  if (!isEdit) {
    return `你是 Skill 创建助手。用户希望通过对话来创建一个新的 Skill。

**Skill 文件规范（必须严格遵守）：**
- 每个 Skill 是一个独立目录，目录名即 skill 名称（kebab-case，如 \`my-skill\`）
- 文件路径：\`~/.flocks/plugins/skills/<skill-name>/SKILL.md\`（优先写到用户全局目录）
  - 若需写到项目目录则使用：\`./.flocks/plugins/skills/<skill-name>/SKILL.md\`
  - **注意：不要写到 \`.flocks/skills/\`（那是内置 skill 目录，不应由用户创建）**
- 文件必须以 YAML front matter 开头，格式如下：
  \`\`\`
  ---
  name: <skill-name>
  description: <一句话描述>
  ---
  
  # 正文内容（Markdown 格式）
  \`\`\`

**创建流程：**
1. 先确认用户需求：Skill 名称（kebab-case）、描述、主要功能
2. 生成完整的 SKILL.md 内容（含 front matter + Markdown 正文）
3. 用 \`write\` 工具写入文件（路径：\`~/.flocks/plugins/skills/<name>/SKILL.md\`）`;
  }

  return `你是 Skill 优化助手，正在帮助用户修改技能「${formData.name}」。

**当前 Skill 信息：**
- 名称：${formData.name}
- 描述：${formData.description || '（无描述）'}
- 中文描述：${formData.descriptionCn || '（无中文描述）'}
- 内容预览：${formData.content.slice(0, 200)}${formData.content.length > 200 ? '...' : ''}

请根据用户的需求帮助他们改进技能内容和描述。`;
}

function buildRexWelcome(isEdit: boolean, skillName?: string): string {
  if (!isEdit) {
    return `你好！我来帮你创建一个新的 Skill。

请告诉我你需要什么样的 Skill，比如：

- **名称**：如 \`code-audit\`（小写 + 短横线）
- **描述**：这个 Skill 负责做什么
- **功能**：它的主要能力是什么

描述越详细，生成的 Skill 越准确。`;
  }

  return `你好！我来帮你优化技能「**${skillName}**」。

你可以告诉我：
- 想修改哪些内容或说明？
- 需要添加什么新功能？
- 描述或结构有什么问题？

描述你的需求，我来帮你改进。`;
}

interface SkillSheetProps {
  skill?: Skill | null;
  onClose: () => void;
  onSaved: () => void;
  onDeleted?: () => void;
}

export default function SkillSheet({ skill, onClose, onSaved, onDeleted }: SkillSheetProps) {
  const { t, i18n } = useTranslation('skill');
  const toast = useToast();
  const isEdit = !!skill;
  // Custom skills (source !== 'project') are editable and deletable
  const isUserSkill = isEdit && skill.source !== 'project';
  const isReadonly = isEdit && !isUserSkill;
  const localizedDescription = getLocalizedSkillDescription(skill, i18n.language);

  // Strip YAML front matter — name/description are already shown as separate fields
  const stripFrontMatter = (raw: string) => raw.replace(/^---[\s\S]*?---\n?/, '').trim();

  const [formData, setFormData] = useState<SkillFormData>({
    name: skill?.name ?? '',
    description: skill?.description ?? '',
    descriptionCn: skill?.description_cn ?? '',
    content: stripFrontMatter(skill?.content ?? ''),
  });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // Toggle between edit (textarea) and preview (Markdown render) for the content area
  const [contentEditing, setContentEditing] = useState(false);

  const canSubmit = !isReadonly && (formData.name && formData.description && formData.content);

  const handleSubmit = async () => {
    if (isReadonly) { onClose(); return; }
    try {
      setLoading(true);
      if (isEdit) {
        await skillAPI.update(skill!.name, {
          name: formData.name,
          description: formData.description,
          description_cn: formData.descriptionCn || undefined,
          content: formData.content,
        });
      } else {
        await skillAPI.create({
          name: formData.name,
          description: formData.description,
          description_cn: formData.descriptionCn || undefined,
          content: formData.content,
        });
      }
      onSaved();
    } catch (err: unknown) {
      toast.error(t('sheet.operationFailed'), err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!skill || skill.source === 'project') return;
    if (!confirm(t('sheet.deleteConfirm', { name: skill.name }))) return;
    try {
      setDeleting(true);
      await skillAPI.delete(skill.name);
      onDeleted?.();
    } catch (err: unknown) {
      toast.error(t('sheet.deleteFailed'), err instanceof Error ? err.message : String(err));
    } finally {
      setDeleting(false);
    }
  };

  const handleSaveContent = async () => {
    if (!isEdit || !skill) return;
    try {
      setSaving(true);
      await skillAPI.update(skill.name, {
        name: formData.name,
        description: formData.description,
        description_cn: formData.descriptionCn || undefined,
        content: formData.content,
      });
      setContentEditing(false);
      onSaved();
    } catch (err: unknown) {
      toast.error(t('sheet.saveFailed'), err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  // Display path: strip trailing /SKILL.md to show skill directory only
  // user source:    ~/.flocks/plugins/skills/baidu-news-fetch  (home dir → ~)
  // project source: .flocks/plugins/skills/my-skill            (extract from .flocks/ onwards)
  const relativePath = (() => {
    if (!skill?.location) return null;
    const loc = skill.location.replace(/\/SKILL\.md$/, '');
    if (skill.source === 'user') {
      // Replace home directory prefix with ~
      const homePrefix = loc.match(/^(\/Users\/[^/]+|\/home\/[^/]+)(\/.*)/);
      if (homePrefix) return `~${homePrefix[2]}`;
    }
    // For project/flocks/claude: extract from .flocks/ onwards
    return loc.match(/\.flocks\/.+/)?.[0] ?? loc;
  })();

  const contentForRender = formData.content;

  return (
    <EntitySheet
      open
      mode={isEdit ? 'edit' : 'create'}
      entityType={t('sheet.entityType')}
      entityName={skill?.name}
      icon={<BookOpen className="w-5 h-5" />}
      rexSystemContext={buildRexContext(isEdit, formData)}
      rexWelcomeMessage={buildRexWelcome(isEdit, skill?.name)}
      submitDisabled={!canSubmit}
      submitLoading={loading}
      submitLabel={isReadonly ? t('sheet.submitClose') : undefined}
      width={700}
      maxWidth={900}
      onClose={onClose}
      onSubmit={handleSubmit}
      footerLeft={isEdit ? (
        <button
          type="button"
          onClick={handleDelete}
          disabled={deleting || !isUserSkill}
          title={!isUserSkill ? t('sheet.deleteBuiltinTip') : undefined}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-sm border rounded-lg transition-colors
            ${isUserSkill
              ? 'text-red-500 border-red-200 hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed'
              : 'text-gray-300 border-gray-200 cursor-not-allowed'
            }`}
        >
          {deleting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
          {deleting ? t('sheet.deleting') : t('sheet.delete')}
        </button>
      ) : undefined}
    >
      <div className="space-y-4">
        {/* Read-only banner */}
        {isReadonly && (
          <div className="flex items-center gap-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700">
            <Lock className="w-3.5 h-3.5 shrink-0" />
            {t('sheet.readonlyNote')}
          </div>
        )}

        {/* Name */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            {t('sheet.skillName')} {!isEdit && <span className="text-red-500">*</span>}
          </label>
          {isReadonly ? (
            <div className="px-4 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-700 font-mono">
              {formData.name}
            </div>
          ) : (
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 text-sm font-mono"
              placeholder="my-skill"
            />
          )}
        </div>

        {/* Description */}
        <div>
          <label className={`block text-sm font-medium mb-1 ${isReadonly ? 'text-gray-500' : 'text-gray-700'}`}>
            {t('sheet.description')} {!isEdit && <span className="text-red-500">*</span>}
          </label>
          {isReadonly ? (
            <div className="px-4 py-3 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-600 leading-relaxed whitespace-pre-wrap">
              {localizedDescription || t('sheet.noDescription')}
            </div>
          ) : (
            <textarea
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 text-sm resize-none leading-relaxed"
              placeholder={t('sheet.descriptionPlaceholder')}
              rows={3}
            />
          )}
        </div>

        {/* Chinese Description */}
        {!isReadonly && (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('sheet.descriptionCn')}
            </label>
            <textarea
              value={formData.descriptionCn}
              onChange={(e) => setFormData({ ...formData, descriptionCn: e.target.value })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 text-sm resize-none leading-relaxed"
              placeholder={t('sheet.descriptionCnPlaceholder')}
              rows={3}
            />
          </div>
        )}

        {/* Path (edit/view mode, shown above content) */}
        {isEdit && relativePath && (
          <div className="flex items-center gap-2 px-3 py-2 bg-gray-50 border border-gray-100 rounded-lg text-xs text-gray-500">
            <span className="text-gray-400 shrink-0">{t('sheet.path')}</span>
            <code className="font-mono text-gray-600 truncate">{relativePath}</code>
            {skill?.source && (
              <>
                <span className="text-gray-300">·</span>
                <span className="text-gray-400 shrink-0">{skill.source}</span>
              </>
            )}
          </div>
        )}

        {/* Content */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-sm font-medium text-gray-700">
              {t('sheet.content')}
              {!isEdit && <span className="text-red-500"> *</span>}
            </label>
            {/* Edit/preview toggle (only for editable skills) */}
            {isEdit && !isReadonly && (
              contentEditing ? (
                <button
                  type="button"
                  onClick={() => setContentEditing(false)}
                  className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 transition-colors"
                >
                  <Eye className="w-3.5 h-3.5" />
                  {t('sheet.preview')}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setContentEditing(true)}
                  className="flex items-center gap-1 text-xs text-red-600 hover:text-red-700 transition-colors"
                >
                  <Pencil className="w-3.5 h-3.5" />
                  {t('sheet.edit')}
                </button>
              )
            )}
          </div>

          {contentEditing ? (
            /* Edit mode: raw Markdown textarea with inline save */
            <div className="space-y-2">
              <textarea
                value={formData.content}
                onChange={(e) => setFormData({ ...formData, content: e.target.value })}
                className="w-full px-4 py-3 border border-red-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 resize-none font-mono text-sm"
                style={{ minHeight: '400px', maxHeight: '65vh' }}
                placeholder={t('sheet.contentPlaceholder')}
                autoFocus
              />
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setFormData({ ...formData, content: stripFrontMatter(skill?.content ?? '') });
                    setContentEditing(false);
                  }}
                  className="px-3 py-1.5 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
                >
                  {t('common:button.cancel')}
                </button>
                <button
                  type="button"
                  onClick={handleSaveContent}
                  disabled={saving || !formData.content.trim()}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                  {saving ? t('sheet.saving') : t('common:button.save')}
                </button>
              </div>
            </div>
          ) : (
            /* Preview mode: render Markdown */
            <div
              className="px-4 py-3 bg-gray-50 border border-gray-200 rounded-lg text-sm prose prose-sm max-w-none overflow-y-auto"
              style={{ minHeight: '320px', maxHeight: '65vh' }}
            >
              <ReactMarkdown>{contentForRender || t('sheet.noContent')}</ReactMarkdown>
            </div>
          )}
        </div>
        {/* Delete button moved to footer via footerLeft prop */}
      </div>
    </EntitySheet>
  );
}