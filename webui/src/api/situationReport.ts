import client from './client';

export type SituationReportOperation = 'generate' | 'modify' | 'regenerate';

export interface PreparedSituationReportPrompt {
  sessionID: string;
  agent: 'situation-report-product';
  operation: SituationReportOperation;
  requestID: string;
  generationID: string;
  baseBackendReportVersion: number | null;
  prompt: string;
  displayText: string;
}

export interface SituationReportDebugSessionState {
  sessionID: string;
  reportExists: boolean;
  allowedOperations: SituationReportOperation[];
}

export const situationReportAPI = {
  getDebugSessionState: async (
    sessionID: string,
  ): Promise<SituationReportDebugSessionState> => {
    const response = await client.get<SituationReportDebugSessionState>(
      `/api/situation-report/debug/session/${sessionID}/state`,
    );
    return response.data;
  },
  prepareDebugPrompt: async (
    sessionID: string,
    operation: SituationReportOperation,
    instruction: string,
    language: 'zh-CN' | 'en-US' = 'zh-CN',
  ): Promise<PreparedSituationReportPrompt> => {
    const response = await client.post<PreparedSituationReportPrompt>(
      `/api/situation-report/debug/session/${sessionID}/prepare`,
      { operation, instruction, language },
    );
    return response.data;
  },
};
