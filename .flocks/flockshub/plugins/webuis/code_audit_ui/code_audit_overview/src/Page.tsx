import { Card } from '@flocks/webui-contract-sdk';

export default function Page() {
  return (
    <Card title="审计总览">
      <p style={{ margin: '0 0 12px', color: 'var(--flocks-text-muted, #71717a)' }}>代码审计场景的入口：接入仓库、发起审计、跟踪修复进度。</p>
      <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.9 }}>
        <li>在 Agent 分区的工作台里用代码审计 Agent 发起一次审计</li>
        <li>审计结果按仓库、分支、提交聚合，进入缺陷清单查看</li>
        <li>缺陷修复后重新审计，对比上一次的结论</li>
      </ul>
    </Card>
  );
}
