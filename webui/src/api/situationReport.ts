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
  datasetID?: string | null;
  language?: 'zh-CN' | 'en-US' | null;
}

export interface SituationReportDebugDataset {
  datasetID: string;
  name: string;
  description: string;
  language: 'zh-CN' | 'en-US';
  sourceSessionID: string | null;
  capturedAt: string;
  materialCount: number;
  materialDetailCount: number;
  sourceCounts: Record<string, number>;
}

export interface SituationReportDebugDatasetBinding {
  sessionID: string;
  datasetID: string;
  templateVersion: number;
  materialVersion: number;
  materialCount: number;
  materialDetailCount: number;
  language: 'zh-CN' | 'en-US';
}

export const situationReportAPI = {
  listDebugDatasets: async (): Promise<SituationReportDebugDataset[]> => {
    const response = await client.get<{ items: SituationReportDebugDataset[] }>(
      '/api/situation-report/debug/datasets',
    );
    return response.data.items;
  },
  bindDebugDataset: async (
    sessionID: string,
    datasetID: string,
  ): Promise<SituationReportDebugDatasetBinding> => {
    const response = await client.post<SituationReportDebugDatasetBinding>(
      `/api/situation-report/debug/session/${sessionID}/dataset`,
      { datasetID },
    );
    return response.data;
  },
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
