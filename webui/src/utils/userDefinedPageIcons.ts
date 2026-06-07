import type { LucideIcon } from 'lucide-react';
import {
  BarChart3,
  FileText,
  FolderOpen,
  LayoutDashboard,
  LineChart,
  Shield,
  Table,
} from 'lucide-react';

const ICON_MAP: Record<string, LucideIcon> = {
  LayoutDashboard,
  BarChart3,
  LineChart,
  Table,
  FileText,
  FolderOpen,
  Shield,
};

export function resolveUserDefinedPageIcon(name: string): LucideIcon {
  return ICON_MAP[name] || LayoutDashboard;
}
