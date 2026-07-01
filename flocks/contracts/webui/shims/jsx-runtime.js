const runtime = globalThis.__FLOCKS_WEBUI_CONTRACT_SDK__;
if (!runtime?.jsx || !runtime?.jsxs) {
  throw new Error('Flocks WebUI page runtime is not initialized (missing jsx runtime).');
}
export const jsx = runtime.jsx;
export const jsxs = runtime.jsxs;
export const Fragment = runtime.React.Fragment;
