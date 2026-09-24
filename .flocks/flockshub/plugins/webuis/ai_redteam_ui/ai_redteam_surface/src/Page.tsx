import { Card } from '@flocks/webui-contract-sdk';

export default function Page() {
  return (
    <Card title="攻击面">
      <p style={{ margin: '0 0 12px', color: 'var(--flocks-text-muted, #71717a)' }}>汇总目标系统暴露出的攻击面与已验证的利用路径。</p>
      <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.9 }}>
        <li>按资产、入口、权限层级组织攻击面</li>
        <li>每条利用路径关联到验证它的那一次演练</li>
        <li>已修复的路径保留历史，便于回归验证</li>
      </ul>
    </Card>
  );
}
