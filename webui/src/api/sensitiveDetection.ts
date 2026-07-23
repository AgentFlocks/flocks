import client from './client';

export type PromptRedactionPlaceholderFormat = 'verbose' | 'compact';

export interface PromptRedactionSettings {
  enabled: boolean;
  categories: string[] | null;
  placeholderFormat: PromptRedactionPlaceholderFormat;
  promptHintEnabled: boolean;
}

export interface PromptRedactionSettingsUpdate {
  enabled?: boolean;
  categories?: string[] | null;
  placeholderFormat?: PromptRedactionPlaceholderFormat;
  promptHintEnabled?: boolean;
}

function normalizeSettings(data: any): PromptRedactionSettings {
  const raw = data?.data ?? data ?? {};
  return {
    enabled: raw.enabled === true,
    categories: Array.isArray(raw.categories) ? raw.categories : null,
    placeholderFormat: raw.placeholderFormat === 'compact' ? 'compact' : 'verbose',
    promptHintEnabled: raw.promptHintEnabled !== false,
  };
}

export const sensitiveDetectionApi = {
  getPromptRedactionSettings: async (): Promise<PromptRedactionSettings> => {
    const response = await client.get('/api/flockspro/sensitive-detection/settings');
    return normalizeSettings(response.data);
  },

  updatePromptRedactionSettings: async (
    payload: PromptRedactionSettingsUpdate,
  ): Promise<PromptRedactionSettings> => {
    const response = await client.patch('/api/flockspro/sensitive-detection/settings', payload);
    return normalizeSettings(response.data);
  },
};
