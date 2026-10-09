import { describe, expect, it } from 'vitest';
import { filterLoadedPage } from './state';

const named = (...names: string[]) => names.map(name => ({ name }));

describe('filterLoadedPage', () => {
  it('keeps every loaded item for an empty or blank query', () => {
    const items = named('Notes', 'Other notes');
    expect(filterLoadedPage(items, '')).toBe(items);
    expect(filterLoadedPage(items, '   ')).toBe(items);
  });

  it('matches a trimmed, case-insensitive substring of the name', () => {
    expect(filterLoadedPage(named('Notes', 'Threat intel', '威胁情报-APT 样本库'), ' NOTES ')).toEqual(named('Notes'));
    expect(filterLoadedPage(named('威胁情报-APT 样本库', 'beacon-分析报告.pdf'), 'apt')).toEqual(named('威胁情报-APT 样本库'));
    expect(filterLoadedPage(named('Notes'), 'missing')).toEqual([]);
  });

  it('folds case like the backend casefold for Greek sigma and German sharp s', () => {
    expect(filterLoadedPage(named('ΟΣΑ'), 'ΟΣ')).toEqual(named('ΟΣΑ'));
    expect(filterLoadedPage(named('Straße'), 'STRASSE')).toEqual(named('Straße'));
    expect(filterLoadedPage(named('STRASSE'), 'straße')).toEqual(named('STRASSE'));
    expect(filterLoadedPage(named('STRAẞE'), 'STRASSE')).toEqual(named('STRAẞE'));
    expect(filterLoadedPage(named('Straße'), 'STRAẞE')).toEqual(named('Straße'));
    expect(filterLoadedPage(named('ﬁle report'), 'FILE')).toEqual(named('ﬁle report'));
  });

  it('keeps dotless ı distinct from i, as casefold does', () => {
    expect(filterLoadedPage(named('LICENSE'), 'lıcense')).toEqual([]);
    expect(filterLoadedPage(named('lıcense'), 'LICENSE')).toEqual([]);
    expect(filterLoadedPage(named('Kırmızı'), 'KIRMIZI')).toEqual([]);
    expect(filterLoadedPage(named('Kırmızı'), 'kırm')).toEqual(named('Kırmızı'));
  });
});
