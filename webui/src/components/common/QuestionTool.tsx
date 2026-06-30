/**
 * QuestionTool — generic interactive question component
 *
 * Supports the following input types:
 * - choice   single / multi-select
 * - text     text input (single-line / multi-line)
 * - number   number input (with optional range)
 * - file     file upload (reads content and returns to Agent)
 * - confirm  yes/no quick buttons
 * - password password input (masked)
 */

import { useState, useRef } from 'react';
import { Check, Loader2, Upload, Eye, EyeOff, CircleHelp } from 'lucide-react';
import { useTranslation } from 'react-i18next';

// ============================================================================
// Types
// ============================================================================

export type QuestionType = 'choice' | 'text' | 'number' | 'file' | 'confirm' | 'password';

export interface QuestionOption {
  label?: string;
  description?: string;
  [key: string]: unknown;
}

export interface QuestionItem {
  question: string;
  header?: string;
  /** Input type. Defaults to 'choice' if options present, else 'text'. */
  type?: QuestionType;
  /** For choice: list of options */
  options?: (QuestionOption | string)[];
  /** For choice: allow selecting multiple options */
  multiple?: boolean;
  /** For choice: allow a custom "Other" free-form answer. Defaults to true. */
  custom?: boolean;
  /** Placeholder/hint text */
  placeholder?: string;
  /** For text: use textarea (multi-line) */
  multiline?: boolean;
  /** For number: min value */
  min_value?: number;
  /** For number: max value */
  max_value?: number;
  /** For number: step increment */
  step?: number;
  /** For file: accepted extensions (e.g. ".txt,.log,.csv") */
  accept?: string;
}

export interface QuestionToolProps {
  questions: QuestionItem[];
  onAnswer: (answers: string[][]) => Promise<void>;
  onReject?: () => Promise<void>;
  /** Use compact sizing (for SessionChat) vs full sizing (for Session page) */
  compact?: boolean;
}

// ============================================================================
// Helpers
// ============================================================================

function resolveType(q: QuestionItem): QuestionType {
  const hasOptions = (q.options ?? []).some(opt => optionLabel(opt));
  if (q.type === 'choice' && !hasOptions) return 'text';
  if (q.type) return q.type;
  if (hasOptions) return 'choice';
  return 'text';
}

function optionLabel(opt: QuestionOption | string): string {
  if (typeof opt === 'string') return opt;
  for (const key of ['label', 'text', 'title', 'name', 'value', 'id', 'key']) {
    const value = opt[key];
    if (value !== undefined && value !== null) {
      const text = String(value).trim();
      if (text) return text;
    }
  }
  return '';
}

function optionDescription(opt: QuestionOption | string): string {
  if (typeof opt === 'string') return '';
  for (const key of ['description', 'desc', 'subtitle', 'detail', 'details']) {
    const value = opt[key];
    if (value !== undefined && value !== null) {
      const text = String(value).trim();
      if (text) return text;
    }
  }
  return '';
}

const CUSTOM_CHOICE_PREFIX = '__flocks_custom_choice__:';

function isCustomChoiceLabel(label: string): boolean {
  return /^(其他|其它|自定义|补充)|\b(other|custom)\b|请补充|补充说明|type your answer/i.test(label.trim());
}

function customChoiceValue(text: string): string {
  return `${CUSTOM_CHOICE_PREFIX}${text}`;
}

function isCustomChoiceValue(value: string): boolean {
  return value.startsWith(CUSTOM_CHOICE_PREFIX);
}

function customChoiceText(answer: string[]): string {
  const value = answer.find(isCustomChoiceValue) ?? '';
  return value.slice(CUSTOM_CHOICE_PREFIX.length);
}

function hasCustomChoice(answer: string[]): boolean {
  return answer.some(isCustomChoiceValue);
}

function shouldOfferCustomChoice(q: QuestionItem): boolean {
  return resolveType(q) === 'choice' && q.custom !== false;
}

function isQuestionAnswered(q: QuestionItem, answer: string[], allowBlankAsNone = false): boolean {
  const type = resolveType(q);
  if (type === 'choice') {
    if (hasCustomChoice(answer)) return customChoiceText(answer).trim().length > 0;
    return answer.length > 0;
  }
  if (type === 'confirm') return answer.length > 0;
  if (type === 'text' || type === 'password' || type === 'number') {
    return allowBlankAsNone || (answer[0] ?? '').trim().length > 0;
  }
  if (type === 'file') return answer.length > 0;
  return false;
}

function normalizeAnswerForSubmit(q: QuestionItem, answer: string[], allowBlankAsNone = false): string[] {
  const type = resolveType(q);
  if (type === 'choice') {
    return answer.flatMap(value => {
      if (!isCustomChoiceValue(value)) return [value];
      const text = value.slice(CUSTOM_CHOICE_PREFIX.length).trim();
      return text ? [text] : [];
    });
  }
  if (allowBlankAsNone && type === 'text') {
    const value = (answer[0] ?? '').trim();
    return [value || 'none'];
  }
  return answer;
}

function isInlineTextFollowUp(prev: QuestionItem | undefined, q: QuestionItem | undefined): boolean {
  if (!q) return false;
  if (!prev || resolveType(prev) !== 'choice' || resolveType(q) !== 'text') return false;
  const marker = `${q.header ?? ''} ${q.question ?? ''} ${q.placeholder ?? ''}`.toLowerCase();
  return /自定义|补充|说明|备注|其他|其它|custom|other|additional|note|comment/.test(marker);
}

function inlineTextHeader(q: QuestionItem, fallback: string): string {
  const header = (q.header ?? '').replace(/^step\s*\d+\s*/i, '').trim();
  return header || fallback;
}

// ============================================================================
// Sub-components
// ============================================================================

function ChoiceInput({
  q,
  answer,
  onChange,
  disabled,
  compact,
}: {
  q: QuestionItem;
  answer: string[];
  onChange: (v: string[]) => void;
  disabled: boolean;
  compact: boolean;
}) {
  const { t } = useTranslation('common');
  const multiple = q.multiple ?? false;
  const visibleOptions = (q.options ?? [])
    .map(opt => ({
      label: optionLabel(opt),
      description: optionDescription(opt),
      custom: false,
    }))
    .filter(opt => opt.label);
  const hasProvidedCustomOption = visibleOptions.some(opt => isCustomChoiceLabel(opt.label));
  const options = shouldOfferCustomChoice(q) && !hasProvidedCustomOption
    ? [
        ...visibleOptions,
        {
          label: t('question.customAnswer'),
          description: t('question.textPlaceholder'),
          custom: true,
        },
      ]
    : visibleOptions.map(opt => ({
        ...opt,
        custom: isCustomChoiceLabel(opt.label),
      }));
  const customSelected = hasCustomChoice(answer);
  const customText = customChoiceText(answer);
  const toggle = (label: string) => {
    if (multiple) {
      onChange(answer.includes(label) ? answer.filter(l => l !== label) : [...answer, label]);
    } else {
      onChange([label]);
    }
  };
  const toggleCustom = () => {
    if (multiple) {
      if (customSelected) {
        onChange(answer.filter(value => !isCustomChoiceValue(value)));
      } else {
        onChange([...answer, customChoiceValue(customText)]);
      }
      return;
    }
    onChange(customSelected ? [] : [customChoiceValue(customText)]);
  };
  const setCustomText = (text: string) => {
    const nextCustom = customChoiceValue(text);
    if (multiple) {
      const withoutCustom = answer.filter(value => !isCustomChoiceValue(value));
      onChange([...withoutCustom, nextCustom]);
      return;
    }
    onChange([nextCustom]);
  };

  return (
    <div>
      {/* Mode hint */}
      <div className={`mb-1.5 ${compact ? 'text-[10px]' : 'text-xs'} font-medium text-zinc-400`}>
        {multiple ? t('question.multiSelect') : t('question.singleSelect')}
      </div>
      <div className="space-y-1.5">
        {options.map(opt => {
          const label = opt.label;
          const desc = opt.description;
          const selected = opt.custom ? customSelected : answer.includes(label);
          return (
            <div key={label}>
              <button
                onClick={() => (opt.custom ? toggleCustom() : toggle(label))}
                disabled={disabled}
                className={`w-full text-left rounded-lg border px-3 py-2 text-sm transition-all flex items-start gap-2.5 ${
                  selected
                    ? 'border-emerald-300 bg-emerald-50 text-emerald-900'
                    : 'border-zinc-200 bg-white text-zinc-700 hover:border-zinc-300 hover:bg-zinc-50'
                }`}
              >
                {multiple ? (
                  <span
                    className={`mt-0.5 flex-shrink-0 w-4 h-4 rounded border-2 flex items-center justify-center ${
                      selected ? 'border-emerald-500 bg-emerald-500' : 'border-zinc-300 bg-transparent'
                    }`}
                  >
                    {selected && <Check className="w-2.5 h-2.5 text-white" strokeWidth={3} />}
                  </span>
                ) : (
                  <span
                    className={`mt-0.5 flex-shrink-0 w-4 h-4 rounded-full border-2 flex items-center justify-center ${
                      selected ? 'border-emerald-500' : 'border-zinc-300'
                    }`}
                  >
                    {selected && <span className="w-2 h-2 rounded-full bg-emerald-500 block" />}
                  </span>
                )}
                <span>
                  <span className={`font-medium ${compact ? 'text-xs' : 'text-sm'}`}>{label}</span>
                  {desc && (
                    <span className={`block mt-0.5 ${compact ? 'text-[11px]' : 'text-xs'} ${selected ? 'text-emerald-700/80' : 'text-zinc-400'}`}>
                      {desc}
                    </span>
                  )}
                </span>
              </button>
              {opt.custom && selected && (
                <div className="mt-1.5 rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2">
                  <TextInput
                    q={{ ...q, type: 'text', placeholder: q.placeholder || t('question.textPlaceholder'), multiline: q.multiline ?? true }}
                    answer={[customText]}
                    onChange={(value) => setCustomText(value[0] ?? '')}
                    disabled={disabled}
                    compact={compact}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
      {multiple && answer.length > 0 && (
        <div className={`mt-1.5 ${compact ? 'text-[10px]' : 'text-xs'} text-zinc-500`}>
          {t('question.selectedCount', { count: answer.length })}
        </div>
      )}
    </div>
  );
}

function TextInput({
  q,
  answer,
  onChange,
  disabled,
  compact,
}: {
  q: QuestionItem;
  answer: string[];
  onChange: (v: string[]) => void;
  disabled: boolean;
  compact: boolean;
}) {
  const { t } = useTranslation('common');
  const cls = `w-full border border-zinc-300 rounded-lg px-3 py-2 ${compact ? 'text-xs' : 'text-sm'} text-zinc-900 placeholder-zinc-400 outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100`;
  if (q.multiline) {
    return (
      <textarea
        value={answer[0] ?? ''}
        onChange={e => onChange([e.target.value])}
        placeholder={q.placeholder || t('question.textPlaceholder')}
        disabled={disabled}
        rows={4}
        className={`${cls} resize-none`}
      />
    );
  }
  return (
    <input
      type="text"
      value={answer[0] ?? ''}
      onChange={e => onChange([e.target.value])}
      placeholder={q.placeholder || t('question.textPlaceholder')}
      disabled={disabled}
      className={cls}
    />
  );
}

function NumberInput({
  q,
  answer,
  onChange,
  disabled,
  compact,
}: {
  q: QuestionItem;
  answer: string[];
  onChange: (v: string[]) => void;
  disabled: boolean;
  compact: boolean;
}) {
  const { t } = useTranslation('common');
  return (
    <input
      type="number"
      value={answer[0] ?? ''}
      onChange={e => onChange([e.target.value])}
      placeholder={q.placeholder || t('question.numberPlaceholder')}
      min={q.min_value}
      max={q.max_value}
      step={q.step}
      disabled={disabled}
      className={`w-full border border-zinc-300 rounded-lg px-3 py-2 ${compact ? 'text-xs' : 'text-sm'} text-zinc-900 placeholder-zinc-400 outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100`}
    />
  );
}

function PasswordInput({
  q,
  answer,
  onChange,
  disabled,
  compact,
}: {
  q: QuestionItem;
  answer: string[];
  onChange: (v: string[]) => void;
  disabled: boolean;
  compact: boolean;
}) {
  const { t } = useTranslation('common');
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input
        type={show ? 'text' : 'password'}
        value={answer[0] ?? ''}
        onChange={e => onChange([e.target.value])}
        placeholder={q.placeholder || t('question.passwordPlaceholder')}
        disabled={disabled}
        className={`w-full border border-zinc-300 rounded-lg px-3 pr-10 py-2 ${compact ? 'text-xs' : 'text-sm'} text-zinc-900 placeholder-zinc-400 outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100`}
      />
      <button
        type="button"
        onClick={() => setShow(s => !s)}
        className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600"
        tabIndex={-1}
      >
        {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
      </button>
    </div>
  );
}

function FileInput({
  q,
  answer,
  onChange,
  disabled,
  compact,
}: {
  q: QuestionItem;
  answer: string[];
  onChange: (v: string[]) => void;
  disabled: boolean;
  compact: boolean;
}) {
  const { t } = useTranslation('common');
  const inputRef = useRef<HTMLInputElement>(null);
  const filename = answer.find(s => s.startsWith('filename:'))?.replace('filename:', '') ?? '';

  const handleChange = (file: File | null) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = e => {
      const content = e.target?.result as string;
      onChange([`filename:${file.name}`, `content:${content}`]);
    };
    const isText =
      file.type.startsWith('text/') ||
      /\.(txt|log|csv|json|xml|yaml|yml|md|py|js|ts|sh|conf|ini|toml)$/i.test(file.name);
    if (isText) {
      reader.readAsText(file);
    } else {
      reader.readAsDataURL(file);
    }
  };

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        accept={q.accept}
        className="hidden"
        onChange={e => handleChange(e.target.files?.[0] ?? null)}
      />
      <button
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
        className={`flex items-center gap-2 px-4 py-2.5 border-2 border-dashed rounded-lg ${compact ? 'text-xs' : 'text-sm'} transition-all ${
          filename
            ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
            : 'border-zinc-300 text-zinc-500 hover:border-emerald-300 hover:bg-emerald-50 hover:text-emerald-700'
        }`}
      >
        <Upload className="w-4 h-4 flex-shrink-0" />
        <span className="truncate max-w-[240px]">
          {filename || q.placeholder || t('question.filePlaceholder')}
        </span>
      </button>
      {filename && (
        <p className={`mt-1 ${compact ? 'text-[11px]' : 'text-xs'} text-emerald-600`}>
          {t('question.fileSelected', { filename })}
        </p>
      )}
    </div>
  );
}

function ConfirmInput({
  answer,
  onChange,
  disabled,
  compact,
}: {
  answer: string[];
  onChange: (v: string[]) => void;
  disabled: boolean;
  compact: boolean;
}) {
  const { t } = useTranslation('common');
  const selected = answer[0];
  return (
    <div className="flex gap-2">
      <button
        onClick={() => onChange(['yes'])}
        disabled={disabled}
        className={`flex-1 py-2 rounded-lg border ${compact ? 'text-xs' : 'text-sm'} font-medium transition-all ${
          selected === 'yes'
            ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
            : 'border-zinc-200 bg-white text-zinc-700 hover:border-zinc-300 hover:bg-zinc-50'
        }`}
      >
        {t('question.yes')}
      </button>
      <button
        onClick={() => onChange(['no'])}
        disabled={disabled}
        className={`flex-1 py-2 rounded-lg border ${compact ? 'text-xs' : 'text-sm'} font-medium transition-all ${
          selected === 'no'
            ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
            : 'border-zinc-200 bg-white text-zinc-700 hover:border-zinc-300 hover:bg-zinc-50'
        }`}
      >
        {t('question.no')}
      </button>
    </div>
  );
}

// ============================================================================
// Main component
// ============================================================================

export function QuestionTool({ questions, onAnswer, onReject, compact = false }: QuestionToolProps) {
  const { t } = useTranslation('common');
  const [answers, setAnswers] = useState<string[][]>(() => questions.map(() => []));
  const [submitting, setSubmitting] = useState(false);

  const setAnswer = (idx: number, value: string[]) => {
    setAnswers(prev => {
      const next = [...prev];
      next[idx] = value;
      return next;
    });
  };

  const canSubmit = questions.every((q, i) => (
    isQuestionAnswered(q, answers[i] ?? [], isInlineTextFollowUp(questions[i - 1], q))
  ));

  const handleSubmit = async () => {
    const normalizedAnswers = questions.map((q, i) => (
      normalizeAnswerForSubmit(q, answers[i] ?? [], isInlineTextFollowUp(questions[i - 1], q))
    ));
    setSubmitting(true);
    try { await onAnswer(normalizedAnswers); } finally { setSubmitting(false); }
  };

  const handleReject = async () => {
    if (!onReject) return;
    setSubmitting(true);
    try { await onReject(); } finally { setSubmitting(false); }
  };

  const px = compact ? 'px-3' : 'px-4';
  const py = compact ? 'py-2' : 'py-3';
  const textSm = compact ? 'text-xs' : 'text-sm';

  return (
    <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white">
      {/* Header */}
      <div className={`${px} flex items-center gap-2 border-b border-zinc-100 bg-white py-2`}>
        <CircleHelp className={`${compact ? 'h-3.5 w-3.5' : 'h-4 w-4'} flex-shrink-0 text-emerald-500`} />
        <span className={`${textSm} font-medium text-zinc-700`}>{t('question.needsAnswer')}</span>
      </div>

      {/* Questions */}
      <div className={`${px} ${py} space-y-4`}>
        {questions.map((q, qIdx) => {
          if (isInlineTextFollowUp(questions[qIdx - 1], q)) return null;

          const type = resolveType(q);
          const answer = answers[qIdx] ?? [];
          const inlineFollowUp = type === 'choice' && isInlineTextFollowUp(q, questions[qIdx + 1])
            ? questions[qIdx + 1]
            : undefined;
          const inputQuestion = inlineFollowUp && q.custom === undefined ? { ...q, custom: false } : q;
          const inputProps = { q: inputQuestion, answer, onChange: (v: string[]) => setAnswer(qIdx, v), disabled: submitting, compact };
          const inlineFollowUpIdx = inlineFollowUp ? qIdx + 1 : -1;
          const inlineFollowUpAnswer = inlineFollowUp ? (answers[inlineFollowUpIdx] ?? []) : [];

          return (
            <div key={qIdx}>
              {q.header && (
                <div className={`${compact ? 'text-[11px]' : 'text-xs'} mb-0.5 font-medium text-zinc-500`}>
                  {q.header}
                </div>
              )}
              <div className={`${textSm} mb-2 font-medium text-zinc-800`}>{q.question}</div>

              {type === 'choice'   && <ChoiceInput   {...inputProps} />}
              {inlineFollowUp && (
                <div className="mt-2 rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2.5">
                  <div className={`${compact ? 'text-[11px]' : 'text-xs'} font-medium text-zinc-700`}>
                    {inlineTextHeader(inlineFollowUp, t('question.customAnswer'))}
                  </div>
                  <div className={`${compact ? 'text-[11px]' : 'text-xs'} mt-1 leading-relaxed text-zinc-500`}>
                    {inlineFollowUp.question}
                  </div>
                  <div className="mt-2">
                    <TextInput
                      q={{ ...inlineFollowUp, placeholder: inlineFollowUp.placeholder || 'none' }}
                      answer={inlineFollowUpAnswer}
                      onChange={(v) => setAnswer(inlineFollowUpIdx, v)}
                      disabled={submitting}
                      compact={compact}
                    />
                  </div>
                </div>
              )}
              {type === 'text'     && <TextInput     {...inputProps} />}
              {type === 'number'   && <NumberInput   {...inputProps} />}
              {type === 'password' && <PasswordInput {...inputProps} />}
              {type === 'file'     && <FileInput     {...inputProps} />}
              {type === 'confirm'  && <ConfirmInput  answer={answer} onChange={v => setAnswer(qIdx, v)} disabled={submitting} compact={compact} />}
            </div>
          );
        })}

        {/* Action buttons */}
        <div className="flex gap-2 pt-1">
          <button
            onClick={handleSubmit}
            disabled={submitting || !canSubmit}
            className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-emerald-600 py-2 ${textSm} font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-40`}
          >
            {submitting
              ? <Loader2 className={`${compact ? 'w-3 h-3' : 'w-4 h-4'} animate-spin`} />
              : <Check className={`${compact ? 'w-3 h-3' : 'w-4 h-4'}`} />
            }
            {t('question.confirm')}
          </button>
          {onReject && (
            <button
              onClick={handleReject}
              disabled={submitting}
              className={`rounded-lg border border-zinc-200 px-4 py-2 ${textSm} text-zinc-600 transition-colors hover:bg-zinc-50 disabled:opacity-40`}
            >
              {t('question.skip')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
