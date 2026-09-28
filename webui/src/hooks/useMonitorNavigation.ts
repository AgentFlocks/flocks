/**
 * Compatibility hook for the application shell. Monitoring is background work:
 * starts, retries, reconnects and snapshots must never take over the user's route.
 * The monitoring page owns its updates; only its explicit workbench link navigates.
 */
export function useMonitorNavigation(_ownerID: string | undefined): void {}
