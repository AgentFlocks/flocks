import { describe, expect, it } from 'vitest';

import { buildMCPConfigFromForm, buildMCPFormDataFromConfig, clearMCPFieldErrors, getMCPFieldErrors, getMCPFormError, mcpTestFailure, mcpTestOutcome } from './ToolSheets';

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

describe('MCP form answers (v0.21 #21–#24)', () => {
  const base = {
    name: '', connType: 'sse' as const, command: '', args: '', url: '', transport: 'auto' as const,
    authType: 'none' as const, authValue: '', authHeaderName: 'X-API-Key', authQueryName: 'apikey', headersText: '',
  };
  const t = (key: string) => key;

  it('names each missing or malformed field instead of doing nothing', () => {
    expect(getMCPFieldErrors(base)).toEqual({ name: 'nameRequired', url: 'urlRequired' });
    expect(getMCPFieldErrors({ ...base, name: 'demo', url: 'example.com/mcp' })).toEqual({ url: 'urlScheme' });
    expect(getMCPFieldErrors({ ...base, name: 'demo', url: 'HTTPS://example.com/mcp', headersText: '{bad' })).toEqual({ headersText: 'invalidHeaders' });
    expect(getMCPFieldErrors({ ...base, name: 'demo', connType: 'stdio' })).toEqual({ command: 'commandRequired' });
    expect(getMCPFieldErrors({ ...base, name: 'demo', url: 'http://127.0.0.1:8080/sse' })).toEqual({});
  });

  it('drops only the errors of the fields that changed', () => {
    const errors = { name: 'nameRequired', url: 'urlScheme', headersText: 'invalidHeaders' } as const;
    expect(clearMCPFieldErrors(errors, { url: 'https://x' })).toEqual({ name: 'nameRequired', headersText: 'invalidHeaders' });
    expect(clearMCPFieldErrors(errors, { connType: 'stdio' })).toEqual({ name: 'nameRequired' });
  });

  it('says a failed connection in words and keeps the server diagnosis apart', () => {
    expect(mcpTestOutcome({ success: false, message: 'Connection failed: copy-check__test__: [Errno 16] Device or resource busy' }, t))
      .toEqual({ success: false, message: 'sheet.mcpConnectFailed', detail: 'Connection failed: copy-check__test__: [Errno 16] Device or resource busy', latency: undefined, tools_count: undefined });
    expect(mcpTestOutcome({ success: true, message: 'ok', tools_count: 3 }, t)).toMatchObject({ success: true, message: 'ok', tools_count: 3 });
    expect(mcpTestFailure({ response: { status: 500, data: { error: 'InternalServerError', message: 'Internal server error' } } }, t))
      .toEqual({ success: false, message: 'sheet.mcpConnectFailed', detail: 'Internal server error' });
    // A request the service refused never reached the MCP server.
    expect(mcpTestFailure({ response: { status: 400, data: { detail: 'MCP server url must use http:// or https://' } } }, t))
      .toEqual({ success: false, message: 'MCP server url must use http:// or https://', refused: true });
  });
});
