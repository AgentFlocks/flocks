type Model = {
  id: string
}

type Provider = {
  id: string
  models: Record<string, Model>
}

export function selectFallbackModel(providers: Provider[], defaults: Record<string, string>) {
  const provider = providers.find((item) => Object.keys(item.models).length > 0)
  if (!provider) return undefined
  const defaultModel = defaults[provider.id]
  const firstModel = Object.values(provider.models)[0]
  const modelID = defaultModel && provider.models[defaultModel] ? defaultModel : firstModel?.id
  if (!modelID) return undefined
  return {
    providerID: provider.id,
    modelID,
  }
}
