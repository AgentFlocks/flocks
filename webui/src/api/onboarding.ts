import client from './client';

export type OnboardingRegion = 'cn' | 'global';

export interface ThirdPartyLLMInput {
  provider_id: string;
  api_key: string;
  model_id: string;
  base_url?: string;
  provider_name?: string;
}

export interface OnboardingRequest {
  region: OnboardingRegion;
  use_threatbook_model: boolean;
  threatbook_api_key?: string | null;
  third_party_llm?: ThirdPartyLLMInput | null;
  threatbook_services_only?: boolean;
  threatbook_model_only?: boolean;
}

export interface ResourceValidationResult {
  enabled: boolean;
  success: boolean | null;
  code?: string | null;
  message?: string | null;
  latency_ms?: number | null;
  details: Record<string, any>;
}

export interface OnboardingValidateResponse {
  success: boolean;
  can_apply: boolean;
  threatbook_enabled: boolean;
  threatbook_key_valid?: boolean | null;
  threatbook_region_match?: boolean | null;
  suggested_region?: OnboardingRegion | null;
  error_code?: string | null;
  message?: string | null;
  threatbook_resources: string[];
  third_party_llm_valid?: boolean | null;
  resource_results: Record<string, ResourceValidationResult>;
}

export interface OnboardingApplyResponse {
  success: boolean;
  message: string;
  region: OnboardingRegion;
  threatbook_enabled: boolean;
  configured: string[];
  skipped: string[];
  default_model?: {
    provider_id: string;
    model_id: string;
  } | null;
}

export interface OnboardingStatusResponse {
  completed: boolean;
  has_default_model: boolean;
  default_model?: {
    provider_id: string;
    model_id: string;
  } | null;
  threatbook_intel?: {
    configured: boolean;
    region?: OnboardingRegion | null;
    api_configured: boolean;
    api_service_id?: string | null;
    mcp_configured: boolean;
    mcp_connected: boolean;
    mcp_status: string;
    mcp_name?: string | null;
    service_matrix: Record<string, string[]>;
  };
}

export const onboardingAPI = {
  getStatus: () => client.get<OnboardingStatusResponse>('/api/onboarding/status'),

  validate: (payload: OnboardingRequest) =>
    client.post<OnboardingValidateResponse>('/api/onboarding/validate', payload),

  apply: (payload: OnboardingRequest) =>
    client.post<OnboardingApplyResponse>('/api/onboarding/apply', payload),
};
