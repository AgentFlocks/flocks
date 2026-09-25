/**
 * SkillSheet — unified Skill create/edit/view side panel
 *
 * Wraps EntitySheet and supports:
 * - View mode (read-only: name, description, content)
 * - Form mode (direct field editing: name, description, content)
 * - Rex chat mode (natural language → extract config into form)
 */

import { lazy, Suspense, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconBook, IconLock, IconPencil, IconEye, IconDeviceFloppy, IconLoader2, IconTrash } from '@tabler/icons-react';
import { skillAPI, Skill } from '@/api/skill';
import { useToast } from '@/components/common/Toast';
import EntitySheet from '@/components/common/EntitySheet';
import { buildGuidedCreateGroups } from '@/components/common/GuidedCreatePanel';
import { useRexComposerControls } from '@/components/common/useRexComposerControls';

const ReactMarkdown = lazy(() => import('react-markdown'));

function MarkdownFallback() {
  return (
    <div className="flex min-h-[160px] items-center justify-center text-fg-muted">
      <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
    </div>
  );
}

interface SkillFormData {
  name: string;
  description: string;
  content: string;
}

function buildRexContext(isEdit: boolean, formData: SkillFormData): string {
  if (!isEdit) {
    return `你是 Skill 创建助手。用户希望通过对话来创建一个新的 Skill。

请先加载并遵守项目内 .flocks/plugins/skills/skill-builder（skill-builder skill），再根据用户需求完成创建，产物写入 ~/.flocks/plugins/skills/<skill-name>/ 目录。

**创建流程：**
1. 先确认用户需求：Skill 名称（kebab-case）、描述、主要功能、作用域（用户/global 或项目）
2. 按 skill 生成 SKILL.md（及必要的 references/scripts/evals）
3. 执行 skill 要求的验证，确保 skill 可被系统发现

**重要约束：**
- 必须先加载 .flocks/plugins/skills/skill-builder，再动手写文件
- 禁止写到 .flocks/skills/ 等内置 skill 目录
- 默认写入 ~/.flocks/plugins/skills/<skill-name>/，除非用户明确要求项目级路径

请先引导用户描述需求，信息不足时可追问，然后按 skill 一次性完成创建。`;
  }

  return `你是 Skill 优化助手，正在帮助用户修改技能「${formData.name}」。

**当前 Skill 信息：**
- 名称：${formData.name}
- 描述：${formData.description || '（无描述）'}
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
  const { t } = useTranslation('skill');
  const toast = useToast();
  const isEdit = !!skill;
  // Custom skills (source !== 'project') are editable and deletable
  const isUserSkill = isEdit && skill.source !== 'project';
  const isReadonly = isEdit && !isUserSkill;

  // Strip YAML front matter — name/description are already shown as separate fields
  const stripFrontMatter = (raw: string) => raw.replace(/^---[\s\S]*?---\n?/, '').trim();

  const [formData, setFormData] = useState<SkillFormData>({
    name: skill?.name ?? '',
    description: skill?.description ?? '',
    content: stripFrontMatter(skill?.content ?? ''),
  });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // Toggle between edit (textarea) and preview (Markdown render) for the content area
  const [contentEditing, setContentEditing] = useState(false);
  const createGuideGroups = useMemo(() => buildGuidedCreateGroups([
    { title: t('create.guideSectionTitle'), actions: t('create.guideActions', { returnObjects: true }) },
    { title: t('create.caseSectionTitle'), actions: t('create.caseActions', { returnObjects: true }) },
  ]), [t]);
  const rexComposerControls = useRexComposerControls();

  // Mirrors the backend rule (Skill._is_valid_name): lowercase slug, single hyphens.
  const nameValid = /^[a-z0-9]+(-[a-z0-9]+)*$/.test(formData.name || '');
  const showNameHint = !isReadonly && !!formData.name && !nameValid;
  const canSubmit = !isReadonly && nameValid && (formData.name && formData.description && formData.content);

  const handleSubmit = async () => {
    if (isReadonly) { onClose(); return; }
    try {
      setLoading(true);
      if (isEdit) {
        await skillAPI.update(skill!.name, formData);
      } else {
        await skillAPI.create(formData);
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
      await skillAPI.update(skill.name, formData);
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
      icon={<IconBook aria-hidden="true" className="w-5 h-5" />}
      rexSystemContext={buildRexContext(isEdit, formData)}
      rexWelcomeMessage={buildRexWelcome(isEdit, skill?.name)}
      rexGuideGroups={!isEdit ? createGuideGroups : undefined}
      rexGuidePanelTitle={!isEdit ? t('create.guidePanelTitle') : undefined}
      rexGuidePanelDesc={!isEdit ? t('create.guidePanelDesc') : undefined}
      rexGuideEmptyTitle={!isEdit ? t('create.emptyStateTitle') : undefined}
      rexGuideIcon={!isEdit ? <IconBook aria-hidden="true" className="h-5 w-5" /> : undefined}
      {...(!isEdit ? rexComposerControls : {})}
      // Read-only sheets turn the submit button into 「关闭」; it must stay clickable even though the form can't be submitted.
      submitDisabled={isReadonly ? false : !canSubmit}
      submitLoading={loading}
      submitLabel={isReadonly ? t('sheet.submitClose') : undefined}
      hideForm={!isEdit}
      onClose={onClose}
      onSubmit={handleSubmit}
      footerLeft={isEdit ? (
        <button
          type="button"
          onClick={handleDelete}
          disabled={deleting || !isUserSkill}
          title={!isUserSkill ? t('sheet.deleteBuiltinTip') : undefined}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-sm border rounded-control transition-colors
 ${isUserSkill
              ? 'text-danger border-danger hover:bg-danger-soft disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed'
              : 'text-fg-placeholder border-line cursor-not-allowed'
            }`}
        >
          {deleting ? <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" /> : <IconTrash aria-hidden="true" className="w-4 h-4" />}
          {deleting ? t('sheet.deleting') : t('sheet.delete')}
        </button>
      ) : undefined}
    >
      <div className="space-y-4">
        {/* Read-only banner */}
        {isReadonly && (
          <div className="flex items-center gap-2 px-3 py-2 bg-warning-soft border border-warning rounded-control text-xs text-warning">
            <IconLock aria-hidden="true" className="w-4 h-4 shrink-0" />
            {t('sheet.readonlyNote')}
          </div>
        )}

        {/* Name */}
        <div>
          <label className="block text-sm font-medium text-fg-secondary mb-1">
            {t('sheet.skillName')} {!isEdit && <span className="text-danger">*</span>}
          </label>
          {isReadonly ? (
            <div className="px-4 py-2 bg-subtle border border-line rounded-control text-sm text-fg-secondary font-mono">
              {formData.name}
            </div>
          ) : (
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className={`w-full px-4 py-2 border rounded-control text-sm font-mono ${showNameHint ? 'border-danger ' : 'border-line-control '}`}
              placeholder="my-skill"
              aria-invalid={showNameHint || undefined}
            />
          )}
          {showNameHint && (
            <p className="mt-1 text-xs text-danger" data-testid="skill-name-hint">{t('sheet.nameFormatHint')}</p>
          )}
        </div>

        {/* Description */}
        <div>
          <label className={`block text-sm font-medium mb-1 ${isReadonly ? 'text-fg-muted' : 'text-fg-secondary'}`}>
            {t('sheet.description')} {!isEdit && <span className="text-danger">*</span>}
          </label>
          {isReadonly ? (
            <div className="px-4 py-3 bg-subtle border border-line rounded-control text-sm text-fg-secondary whitespace-pre-wrap">
              {formData.description || t('sheet.noDescription')}
            </div>
          ) : (
            <textarea
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              className="ui-textarea w-full px-4  border border-line-control rounded-control text-sm resize-none"
              placeholder={t('sheet.descriptionPlaceholder')}
              rows={3}
            />
          )}
        </div>

        {/* Path (edit/view mode, shown above content) */}
        {isEdit && relativePath && (
          <div className="flex items-center gap-2 px-3 py-2 bg-subtle border border-line rounded-control text-xs text-fg-muted">
            <span className="text-fg-muted shrink-0">{t('sheet.path')}</span>
            <code className="font-mono text-fg-secondary truncate">{relativePath}</code>
            {skill?.source && (
              <>
                <span className="text-fg-placeholder">·</span>
                <span className="text-fg-muted shrink-0">{skill.source}</span>
              </>
            )}
          </div>
        )}

        {/* Content */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-sm font-medium text-fg-secondary">
              {t('sheet.content')}
              {!isEdit && <span className="text-danger"> *</span>}
            </label>
            {/* Edit/preview toggle (only for editable skills) */}
            {isEdit && !isReadonly && (
              contentEditing ? (
                <button
                  type="button"
                  onClick={() => setContentEditing(false)}
                  className="flex items-center gap-1 text-xs text-fg-muted hover:text-fg-secondary transition-colors"
                >
                  <IconEye aria-hidden="true" className="w-4 h-4" />
                  {t('sheet.preview')}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setContentEditing(true)}
                  className="flex items-center gap-1 text-xs text-danger hover:text-danger transition-colors"
                >
                  <IconPencil aria-hidden="true" className="w-4 h-4" />
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
                className="ui-textarea w-full px-4  border border-danger rounded-control resize-none font-mono text-sm"
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
                  className="px-3 py-1.5 text-sm text-fg-secondary bg-subtle rounded-control hover:bg-hover transition-colors"
                >
                  {t('common:button.cancel')}
                </button>
                <button
                  type="button"
                  onClick={handleSaveContent}
                  disabled={saving || !formData.content.trim()}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-action text-action-fg rounded-control hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed transition-colors"
                >
                  {saving ? <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" /> : <IconDeviceFloppy aria-hidden="true" className="w-4 h-4" />}
                  {saving ? t('sheet.saving') : t('common:button.save')}
                </button>
              </div>
            </div>
          ) : (
            /* Preview mode: render Markdown */
            <div
              className="px-4 py-3 bg-subtle border border-line rounded-control text-sm prose prose-sm max-w-none overflow-y-auto"
              style={{ minHeight: '320px', maxHeight: '65vh' }}
            >
              <Suspense fallback={<MarkdownFallback />}>
                <ReactMarkdown>{contentForRender || t('sheet.noContent')}</ReactMarkdown>
              </Suspense>
            </div>
          )}
        </div>
        {/* Delete button moved to footer via footerLeft prop */}
      </div>
    </EntitySheet>
  );
}
