const runtime = globalThis.__FLOCKS_USER_DEFINED_PAGE_SDK__;
if (!runtime?.jsx || !runtime?.jsxs) {
  throw new Error('Flocks user-defined page runtime is not initialized (missing jsx runtime).');
}
export const jsx = runtime.jsx;
export const jsxs = runtime.jsxs;
export const Fragment = runtime.React.Fragment;
