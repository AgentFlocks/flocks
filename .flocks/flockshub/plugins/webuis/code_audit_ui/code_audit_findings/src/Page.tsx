import { Card } from '@flocks/webui-contract-sdk';

export default function Page() {
  return (
    <Card title="缺陷清单">
      <p style={{ margin: '0 0 12px', color: 'var(--flocks-text-muted, #71717a)' }}>按严重级别列出代码审计发现的问题，支持指派与复核。</p>
      <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.9 }}>
        <li>按严重级别、文件、规则筛选</li>
        <li>每条缺陷保留触发它的代码位置与判定依据</li>
        <li>复核通过后标记为已处理，下次审计不再重复报告</li>
      </ul>
    </Card>
  );
}
