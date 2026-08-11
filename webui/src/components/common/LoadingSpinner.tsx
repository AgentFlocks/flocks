import { Loader2 } from 'lucide-react';
import { useDelayedVisible } from '@/hooks/useDelayedVisible';

interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
  delayMs?: number;
}

export default function LoadingSpinner({ size = 'md', className = '', delayMs = 0 }: LoadingSpinnerProps) {
  const visible = useDelayedVisible(delayMs);
  const sizeClasses = {
    sm: 'w-4 h-4',
    md: 'w-8 h-8',
    lg: 'w-12 h-12',
  };

  if (!visible) return null;

  return (
    <div role="status" className={`flex items-center justify-center ${className}`}>
      <Loader2 className={`${sizeClasses[size]} animate-spin text-red-600`} />
    </div>
  );
}
