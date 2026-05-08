/**
 * Image upload helpers shared by the session composer.
 *
 * This module owns the rules that turn a user-selected ``File`` into something
 * the LLM API can actually accept:
 *
 *   1. Detection      — ``isImageFile`` decides whether to take the image path
 *                       (re-encode + base64) or the document path (multipart
 *                       upload to the backend).
 *   2. Encoding       — ``readFileAsDataUrl`` turns a ``File`` into a base64
 *                       ``data:`` URL the backend persists into a message
 *                       part. The backend then materialises it to disk
 *                       (see ``_materialize_data_url_to_disk``) before
 *                       handing it to the LLM adapter.
 *   3. Compression    — ``compressImageFile`` re-encodes images to JPEG with
 *                       a hard edge cap so a single multi-image turn stays
 *                       well under the ~1 MB body limit most upstream LLM
 *                       gateways enforce.
 *   4. Batch policy   — ``batchCompressOptions`` picks tighter compression
 *                       parameters when the user attaches several images in
 *                       one turn.
 */

/** Image MIME types we emit to the LLM API. */
export const IMAGE_MIME_TYPES = new Set([
  'image/jpeg',
  'image/png',
  'image/gif',
  'image/webp',
  'image/bmp',
]);

/** Filename extensions we treat as images even when ``File.type`` is empty. */
export const IMAGE_EXTENSIONS = new Set([
  'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp',
]);

/** ``accept`` value for the file input when only images are allowed. */
export const FILE_INPUT_ACCEPT_IMAGES = '.jpg,.jpeg,.png,.gif,.webp,.bmp';

// Cap LLM payload size. base64 inflates bytes by ~4/3, so each MB of raw
// image becomes ~1.4 MB on the wire — a single 4-image batch easily blows
// past the 1 MB body limits commonly enforced by upstream gateways and shows
// up to the user as "Connection error". We re-encode every image as JPEG
// with a hard edge cap so even a 4-image batch stays well under 1 MB total.
//
// PNG transparency is intentionally flattened onto white. For LLM image
// understanding (the only place these compressed copies are sent) the alpha
// channel almost never matters, and keeping PNG would routinely produce
// 3-5× larger payloads than JPEG for screenshots / charts.
export const IMAGE_MAX_EDGE_PX = 1280;
export const IMAGE_QUALITY = 0.82;
/** Files smaller than this are passed through untouched (default path). */
export const IMAGE_PASSTHROUGH_BYTES = 180 * 1024;

/** Wire-format payload for an image part sent in ``prompt_async``. */
export interface ImagePartData {
  /** ``data:<mime>;base64,<bytes>`` URL produced by {@link readFileAsDataUrl}. */
  url: string;
  mime: string;
  filename: string;
}

/** Lower-cased extension (without the leading dot) or ``''`` for no extension. */
export function getFileExtension(filename: string): string {
  const normalized = filename.toLowerCase();
  const idx = normalized.lastIndexOf('.');
  return idx >= 0 ? normalized.slice(idx + 1) : '';
}

/** True when ``file`` should be sent as an inline LLM image part. */
export function isImageFile(file: File): boolean {
  return (
    IMAGE_MIME_TYPES.has(file.type) ||
    IMAGE_EXTENSIONS.has(getFileExtension(file.name))
  );
}

/** Read a ``File`` as a ``data:<mime>;base64,...`` URL. */
export function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = (e) => resolve(e.target?.result as string);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function encodeJpegFromBitmap(
  bitmap: ImageBitmap,
  maxEdge: number,
  quality: number,
): Promise<Blob | null> {
  const { width, height } = bitmap;
  const longest = Math.max(width, height);
  const scale = longest > maxEdge ? maxEdge / longest : 1;
  const targetW = Math.max(1, Math.round(width * scale));
  const targetH = Math.max(1, Math.round(height * scale));

  const canvas = document.createElement('canvas');
  canvas.width = targetW;
  canvas.height = targetH;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  // Flatten alpha onto white so transparent regions don't turn black in JPEG.
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, targetW, targetH);
  ctx.drawImage(bitmap, 0, 0, targetW, targetH);
  return await new Promise((resolve) => {
    canvas.toBlob((b) => resolve(b), 'image/jpeg', quality);
  });
}

/**
 * Re-encode ``file`` as a downscaled JPEG when it would otherwise be too big
 * to send to an upstream LLM gateway. Falls back to the original ``File`` on
 * any error so the upload path stays best-effort.
 */
export async function compressImageFile(
  file: File,
  opts: { maxEdge?: number; quality?: number } = {},
): Promise<File> {
  const maxEdge = opts.maxEdge ?? IMAGE_MAX_EDGE_PX;
  const quality = opts.quality ?? IMAGE_QUALITY;
  // Tiny images go through untouched, but only when caller didn't request a
  // smaller maxEdge (a batched turn may want to squeeze even small inputs).
  if (file.size <= IMAGE_PASSTHROUGH_BYTES && opts.maxEdge === undefined) {
    return file;
  }
  const ext = getFileExtension(file.name);
  if (ext === 'gif') return file; // animation would be lost.

  let bitmap: ImageBitmap | null = null;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    return file;
  }
  try {
    const blob = await encodeJpegFromBitmap(bitmap, maxEdge, quality);
    if (!blob || blob.size >= file.size) return file;
    const newName = file.name.replace(/\.[^.]+$/, '.jpg');
    return new File([blob], newName, { type: 'image/jpeg' });
  } finally {
    bitmap.close?.();
  }
}

/**
 * Pick compression parameters based on how many images the user has attached
 * in this turn. More images → tighter cap so the combined base64 body stays
 * well under typical 1 MB body limits at upstream gateways.
 */
export function batchCompressOptions(
  count: number,
): { maxEdge: number; quality: number } {
  if (count >= 4) return { maxEdge: 768, quality: 0.78 };
  if (count >= 2) return { maxEdge: 1024, quality: 0.80 };
  return { maxEdge: IMAGE_MAX_EDGE_PX, quality: IMAGE_QUALITY };
}
