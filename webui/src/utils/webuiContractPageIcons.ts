import {
  Activity as ActivityIcon,
  AlertTriangle as AlertTriangleIcon,
  BarChart3 as BarChart3Icon,
  FileText as FileTextIcon,
  FolderOpen as FolderOpenIcon,
  LayoutDashboard as LayoutDashboardIcon,
  LineChart as LineChartIcon,
  ShieldCheck as ShieldCheckIcon,
  Shield as ShieldIcon,
  Table as TableIcon,
  type LucideIcon,
} from 'lucide-react';

const ICON_MAP: Record<string, LucideIcon> = {
  LayoutDashboard: LayoutDashboardIcon,
  BarChart3: BarChart3Icon,
  LineChart: LineChartIcon,
  Table: TableIcon,
  FileText: FileTextIcon,
  FolderOpen: FolderOpenIcon,
  Shield: ShieldIcon,
  ShieldCheck: ShieldCheckIcon,
  AlertTriangle: AlertTriangleIcon,
  Activity: ActivityIcon,
};

export function resolveWebUIContractPageIcon(name: string): LucideIcon {
  return ICON_MAP[name] || LayoutDashboardIcon;
}
