// Projects (history folders) are listed by the sidebar and by the composer's
// project picker; either side announces a change so the other one refetches.
export const PROJECTS_CHANGED_EVENT = 'flocks:projects-changed';

export type ProjectsChangedSource = 'sidebar' | 'composer';

export function announceProjectsChanged(source: ProjectsChangedSource): void {
  window.dispatchEvent(new CustomEvent(PROJECTS_CHANGED_EVENT, { detail: { source } }));
}

/** Subscribe to changes announced by the other side; returns the unsubscribe function. */
export function onProjectsChanged(self: ProjectsChangedSource, listener: () => void): () => void {
  const handle = (event: Event) => {
    if ((event as CustomEvent<{ source?: string }>).detail?.source === self) return;
    listener();
  };
  window.addEventListener(PROJECTS_CHANGED_EVENT, handle);
  return () => window.removeEventListener(PROJECTS_CHANGED_EVENT, handle);
}
