import type { ReactNode } from 'react';

/** The heading of the card that says where the machine stands, and what can be done from there. */
export default function MachineHeading({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between max-md:flex-wrap max-md:gap-3">
      <div className="min-w-0">
        <h2 id="flockspro-machine" className="break-words text-lg font-semibold text-gray-900">{title}</h2>
        <p className="mt-1 text-sm text-gray-500">{description}</p>
      </div>
      {action}
    </div>
  );
}
