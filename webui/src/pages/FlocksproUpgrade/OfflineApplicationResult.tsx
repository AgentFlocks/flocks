import type { OfflineApplication } from '@/api/enterpriseUpgrade';

// The application made offline, as it is handed over: saved as a file the moment it is made. Whoever issues
// the licence enters the machine fingerprint in Console's offline issue, which binds the licence to this
// machine, and sends back the license.json it downloads.

/** Saves the application as a file, the way it is handed over. */
export function downloadApplication(application: OfflineApplication): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(application, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `flocks-pro-application-${application.machine ? application.machine.slice(0, 12) : 'unbound'}.json`;
  link.click();
  URL.revokeObjectURL(url);
}
