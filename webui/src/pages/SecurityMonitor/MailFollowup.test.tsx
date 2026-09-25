import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import MailFollowup, { MailSettings } from './MailFollowup';
const mocks = vi.hoisted(() => ({ mail: vi.fn(), saveMail: vi.fn() }));
vi.mock('@/api/securityMonitoring', () => ({ monitoringApi: mocks }));
const data = {
 settings: { enabled: true, recipient_email: 'owner@example.com', responsible_name: '值班' },
 counts: { sent: 1, send_unknown: 1 }, reply_counts: { pending: 1 }, has_more: false,
 notices: [{ id: 'notice', recipient: 'owner@example.com', state: 'sent', created_at: '2026-09-25T00:00:00Z', subject: '告警通知', body: '一条告警', event: { id: 'event', name: '待跟进告警', host: 'fixture' }, items: [] }],
 replies: [{ id: 'reply', sender: 'owner@example.com', state: 'pending', received_at: '2026-09-25T00:01:00Z', payload: { subject: '处置反馈', text: '已清理并复查' }, targets: [], result: null }],
};
beforeEach(() => { vi.resetAllMocks(); mocks.mail.mockResolvedValue({ data }); });
it('distinguishes sent notices from unprocessed replies and never claims closure', async () => {
 render(<MailFollowup />);
 expect(await screen.findByText('待跟进告警')).toBeInTheDocument();
 expect(screen.getByText(/邮件已发送或收到回复均不代表处置完成/)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('tab', { name: '回信记录' }));
 expect(screen.getByText('处置反馈')).toBeInTheDocument();
 expect(screen.getByText(/已收到，待下轮处理/)).toBeInTheDocument();
 expect(screen.queryByText(/目标状态已回查确认/)).not.toBeInTheDocument();
});
it('saves recipient settings and closes after the refreshed state', async () => {
 const close = vi.fn(), refresh = vi.fn().mockResolvedValue(undefined); mocks.saveMail.mockResolvedValue({});
 render(<MailSettings close={close} refresh={refresh} />);
 fireEvent.change(await screen.findByLabelText('责任人邮箱'), { target: { value: 'new@example.com' } });
 fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
 await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
 expect(mocks.saveMail).toHaveBeenCalledWith({ enabled: true, recipient_email: 'new@example.com', responsible_name: '值班' });
 expect(refresh).toHaveBeenCalledTimes(1);
});
it.each([
 [{ error: 'HTTPException', message: '请先连接 Flocks 邮件通道' }, '请先连接 Flocks 邮件通道'],
 [{ detail: '请先连接 Flocks 邮件通道' }, '请先连接 Flocks 邮件通道'],
 [{ message: { invalid: true }, detail: [] }, '邮件配置保存失败'],
])('keeps configuration open and displays the server error %j', async (data, expected) => {
 mocks.saveMail.mockRejectedValue({ response: { data } });
 const close = vi.fn(); render(<MailSettings close={close} refresh={vi.fn()} />);
 fireEvent.click(await screen.findByRole('button', { name: '保存配置' }));
 expect(await screen.findByRole('alert')).toHaveTextContent(expected); expect(close).not.toHaveBeenCalled();
});
it('shows the related alert and verified status', async () => {
 mocks.mail.mockResolvedValue({ data: { ...data, replies: [{ ...data.replies[0], state: 'verified', targets: [{ event_id: 'event', name: '待跟进告警', state: 'verified', target: 40 }] }] } });
 render(<MailFollowup />);
 fireEvent.click(await screen.findByRole('tab', { name: '回信记录' }));
 expect(screen.getByText(/告警：待跟进告警 · event · 目标状态已回查确认/)).toBeInTheDocument();
});

it('surfaces unsupported mail without hiding normal follow-up records', async () => {
 mocks.mail.mockResolvedValue({ data: { ...data, unparsed_count: 2 } });
 render(<MailFollowup />);
 expect(await screen.findByRole('alert')).toHaveTextContent('2 封邮件未能解析');
 expect(screen.getByText('待跟进告警')).toBeInTheDocument();
});

it('shows the development authentication risk in settings before saving', async () => {
 mocks.mail.mockResolvedValue({ data: { ...data, sender_verification_required: false } });
 render(<MailSettings close={vi.fn()} refresh={vi.fn()} />);
 expect(await screen.findByText(/开发联调模式：暂不要求回信身份认证/)).toBeInTheDocument();
 expect(screen.getByText(/伪造回信触发状态标记的风险/)).toBeInTheDocument();
});

it('labels unverified feedback separately from the disposition result', async () => {
 mocks.mail.mockResolvedValue({ data: { ...data, sender_verification_required: false, replies: [{
  ...data.replies[0], payload: { ...data.replies[0].payload, authenticated_sender: false, sender_verification_bypassed: true },
 }] } });
 render(<MailFollowup />);
 fireEvent.click(await screen.findByRole('tab', { name: '回信记录' }));
 expect(screen.getByText('此回信未验证发件人身份，按开发联调规则处理。')).toBeInTheDocument();
 expect(screen.getByText(/已收到，待下轮处理/)).toBeInTheDocument();
 expect(screen.queryByText(/目标状态已回查确认/)).not.toBeInTheDocument();
});
