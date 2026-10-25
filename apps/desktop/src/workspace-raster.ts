import { imageSize } from "image-size";
import type { WorkspaceImage } from "./workspace-file-types.js";
export const MAX_RASTER_BYTES = 8 * 1024 * 1024;
/** WHATWG image signatures; Chromium does the actual decode inside the sandbox. */
export function rasterMediaType(bytes: Buffer): WorkspaceImage["mediaType"] | undefined {
  if (bytes.length >= 8 && bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) return "image/png";
  if (bytes.length >= 3 && bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) return "image/jpeg";
  if (bytes.length >= 6 && ["GIF87a", "GIF89a"].includes(bytes.toString("ascii", 0, 6))) return "image/gif";
  if (bytes.length >= 14 && bytes.toString("ascii", 0, 2) === "BM") return "image/bmp";
  if (bytes.length >= 16 && bytes.toString("ascii", 0, 4) === "RIFF" && bytes.toString("ascii", 8, 12) === "WEBP" && bytes.toString("ascii", 12, 15) === "VP8") return "image/webp";
  return undefined;
}

export function rasterDimensions(bytes: Buffer): { width: number; height: number } | undefined {
  if (!rasterMediaType(bytes)) return undefined;
  try {
    // Its DataView helpers use the backing buffer; isolate the bounded view.
    const { width, height } = imageSize(Uint8Array.from(bytes));
    if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width < 1 || height < 1 || width > 16384 || height > 16384 || width * height > 16000000) return undefined;
    return { width, height };
  } catch { return undefined; }
}
