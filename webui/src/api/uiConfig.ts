import client from './client';

export interface UIDisplayConfig {
  displayName: string;
  configuredDisplayName?: string | null;
  faviconUrl?: string | null;
}

export interface UIConfigUpdate {
  displayName?: string | null;
}

export const uiConfigApi = {
  async getDisplay(): Promise<UIDisplayConfig> {
    const response = await client.get<UIDisplayConfig>('/api/config/ui-display');
    return response.data;
  },

  async update(config: UIConfigUpdate): Promise<UIDisplayConfig> {
    const response = await client.patch<UIDisplayConfig>('/api/config/ui', config);
    return response.data;
  },

  async uploadFavicon(file: File): Promise<UIDisplayConfig> {
    const form = new FormData();
    form.append('file', file);
    const response = await client.post<UIDisplayConfig>('/api/config/ui/favicon', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data;
  },

  async resetFavicon(): Promise<UIDisplayConfig> {
    const response = await client.delete<UIDisplayConfig>('/api/config/ui/favicon');
    return response.data;
  },
};
