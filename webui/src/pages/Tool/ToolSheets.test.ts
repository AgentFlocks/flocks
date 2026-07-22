import { describe, expect, it } from 'vitest';

import { buildMCPConfigFromForm, buildMCPFormDataFromConfig, getMCPFormError } from './ToolSheets';

describe('ToolSheets MCP helpers', () => {
  it('builds remote MCP config with transport and bearer auth', () => {
    const config = buildMCPConfigFromForm({
      name: 'qradar',
      connType: 'sse',
      command: '',
      args: '',
      url: 'https://example.com/sse',
      transport: 'sse',
      authType: 'bearer',
      authValue: 'token-123',
      authHeaderName: 'X-API-Key',
      authQueryName: 'apikey',
      headersText: '{\n  "X-Client": "flocks"\n}',
    });

    expect(config).toEqual({
      type: 'sse',
      url: 'https://example.com/sse',
      transport: 'sse',
      headers: {
        'X-Client': 'flocks',
      },
      auth: {
        type: 'apikey',
        scheme: 'bearer',
        location: 'header',
        param_name: 'Authorization',
        value: 'token-123',
      },
    });
  });

  it('parses remote MCP config back into form data', () => {
    const formData = buildMCPFormDataFromConfig('qradar', {
      type: 'sse',
      url: 'https://example.com/sse',
      transport: 'sse',
      headers: {
        'X-Client': 'flocks',
      },
      auth: {
        scheme: 'bearer',
        location: 'header',
        param_name: 'Authorization',
        value: '{secret:qradar_mcp_key}',
      },
    });

    expect(formData.transport).toBe('sse');
    expect(formData.authType).toBe('bearer');
    expect(formData.authValue).toBe('{secret:qradar_mcp_key}');
    expect(formData.headersText).toContain('"X-Client": "flocks"');
  });

  it('flags invalid extra headers json', () => {
    expect(getMCPFormError({
      name: 'demo',
      connType: 'sse',
      command: '',
      args: '',
      url: 'https://example.com/sse',
      transport: 'auto',
      authType: 'none',
      authValue: '',
      authHeaderName: 'X-API-Key',
      authQueryName: 'apikey',
      headersText: '{invalid json}',
    })).toBe('invalidHeaders');
  });
});
