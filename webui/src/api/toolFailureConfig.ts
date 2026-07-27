import client from './client';

export interface ToolFailureConfig {
  disableOnRepeatedFailure: boolean;
}

export const toolFailureConfigApi = {
  async get(): Promise<ToolFailureConfig> {
    const response = await client.get<ToolFailureConfig>('/api/config/tool-failure');
    return response.data;
  },

  async update(disableOnRepeatedFailure: boolean): Promise<ToolFailureConfig> {
    const response = await client.patch<ToolFailureConfig>('/api/config/tool-failure', {
      disableOnRepeatedFailure,
    });
    return response.data;
  },
};
