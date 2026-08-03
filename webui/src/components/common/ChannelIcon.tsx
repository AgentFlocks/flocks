import { MessageSquare } from 'lucide-react';

type ChannelIconSize = 'xs' | 'sm' | 'md';

const CHANNEL_ICON_SRC: Record<string, string> = {
  feishu: '/channel-feishu.png',
  wecom: '/channel-wecom.png',
  telegram: '/channel-telegram.png',
  email: '/channel-email.png',
  whatsapp: '/channel-whatsapp.png',
  slack: '/channel-slack.png',
};

const CHANNEL_MASK_ICON: Record<string, { src: string; color: string }> = {
  dingtalk: { src: '/channel-dingtalk-transparent.png', color: '#1677ff' },
  weixin: { src: '/channel-weixin-transparent.png', color: '#07c160' },
};

export default function ChannelIcon({
  channelId,
  size = 'sm',
}: {
  channelId: string;
  size?: ChannelIconSize;
}) {
  const id = channelId.trim().toLowerCase();
  const compact = size === 'xs';
  const containerSize = size === 'md' ? 'h-10 w-10' : compact ? 'h-3.5 w-3.5' : 'h-9 w-9';
  const iconSize = size === 'md' ? 'h-7 w-7' : compact ? 'h-3.5 w-3.5' : 'h-6 w-6';
  const src = CHANNEL_ICON_SRC[id];
  const maskIcon = CHANNEL_MASK_ICON[id];
  const containerClass = compact
    ? `${containerSize} inline-flex shrink-0 items-center justify-center`
    : `${containerSize} flex shrink-0 items-center justify-center rounded-xl border border-gray-100 shadow-sm`;

  if (!src && !maskIcon) {
    const fallbackClass = compact
      ? containerClass
      : `${containerSize} flex shrink-0 items-center justify-center rounded-xl bg-gray-100`;
    return (
      <span className={fallbackClass} data-channel-icon={id}>
        <MessageSquare
          aria-label={id}
          className={compact ? 'h-3.5 w-3.5 text-gray-400' : 'h-5 w-5 text-gray-400'}
        />
      </span>
    );
  }

  return (
    <span className={compact ? containerClass : `${containerClass} bg-white`} data-channel-icon={id}>
      {maskIcon ? (
        <span
          role="img"
          aria-label={id}
          className={`${iconSize} block`}
          style={{
            backgroundColor: maskIcon.color,
            WebkitMaskImage: `url(${maskIcon.src})`,
            maskImage: `url(${maskIcon.src})`,
            WebkitMaskPosition: 'center',
            maskPosition: 'center',
            WebkitMaskRepeat: 'no-repeat',
            maskRepeat: 'no-repeat',
            WebkitMaskSize: 'contain',
            maskSize: 'contain',
          }}
        />
      ) : (
        <img src={src} alt={id} className={`${iconSize} object-contain`} />
      )}
    </span>
  );
}
