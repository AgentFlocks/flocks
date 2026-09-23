import { useCallback, useEffect, useState } from 'react';
import {
  LAYOUT_OPEN_TABS_CHANGED_EVENT,
  readOpenTabs,
  saveOpenTabs,
  type OpenTabRecord,
} from '@/utils/layoutTabs';

/** Open-tab records for the main layout, kept in sync with localStorage. */
export function useLayoutOpenTabs(): [OpenTabRecord[], (records: OpenTabRecord[]) => void] {
  const [records, setRecords] = useState<OpenTabRecord[]>(readOpenTabs);

  useEffect(() => {
    const handleChange = () => setRecords(readOpenTabs());
    window.addEventListener(LAYOUT_OPEN_TABS_CHANGED_EVENT, handleChange);
    return () => window.removeEventListener(LAYOUT_OPEN_TABS_CHANGED_EVENT, handleChange);
  }, []);

  const update = useCallback((next: OpenTabRecord[]) => {
    setRecords(next);
    saveOpenTabs(next);
  }, []);

  return [records, update];
}
