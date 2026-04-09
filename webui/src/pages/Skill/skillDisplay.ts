import type { Skill } from '@/api/skill';

export function getLocalizedSkillDescription(
  skill: Pick<Skill, 'description' | 'description_cn'> | null | undefined,
  language: string,
): string {
  const normalized = language.toLowerCase().replace('_', '-');
  const englishDescription = skill?.description?.trim() || '';
  const chineseDescription = skill?.description_cn?.trim() || '';

  if (normalized.startsWith('zh')) {
    return chineseDescription || englishDescription;
  }

  return englishDescription || chineseDescription;
}
