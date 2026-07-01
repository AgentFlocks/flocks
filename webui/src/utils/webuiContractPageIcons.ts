import type { LucideIcon } from 'lucide-react';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  FileText,
  FolderOpen,
  LayoutDashboard,
  LineChart,
  Shield,
  ShieldCheck,
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
  ShieldCheck,
  AlertTriangle,
  Activity,
};

export function resolveWebUIContractPageIcon(name: string): LucideIcon {
  return ICON_MAP[name] || LayoutDashboard;
}
