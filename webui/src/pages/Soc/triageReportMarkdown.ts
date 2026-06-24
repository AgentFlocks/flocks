export const TRIAGE_REPORT_VERSION = 'soc.triage.markdown.v1';

export const TRIAGE_REPORT_TAGS = [
  'report_title',
  'report_meta',
  'analysis_steps',
  'triage_conclusion',
  'attack_payload',
  'payload_explanation',
  'response_evidence',
  'key_evidence',
  'disposal_recommendation',
] as const;

export type TriageReportTag = typeof TRIAGE_REPORT_TAGS[number];

export type TriageReportSections = Record<TriageReportTag, string>;

export interface TaggedTriageReport {
  raw: string;
  title: string;
  stepCount: number;
  sections: TriageReportSections;
}

export function parseTaggedTriageReport(markdown?: string | null): TaggedTriageReport | null {
  if (!markdown) return null;

  const rootMatch = markdown.match(
    new RegExp(`<triage_report\\b[^>]*version=["']${escapeRegExp(TRIAGE_REPORT_VERSION)}["'][^>]*>([\\s\\S]*?)</triage_report>`, 'i'),
  );
  if (!rootMatch) return null;

  const body = rootMatch[1];
  const sections = {} as TriageReportSections;
  for (const tag of TRIAGE_REPORT_TAGS) {
    const match = body.match(new RegExp(`<${tag}\\b[^>]*>([\\s\\S]*?)</${tag}>`, 'i'));
    if (!match) return null;
    sections[tag] = match[1].trim();
  }

  return {
    raw: rootMatch[0].trim(),
    title: readMarkdownTitle(sections.report_title),
    stepCount: countMarkdownSteps(sections.analysis_steps),
    sections,
  };
}

export function readMarkdownTitle(markdown: string, fallback = 'Web日志分析') {
  const heading = markdown.match(/^#\s+(.+)$/m)?.[1]?.trim();
  if (heading) return heading;
  const firstLine = markdown.split(/\r?\n/).map((line) => line.trim()).find(Boolean);
  return firstLine?.replace(/^#+\s*/, '') || fallback;
}

export function countMarkdownSteps(markdown: string) {
  const numberedSteps = markdown.match(/^###\s+\d+[.、]\s+/gm);
  if (numberedSteps?.length) return numberedSteps.length;

  const headings = markdown.match(/^###\s+/gm);
  if (headings?.length) return headings.length;

  return markdown.trim() ? 1 : 0;
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
