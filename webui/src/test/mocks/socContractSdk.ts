// Match the host-provided client while allowing each test to install its SDK.
export const api = new Proxy({}, {
  get(_target, key) {
    return (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__.api[key];
  },
}) as any;
