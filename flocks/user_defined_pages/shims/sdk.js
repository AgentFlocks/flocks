const sdk = globalThis.__FLOCKS_USER_DEFINED_PAGE_SDK__;
if (!sdk) {
  throw new Error('Flocks user-defined page runtime is not initialized (missing SDK).');
}
export const api = sdk.api;
export const Card = sdk.Card;
export const useCurrentUser = sdk.useCurrentUser;
