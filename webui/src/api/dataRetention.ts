import client from './client';

/** Retention days per kind of data; 0 keeps that kind forever. */
export interface DataRetentionPolicy {
  logDays: number;
  messageDays: number;
  sessionDays: number;
}

export interface DataRetentionLastRun {
  startedAt: number;
  finishedAt: number;
  sessions?: number;
  messages?: number;
  logPaths?: number;
  recordFiles?: number;
  freedBytes?: number;
  skippedBusy?: number;
  errors?: number;
  failed?: boolean;
}

export interface DataRetentionState extends DataRetentionPolicy {
  running: boolean;
  lastRun: DataRetentionLastRun | null;
}

/** What saving a policy would delete right now. */
export interface DataRetentionPreview {
  sessions: number;
  messages: number;
  logPaths: number;
  logBytes: number;
  recordFiles: number;
  recordBytes: number;
  skippedBusy: number;
}

export const dataRetentionApi = {
  async get(): Promise<DataRetentionState> {
    const response = await client.get<DataRetentionState>('/api/config/data-retention');
    return response.data;
  },

  async preview(policy: DataRetentionPolicy): Promise<DataRetentionPreview> {
    const response = await client.post<DataRetentionPreview>('/api/config/data-retention/preview', policy);
    return response.data;
  },

  async update(policy: DataRetentionPolicy): Promise<DataRetentionState> {
    const response = await client.patch<DataRetentionState>('/api/config/data-retention', policy);
    return response.data;
  },
};
