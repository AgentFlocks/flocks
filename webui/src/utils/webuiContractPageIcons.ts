import type { TablerIcon } from '@tabler/icons-react';
import {
  IconActivity,
  IconAlertTriangle,
  IconChartBar,
  IconFileText,
  IconFolderOpen,
  IconLayoutDashboard,
  IconChartLine,
  IconShield,
  IconShieldCheck,
  IconTable,
} from '@tabler/icons-react';

const ICON_MAP: Record<string, TablerIcon> = {
  LayoutDashboard: IconLayoutDashboard,
  BarChart3: IconChartBar,
  LineChart: IconChartLine,
  Table: IconTable,
  FileText: IconFileText,
  FolderOpen: IconFolderOpen,
  Shield: IconShield,
  ShieldCheck: IconShieldCheck,
  AlertTriangle: IconAlertTriangle,
  Activity: IconActivity,
};

export function resolveWebUIContractPageIcon(name: string): TablerIcon {
  return ICON_MAP[name] || IconLayoutDashboard;
}
