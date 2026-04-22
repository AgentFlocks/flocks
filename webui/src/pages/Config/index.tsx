import { useState, useEffect } from 'react';
import { Settings, Save, RotateCcw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import client from '@/api/client';

export default function ConfigPage() {
  const { t } = useTranslation('config');
  const [config, setConfig] = useState<Record<string, any>>({});
  const [editedConfig, setEditedConfig] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchConfig();
  }, []);

  const fetchConfig = async () => {
    try {
      setLoading(true);
      const response = await client.get('/api/config');
      setConfig(response.data);
      setEditedConfig(JSON.stringify(response.data, null, 2));
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    try {
      const parsedConfig = JSON.parse(editedConfig);
      setSaving(true);
      await client.put('/api/config', parsedConfig);
      setConfig(parsedConfig);
      alert(t('editor.saved'));
    } catch (err: any) {
      if (err instanceof SyntaxError) {
        alert(t('editor.jsonError'));
      } else {
        alert(`${t('editor.saveFailed')}: ${err.message}`);
      }
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setEditedConfig(JSON.stringify(config, null, 2));
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-red-600 mb-4">{error}</p>
          <button
            onClick={fetchConfig}
            className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
          >
            {t('common:button.retry')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<Settings className="w-8 h-8" />}
      />

      <div className="flex-1 bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden flex flex-col">
        <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">{t('editor.title')}</h3>
            <p className="text-sm text-gray-600 mt-1">{t('editor.description')}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleReset}
              className="flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50"
            >
              <RotateCcw className="w-5 h-5" />
              {t('editor.reset')}
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50"
            >
              <Save className="w-5 h-5" />
              {saving ? t('editor.saving') : t('editor.save')}
            </button>
          </div>
        </div>

        <div className="flex-1 p-6 overflow-auto">
          <textarea
            value={editedConfig}
            onChange={(e) => setEditedConfig(e.target.value)}
            className="w-full h-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 resize-none font-mono text-sm"
            spellCheck={false}
          />
        </div>
      </div>
    </div>
  );
}
