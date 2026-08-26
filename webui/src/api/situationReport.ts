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

export const situationReportAPI = {
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
