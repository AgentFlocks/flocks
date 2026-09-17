import { Card } from '@flocks/webui-contract-sdk';

export default function Page() {
  return (
    <Card title="演练总览">
      <p style={{ margin: '0 0 12px', color: 'var(--flocks-text-muted, #71717a)' }}>AI 红队场景的入口：编排演练、跟踪每一轮的结论。</p>
      <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.9 }}>
        <li>选择目标系统与授权范围，创建一次演练</li>
        <li>演练过程由红队 Agent 驱动，每一步都留下可复核的记录</li>
        <li>演练结束后生成结论，可直接转成整改任务</li>
      </ul>
    </Card>
  );
}
