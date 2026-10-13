import type { LucideIcon } from 'lucide-react';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  FileText,
  FolderOpen,
  Home,
  LayoutDashboard,
  ListTodo,
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
  Home,
  ListTodo,
};

export function resolveWebUIContractPageIcon(name: string): LucideIcon {
  return ICON_MAP[name] || LayoutDashboard;
}
