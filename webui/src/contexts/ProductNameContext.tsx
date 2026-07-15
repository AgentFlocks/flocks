import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { uiConfigApi, type UIDisplayConfig } from '@/api/uiConfig';

const DEFAULT_PRODUCT_NAME = (import.meta.env.VITE_APP_NAME || 'Flocks').trim() || 'Flocks';
const DEFAULT_FAVICON_URL = '/favicon.svg';

interface ProductNameContextValue {
  productName: string;
  configuredDisplayName: string | null;
  faviconUrl: string;
  hasCustomFavicon: boolean;
  loading: boolean;
  refreshProductName: () => Promise<void>;
  updateProductName: (displayName: string | null) => Promise<void>;
  uploadProductFavicon: (file: File) => Promise<void>;
  resetProductFavicon: () => Promise<void>;
}

const ProductNameContext = createContext<ProductNameContextValue>({
  productName: DEFAULT_PRODUCT_NAME,
  configuredDisplayName: null,
  faviconUrl: DEFAULT_FAVICON_URL,
  hasCustomFavicon: false,
  loading: true,
  refreshProductName: async () => undefined,
  updateProductName: async () => undefined,
  uploadProductFavicon: async () => undefined,
  resetProductFavicon: async () => undefined,
});

function normalizeConfig(config: UIDisplayConfig): UIDisplayConfig {
  return {
    displayName: (config.displayName || DEFAULT_PRODUCT_NAME).trim() || DEFAULT_PRODUCT_NAME,
    configuredDisplayName: config.configuredDisplayName?.trim() || null,
    faviconUrl: config.faviconUrl?.trim() || null,
  };
}

function updateDocumentFavicon(href: string) {
  let icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!icon) {
    icon = document.createElement('link');
    icon.rel = 'icon';
    document.head.appendChild(icon);
  }
  if (href === DEFAULT_FAVICON_URL) {
    icon.type = 'image/svg+xml';
  } else {
    icon.removeAttribute('type');
  }
  icon.href = href;
}

export function ProductNameProvider({ children }: { children: ReactNode }) {
  const [displayConfig, setDisplayConfig] = useState<UIDisplayConfig>({
    displayName: DEFAULT_PRODUCT_NAME,
    configuredDisplayName: null,
    faviconUrl: null,
  });
  const [loading, setLoading] = useState(true);

  const refreshProductName = useCallback(async () => {
    const next = normalizeConfig(await uiConfigApi.getDisplay());
    setDisplayConfig(next);
  }, []);

  const updateProductName = useCallback(async (displayName: string | null) => {
    const next = normalizeConfig(await uiConfigApi.update({ displayName }));
    setDisplayConfig(next);
  }, []);

  const uploadProductFavicon = useCallback(async (file: File) => {
    const next = normalizeConfig(await uiConfigApi.uploadFavicon(file));
    setDisplayConfig(next);
  }, []);

  const resetProductFavicon = useCallback(async () => {
    const next = normalizeConfig(await uiConfigApi.resetFavicon());
    setDisplayConfig(next);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    uiConfigApi.getDisplay()
      .then((config) => {
        if (!cancelled) {
          setDisplayConfig(normalizeConfig(config));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setDisplayConfig({ displayName: DEFAULT_PRODUCT_NAME, configuredDisplayName: null, faviconUrl: null });
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    document.title = displayConfig.displayName;
    updateDocumentFavicon(displayConfig.faviconUrl || DEFAULT_FAVICON_URL);
  }, [displayConfig.displayName, displayConfig.faviconUrl]);

  const value = useMemo<ProductNameContextValue>(() => ({
    productName: displayConfig.displayName,
    configuredDisplayName: displayConfig.configuredDisplayName ?? null,
    faviconUrl: displayConfig.faviconUrl || DEFAULT_FAVICON_URL,
    hasCustomFavicon: Boolean(displayConfig.faviconUrl),
    loading,
    refreshProductName,
    updateProductName,
    uploadProductFavicon,
    resetProductFavicon,
  }), [
    displayConfig.configuredDisplayName,
    displayConfig.displayName,
    displayConfig.faviconUrl,
    loading,
    refreshProductName,
    resetProductFavicon,
    updateProductName,
    uploadProductFavicon,
  ]);

  return (
    <ProductNameContext.Provider value={value}>
      {children}
    </ProductNameContext.Provider>
  );
}

export function useProductName() {
  return useContext(ProductNameContext);
}
