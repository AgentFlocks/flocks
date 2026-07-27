import { describe, expect, it } from 'vitest';
import { parseSlashCommand } from './CommandDropdown';

describe('parseSlashCommand', () => {
  it('parses normal slash commands', () => {
    expect(parseSlashCommand('/help')).toEqual({ command: 'help', args: '' });
    expect(parseSlashCommand('/plan build the thing')).toEqual({
      command: 'plan',
      args: 'build the thing',
    });
  });

  it('does not treat absolute filesystem paths as commands', () => {
    expect(parseSlashCommand('/tmp/workspace/workflow.md')).toBeNull();
    expect(parseSlashCommand('/tmp/rex_integration_guide.md\n\nuse this')).toBeNull();
  });
});
