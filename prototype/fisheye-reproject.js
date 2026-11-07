// Changes: DOM-free equidistant-fisheye dolly remap + 16-bit depth PNG decode (#66).
"use strict";

// Model contract: 256² equidistant fisheye, 120° FOV. Radius 128 px maps to
// half the FOV in radians, so the focal length is 128 / (π/3).
const FOCAL_PX = 128 / (Math.PI / 3);
const MAX_RADIUS = 128;
const AZIMUTH_BUCKETS = 720;

function reprojectRadius(theta, depthMm, dollyMm) {
  // Pinhole dolly: tan θ' = Z sin θ / (Z cos θ − d). A sample is behind the
  // camera when Z cos θ ≤ d; return null so callers drop it.
  if (!(depthMm > 0) || !(dollyMm >= 0)) return null;
  const denominator = depthMm * Math.cos(theta) - dollyMm;
  if (!(denominator > 0)) return null;
  return Math.atan((depthMm * Math.sin(theta)) / denominator);
}

function paeth(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) return a;
  if (pb <= pc) return b;
  return c;
}

function unfilterRows(raw, width, height, bytesPerPixel) {
  const rowBytes = width * bytesPerPixel;
  const out = new Uint8Array(rowBytes * height);
  let offset = 0;
  let previous = null;
  for (let y = 0; y < height; y += 1) {
    const filter = raw[offset];
    offset += 1;
    const row = new Uint8Array(rowBytes);
    for (let i = 0; i < rowBytes; i += 1) {
      const value = raw[offset + i];
      const left = i >= bytesPerPixel ? row[i - bytesPerPixel] : 0;
      const above = previous ? previous[i] : 0;
      const upperLeft = previous && i >= bytesPerPixel ? previous[i - bytesPerPixel] : 0;
      let restored;
      switch (filter) {
        case 0: restored = value; break;
        case 1: restored = value + left; break;
        case 2: restored = value + above; break;
        case 3: restored = value + ((left + above) >> 1); break;
        case 4: restored = value + paeth(left, above, upperLeft); break;
        default: throw new Error(`unsupported PNG filter ${filter}`);
      }
      row[i] = restored & 0xff;
    }
    offset += rowBytes;
    out.set(row, y * rowBytes);
    previous = row;
  }
  return out;
}

async function decodeDepthPng(arrayBuffer) {
  // Depth PNG contract: 16-bit grayscale, uint16 = round(depth_mm × 10),
  // 65535 for pixels with no hit inside the far plane.
  const bytes = new Uint8Array(arrayBuffer);
  if (bytes.length < 8 || bytes[0] !== 0x89 || bytes[1] !== 0x50) {
    throw new Error("not a PNG");
  }
  const view = new DataView(arrayBuffer);
  let offset = 8;
  let width = 0;
  let height = 0;
  let bitDepth = 0;
  let colorType = 0;
  let interlace = 0;
  const idat = [];
  while (offset + 8 <= bytes.length) {
    const length = view.getUint32(offset);
    const type = String.fromCharCode(
      bytes[offset + 4], bytes[offset + 5], bytes[offset + 6], bytes[offset + 7],
    );
    const dataStart = offset + 8;
    if (type === "IHDR") {
      width = view.getUint32(dataStart);
      height = view.getUint32(dataStart + 4);
      bitDepth = bytes[dataStart + 8];
      colorType = bytes[dataStart + 9];
      interlace = bytes[dataStart + 12];
    } else if (type === "IDAT") {
      idat.push(bytes.subarray(dataStart, dataStart + length));
    } else if (type === "IEND") {
      break;
    }
    offset = dataStart + length + 4;
  }
  if (bitDepth !== 16 || colorType !== 0 || interlace !== 0) {
    throw new Error(`unsupported depth PNG (${bitDepth}-bit, colour ${colorType})`);
  }
  let total = 0;
  for (const chunk of idat) total += chunk.length;
  const compressed = new Uint8Array(total);
  let cursor = 0;
  for (const chunk of idat) {
    compressed.set(chunk, cursor);
    cursor += chunk.length;
  }
  const stream = new Blob([compressed]).stream()
    .pipeThrough(new DecompressionStream("deflate"));
  const raw = new Uint8Array(await new Response(stream).arrayBuffer());
  const rows = unfilterRows(raw, width, height, 2);
  const mm = new Float32Array(width * height);
  for (let i = 0; i < mm.length; i += 1) {
    const value = (rows[i * 2] << 8) | rows[i * 2 + 1];
    mm[i] = value === 65535 ? Number.NaN : value / 10;
  }
  return {width, height, data: mm};
}

function sampleDepth(depth, size, x, y) {
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  if (x0 < 0 || y0 < 0 || x0 + 1 >= size || y0 + 1 >= size) return Number.NaN;
  const fx = x - x0;
  const fy = y - y0;
  const data = depth.data;
  const i00 = y0 * size + x0;
  const weights = [
    [data[i00], (1 - fx) * (1 - fy)],
    [data[i00 + 1], fx * (1 - fy)],
    [data[i00 + size], (1 - fx) * fy],
    [data[i00 + size + 1], fx * fy],
  ];
  let total = 0;
  let weight = 0;
  for (const [value, w] of weights) {
    if (!Number.isFinite(value)) continue;
    total += value * w;
    weight += w;
  }
  return weight > 0 ? total / weight : Number.NaN;
}

function sampleRgba(rgba, size, x, y) {
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  if (x0 < 0 || y0 < 0 || x0 + 1 >= size || y0 + 1 >= size) return null;
  const fx = x - x0;
  const fy = y - y0;
  const out = [0, 0, 0, 0];
  const corners = [
    [(1 - fx) * (1 - fy), y0 * size + x0],
    [fx * (1 - fy), y0 * size + x0 + 1],
    [(1 - fx) * fy, (y0 + 1) * size + x0],
    [fx * fy, (y0 + 1) * size + x0 + 1],
  ];
  for (const [weight, index] of corners) {
    for (let channel = 0; channel < 4; channel += 1) {
      out[channel] += rgba[index * 4 + channel] * weight;
    }
  }
  return out;
}

function buildAzimuthMaps(depth, size, dollyMm) {
  const centre = (size - 1) / 2;
  const step = (Math.PI * 2) / AZIMUTH_BUCKETS;
  const maps = new Array(AZIMUTH_BUCKETS);
  for (let bucket = 0; bucket < AZIMUTH_BUCKETS; bucket += 1) {
    const angle = (bucket + 0.5) * step;
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    const sourceRadius = [];
    const targetRadius = [];
    let last = -Infinity;
    for (let radius = 0; radius <= MAX_RADIUS; radius += 1) {
      const x = centre + radius * cos;
      const y = centre + radius * sin;
      if (x < 0 || y < 0 || x > size - 1 || y > size - 1) break;
      const theta = radius / FOCAL_PX;
      if (theta > Math.PI / 3 + 1e-9) break;
      const z = sampleDepth(depth, size, x, y);
      if (!Number.isFinite(z)) continue;
      const mapped = reprojectRadius(theta, z, dollyMm);
      if (mapped === null) continue;
      const target = FOCAL_PX * mapped;
      // Keep the forward map strictly increasing; a nearer wall can otherwise
      // fold it back over an earlier sample.
      if (!(target > last)) continue;
      sourceRadius.push(radius);
      targetRadius.push(target);
      last = target;
    }
    maps[bucket] = {sourceRadius, targetRadius};
  }
  return maps;
}

function invertRadius(map, target) {
  const {sourceRadius, targetRadius} = map;
  if (!targetRadius.length) return Number.NaN;
  if (target < targetRadius[0] || target > targetRadius[targetRadius.length - 1]) {
    // No source sample: a disocclusion hole. Never extrapolate.
    return Number.NaN;
  }
  let low = 0;
  let high = targetRadius.length - 1;
  while (low + 1 < high) {
    const middle = (low + high) >> 1;
    if (targetRadius[middle] <= target) low = middle;
    else high = middle;
  }
  const span = targetRadius[high] - targetRadius[low];
  const mix = span > 0 ? (target - targetRadius[low]) / span : 0;
  return sourceRadius[low] + (sourceRadius[high] - sourceRadius[low]) * mix;
}

function reprojectFrame(rgba, depth, dollyMm) {
  // Dolly the source frame forward by dollyMm. Target pixels without a source
  // sample keep alpha 0 so the caller can fall back to the next anchor.
  if (!rgba || !depth || !(dollyMm >= 0)) return null;
  const size = depth.width || 256;
  if (depth.width && depth.height && depth.width !== depth.height) return null;
  const maps = buildAzimuthMaps(depth, size, dollyMm);
  const centre = (size - 1) / 2;
  const out = new Uint8ClampedArray(size * size * 4);
  const bucketStep = (Math.PI * 2) / AZIMUTH_BUCKETS;
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const dx = x - centre;
      const dy = y - centre;
      const target = Math.hypot(dx, dy);
      if (target > size / 2) continue;
      let angle = Math.atan2(dy, dx);
      if (angle < 0) angle += Math.PI * 2;
      const bucket = Math.min(
        AZIMUTH_BUCKETS - 1, Math.floor(angle / bucketStep),
      );
      const source = invertRadius(maps[bucket], target);
      if (!Number.isFinite(source)) continue;
      const sourceAngle = angle;
      const sample = sampleRgba(
        rgba, size,
        centre + source * Math.cos(sourceAngle),
        centre + source * Math.sin(sourceAngle),
      );
      if (!sample) continue;
      const index = (y * size + x) * 4;
      out[index] = sample[0];
      out[index + 1] = sample[1];
      out[index + 2] = sample[2];
      out[index + 3] = sample[3];
    }
  }
  return {width: size, height: size, data: out};
}

const api = {
  AZIMUTH_BUCKETS,
  FOCAL_PX,
  decodeDepthPng,
  reprojectFrame,
  reprojectRadius,
};

if (typeof module === "object" && module.exports) {
  module.exports = api;
} else {
  globalThis.FisheyeReproject = api;
}
