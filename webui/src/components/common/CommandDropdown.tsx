/**
 * CommandDropdown — slash 命令自动补全下拉面板
 *
 * 当用户在输入框中输入 "/" 时显示，支持：
 * - 实时过滤（根据已输入的命令前缀）
 * - 键盘导航（↑↓ 选择，Enter/Tab 确认，Escape 关闭）
 * - 鼠标点击选择
 */

import { useEffect, useRef } from 'react';
import { Command } from '@/api/skill';

export interface CommandDropdownProps {
  /** 是否显示下拉面板 */
  visible: boolean;
  /** 用户已输入的查询前缀（不含 /），用于过滤 */
  query: string;
  /** 可用命令列表 */
  commands: Command[];
  /** 当前高亮选中的命令索引 */
  selectedIndex: number;
  /** 用户选择某个命令时的回调 */
  onSelect: (command: Command) => void;
}

export default function CommandDropdown({
  visible,
  query,
  commands,
  selectedIndex,
  onSelect,
}: CommandDropdownProps) {
  const listRef = useRef<HTMLDivElement>(null);

  const filtered = commands.filter(
    (cmd) =>
      !cmd.hidden &&
      (query === '' || cmd.name.toLowerCase().startsWith(query.toLowerCase())),
  );

  // 自动滚动使选中项可见
  useEffect(() => {
    if (!listRef.current) return;
    const item = listRef.current.children[selectedIndex] as HTMLElement | undefined;
    item?.scrollIntoView({ block: 'nearest' });
  }, [selectedIndex]);

  if (!visible || filtered.length === 0) return null;

  return (
    <div
      className="absolute bottom-full left-0 right-0 mb-1 z-50 bg-white border border-gray-200 rounded-lg shadow-lg overflow-hidden"
      onMouseDown={(e) => e.preventDefault()}
    >
      <div className="px-3 py-1.5 text-[10px] font-semibold text-gray-400 uppercase tracking-wide border-b border-gray-100 bg-gray-50">
        Slash Commands
      </div>
      <div ref={listRef} className="overflow-y-auto" style={{ maxHeight: '240px' }}>
        {filtered.map((cmd, idx) => (
          <button
            key={cmd.name}
            className={`w-full text-left px-3 py-2 flex items-start gap-2 transition-colors ${
              idx === selectedIndex
                ? 'bg-red-50 text-red-700'
                : 'text-gray-800 hover:bg-gray-50'
            }`}
            onClick={() => onSelect(cmd)}
            onMouseDown={(e) => e.preventDefault()}
          >
            <span className="flex-shrink-0 font-mono text-sm font-semibold min-w-[120px]">
              /{cmd.name}
            </span>
            <span className="text-xs text-gray-500 leading-relaxed mt-0.5">
              {cmd.description}
            </span>
          </button>
        ))}
      </div>
      <div className="px-3 py-1 text-[10px] text-gray-400 border-t border-gray-100 bg-gray-50 flex gap-3">
        <span><kbd className="font-mono">↑↓</kbd> 导航</span>
        <span><kbd className="font-mono">Enter</kbd>/<kbd className="font-mono">Tab</kbd> 选择</span>
        <span><kbd className="font-mono">Esc</kbd> 关闭</span>
      </div>
    </div>
  );
}

/**
 * 从输入文本中解析 slash 命令的名称和参数
 * 例如 "/bug describe issue" → { command: "bug", args: "describe issue" }
 */
export function isSlashCommandName(command: string): boolean {
  return command.length > 0 && !/[\\/]/.test(command);
}

export function parseSlashCommand(text: string): { command: string; args: string } | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith('/')) return null;
  const withoutSlash = trimmed.slice(1);
  const spaceIndex = withoutSlash.indexOf(' ');
  const command = spaceIndex === -1 ? withoutSlash : withoutSlash.slice(0, spaceIndex);
  if (!isSlashCommandName(command)) {
    return null;
  }
  return {
    command,
    args: spaceIndex === -1 ? '' : withoutSlash.slice(spaceIndex + 1).trim(),
  };
}
