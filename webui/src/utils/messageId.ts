const BASE62 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz';
const RANDOM_SUFFIX_LENGTH = 14;

let lastTimestamp = 0;
let counter = 0;

export function createMessageId(timestamp = Date.now()): string {
  if (timestamp !== lastTimestamp) {
    lastTimestamp = timestamp;
    counter = 0;
  }
  counter += 1;

  const encodedTime = BigInt(timestamp) * 0x1000n + BigInt(counter);
  const timeHex = encodedTime.toString(16).slice(-12).padStart(12, '0');
  const randomBytes = new Uint8Array(RANDOM_SUFFIX_LENGTH);
  globalThis.crypto.getRandomValues(randomBytes);
  const randomSuffix = Array.from(
    randomBytes,
    (byte) => BASE62[byte % BASE62.length],
  ).join('');

  return `msg_${timeHex}${randomSuffix}`;
}
