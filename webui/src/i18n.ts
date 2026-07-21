import i18n, { type BackendModule } from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import {
  INITIAL_NAMESPACES,
  initialI18nResources,
  preloadI18nNamespaces,
  registerI18nInstance,
  readLazyLocale,
} from './i18nResources';

const lazyLocaleBackend: BackendModule = {
  type: 'backend',
  init() {},
  read: readLazyLocale,
};

i18n
  .use(lazyLocaleBackend)
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: initialI18nResources,
    fallbackLng: 'en-US',
    defaultNS: 'common',
    ns: INITIAL_NAMESPACES,
    partialBundledLanguages: true,
    detection: {
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: 'flocks-language',
      caches: ['localStorage'],
    },
    interpolation: {
      escapeValue: false,
    },
  });

registerI18nInstance(i18n);

export { preloadI18nNamespaces };
export default i18n;
