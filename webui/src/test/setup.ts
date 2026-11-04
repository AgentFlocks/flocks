import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';
import { resetSharedRequestsForTesting } from '@/utils/sharedRequest';

function createStorageMock(): Storage {
  let store: Record<string, string> = {};

  return {
    get length() {
      return Object.keys(store).length;
    },
    clear() {
      store = {};
    },
    getItem(key: string) {
      return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
    },
    key(index: number) {
      return Object.keys(store)[index] ?? null;
    },
    removeItem(key: string) {
      delete store[key];
    },
    setItem(key: string, value: string) {
      store[key] = String(value);
    },
  };
}

if (typeof globalThis.localStorage?.clear !== 'function') {
  const storage = createStorageMock();
  Object.defineProperty(globalThis, 'localStorage', {
    value: storage,
    configurable: true,
  });
  Object.defineProperty(window, 'localStorage', {
    value: storage,
    configurable: true,
  });
}

afterEach(() => {
  cleanup();
  // A request a test never settles must not be shared into the next test.
  resetSharedRequestsForTesting();
});

global.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

global.MutationObserver = class MutationObserver {
  observe() {}
  disconnect() {}
};
