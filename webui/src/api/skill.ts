import client from './client';

export interface SkillRequires {
  bins?: string[];
  any_bins?: string[];
  env?: string[];
}

export interface SkillInstallSpec {
  id?: string;
  kind: 'brew' | 'npm' | 'uv' | 'pip' | 'go' | 'download';
  label?: string;
  bins?: string[];
  formula?: string;
  package?: string;
  url?: string;
}

export interface Skill {
  name: string;
  description: string;
  description_cn?: string;
  location: string;
  source?: string;
  content?: string;
  category?: string;
  // Eligibility
  eligible?: boolean;
  missing?: string[];
  requires?: SkillRequires;
  install_specs?: SkillInstallSpec[];
}

export interface Command {
  name: string;
  description: string;
  template: string;
  agent?: string;
  model?: string;
  subtask?: boolean;
  hidden: boolean;
}

export interface SkillInstallRequest {
  /** Install source: clawhub:<name>, github:<owner>/<repo>, https://..., /local/path */
  source: string;
  /** 'global' (default) or 'project' */
  scope?: string;
}

export interface SkillInstallResponse {
  success: boolean;
  skill_name?: string;
  location?: string;
  message: string;
  error?: string;
}

export interface DepInstallSpecResult {
  success: boolean;
  spec_id?: string;
  command: string[];
  stdout: string;
  stderr: string;
  returncode: number;
  error?: string;
}

export interface DepInstallResponse {
  results: DepInstallSpecResult[];
}

export const skillAPI = {
  list: () =>
    client.get<Skill[]>('/api/skills'),

  /** List all skills with eligibility status (bin/env checks). */
  status: () =>
    client.get<Skill[]>('/api/skills/status'),

  get: (name: string) =>
    client.get<Skill>(`/api/skills/${name}`),

  create: (data: {
    name: string;
    description: string;
    description_cn?: string;
    content: string;
  }) =>
    client.post<Skill>('/api/skills', data),

  update: (name: string, data: {
    name: string;
    description: string;
    description_cn?: string;
    content: string;
  }) =>
    client.put<Skill>(`/api/skills/${name}`, data),

  delete: (name: string) =>
    client.delete(`/api/skills/${name}`),

  refresh: () =>
    client.post('/api/skills/refresh'),

  /**
   * Install a skill from an external source.
   *
   * Supported sources:
   *   clawhub:<name>        – clawhub.com registry
   *   github:<owner>/<repo> – GitHub repo (or shorthand owner/repo)
   *   https://...           – direct URL to SKILL.md
   *   /local/path           – local filesystem
   *   safeskill:<name>      – SafeSkill registry (future)
   */
  install: (req: SkillInstallRequest) =>
    client.post<SkillInstallResponse>('/api/skills/install', req),

  /**
   * Install a skill's declared tool dependencies (brew, npm, uv, pip …).
   *
   * @param name       Skill name
   * @param installId  Optional: only run the spec with this id
   * @param timeoutMs  Subprocess timeout in ms (default 300000)
   */
  installDeps: (name: string, installId?: string, timeoutMs?: number) =>
    client.post<DepInstallResponse>(`/api/skills/${name}/install-deps`, {
      install_id: installId,
      timeout_ms: timeoutMs,
    }),
};

export const commandAPI = {
  list: () =>
    client.get<Command[]>('/api/commands'),

  get: (name: string) =>
    client.get<Command>(`/api/commands/${name}`),
};
