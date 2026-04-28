import client from './client';

export type NotificationKind = 'benefit' | 'whats_new' | 'announcement';

export interface NotificationAction {
  label: string;
  url?: string | null;
}

export interface UserNotification {
  id: string;
  kind: NotificationKind;
  title: string;
  summary?: string | null;
  body?: string | null;
  highlights: string[];
  primary_action?: NotificationAction | null;
  secondary_action?: NotificationAction | null;
  version?: string | null;
  priority: number;
}

export interface NotificationAck {
  notification_id: string;
  user_id: string;
  acknowledged_at: string;
}

export const getActiveNotifications = async (
  locale?: string,
  currentVersion?: string | null,
): Promise<UserNotification[]> => {
  const response = await client.get<UserNotification[]>('/api/notifications/active', {
    params: {
      ...(locale ? { locale } : {}),
      ...(currentVersion ? { current_version: currentVersion } : {}),
    },
  });
  return response.data;
};

export const ackNotification = async (notificationId: string): Promise<NotificationAck> => {
  const response = await client.post<NotificationAck>(
    `/api/notifications/${encodeURIComponent(notificationId)}/ack`,
  );
  return response.data;
};
