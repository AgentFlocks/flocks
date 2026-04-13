export type EnvLike = Record<string, string | undefined>;

export const ADDITIONAL_ALLOWED_HOSTS_ENV = '__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS';

export function getAdditionalAllowedHosts(env: EnvLike): string[] | undefined {
  const configuredHosts = env[ADDITIONAL_ALLOWED_HOSTS_ENV];
  if (!configuredHosts) {
    return undefined;
  }

  const extraHosts = Array.from(new Set(configuredHosts
    .split(',')
    .map((host) => host.trim())
    .filter(Boolean)));

  return extraHosts.length > 0 ? extraHosts : undefined;
}
