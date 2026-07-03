const sdk = globalThis.__FLOCKS_WEBUI_CONTRACT_SDK__;
if (!sdk) {
  throw new Error('Flocks WebUI page runtime is not initialized (missing SDK).');
}
export const api = sdk.api;
export const contract = sdk.api.contract;
export const Card = sdk.Card;
export const useCurrentUser = sdk.useCurrentUser;
