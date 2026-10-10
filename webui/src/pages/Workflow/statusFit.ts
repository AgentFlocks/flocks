import { useLayoutEffect, useState, type RefObject } from 'react';

/** Widths read from a card's hidden status sample, in CSS px. */
export interface StatusRowMeasurement {
  /** Width of the whole status line. */
  available: number;
  /** The status always shown first (API). */
  leadWidth: number;
  /** Candidate trigger statuses, in display order. */
  itemWidths: number[];
  /** Horizontal gap between statuses. */
  gap: number;
  /** The "+N" button including its left margin. */
  overflowWidth: number;
}

// Sub-pixel widths can add up a hair over the line width; never drop a
// status for that.
const FIT_TOLERANCE_PX = 0.5;

/**
 * How many candidate statuses fit on one line after the lead status, keeping
 * room for the "+N" button whenever some statuses are left over.
 * A row that could not be measured (zero width: tests, hidden containers)
 * keeps every candidate, i.e. the static cap.
 */
export function countFittingStatuses(
  { available, leadWidth, itemWidths, gap, overflowWidth }: StatusRowMeasurement,
  totalItems: number,
): number {
  if (!(available > 0)) return itemWidths.length;
  let used = leadWidth;
  let count = 0;
  for (let index = 0; index < itemWidths.length; index += 1) {
    const next = used + gap + itemWidths[index];
    const reserve = totalItems > index + 1 ? overflowWidth : 0;
    if (next + reserve > available + FIT_TOLERANCE_PX) break;
    used = next;
    count = index + 1;
  }
  return count;
}

function px(value: string): number {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Number of trigger statuses that fit the status line of one card.
 *
 * ``sampleRef`` points at an invisible copy of the row: the lead status, the
 * ``candidateCount`` candidates and a "+N" sample, in that order.
 * ``contentKey`` must change whenever the sampled labels do (state, language),
 * since a label can widen without the count changing. Font loading and other
 * size changes are caught by observing the line and every sampled status.
 */
export function useFittingStatusCount(
  lineRef: RefObject<HTMLElement | null>,
  sampleRef: RefObject<HTMLElement | null>,
  candidateCount: number,
  totalItems: number,
  contentKey: string,
): number {
  const [count, setCount] = useState(candidateCount);

  useLayoutEffect(() => {
    const line = lineRef.current;
    const sample = sampleRef.current;
    if (!line || !sample) {
      setCount(candidateCount);
      return undefined;
    }
    const update = () => {
      const children = Array.from(sample.children) as HTMLElement[];
      const lead = children[0];
      const overflow = children[children.length - 1];
      const items = children.slice(1, 1 + candidateCount);
      const width = (element: HTMLElement | undefined) => element?.getBoundingClientRect().width ?? 0;
      setCount(countFittingStatuses({
        available: line.clientWidth,
        leadWidth: width(lead),
        itemWidths: items.map(width),
        gap: px(getComputedStyle(sample).columnGap),
        overflowWidth: width(overflow) + (overflow ? px(getComputedStyle(overflow).marginLeft) : 0),
      }, totalItems));
    };
    update();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(update);
    observer.observe(line);
    // The sample itself is zero-width; its children carry the label widths.
    Array.from(sample.children).forEach((child) => observer.observe(child));
    return () => observer.disconnect();
  }, [lineRef, sampleRef, candidateCount, totalItems, contentKey]);

  return count;
}
