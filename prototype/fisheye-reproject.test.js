// Changes: exact-value tests for the fisheye dolly remap and depth PNG decode (#66).
"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const zlib = require("node:zlib");

const reproject = require("./fisheye-reproject.js");

const DEG = Math.PI / 180;

function chunk(type, data) {
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(zlib.crc32(body) >>> 0, 0);
  return Buffer.concat([length, body, crc]);
}

function encodeGray16(width, height, values) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 16; // bit depth
  ihdr[9] = 0; // grayscale
  const raw = Buffer.alloc(height * (1 + width * 2));
  for (let y = 0; y < height; y += 1) {
    const row = y * (1 + width * 2);
    raw[row] = 0; // no filter
    for (let x = 0; x < width; x += 1) {
      raw.writeUInt16BE(values[y * width + x], row + 1 + x * 2);
    }
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", zlib.deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function solidRgba(size, colour) {
  const data = new Uint8ClampedArray(size * size * 4);
  for (let i = 0; i < size * size; i += 1) {
    data.set(colour, i * 4);
  }
  return data;
}

function alphaAt(frame, x, y) {
  return frame.data[(y * frame.width + x) * 4 + 3];
}

test("reprojected radius matches the pinhole dolly formula", () => {
  const focal = reproject.FOCAL_PX;
  // depth 50 mm, dolly 10 mm.
  const cases = [
    [0, 0],
    [30, 78.71225158875481],
    [60, 151.23924191814592],
  ];
  for (const [thetaDeg, expectedRadius] of cases) {
    const theta = reproject.reprojectRadius(thetaDeg * DEG, 50, 10);
    assert.notEqual(theta, null);
    assert.ok(
      Math.abs(focal * theta - expectedRadius) < 1e-9,
      `θ=${thetaDeg}° radius ${focal * theta}`,
    );
  }
});

test("samples at or behind the camera are dropped", () => {
  assert.equal(reproject.reprojectRadius(60 * DEG, 15, 10), null);
  assert.equal(reproject.reprojectRadius(0, 10, 10), null);
  assert.notEqual(reproject.reprojectRadius(0, 10.001, 10), null);
});

test("depth PNG decode returns millimetres and invalid pixels", async () => {
  const values = [0, 1, 500, 65534, 65535];
  const png = encodeGray16(5, 1, values);
  const depth = await reproject.decodeDepthPng(
    png.buffer.slice(png.byteOffset, png.byteOffset + png.byteLength),
  );
  assert.equal(depth.width, 5);
  assert.equal(depth.height, 1);
  assert.equal(depth.data[0], 0);
  assert.ok(Math.abs(depth.data[1] - 0.1) < 1e-6);
  assert.equal(depth.data[2], 50);
  assert.ok(Math.abs(depth.data[3] - 6553.4) < 1e-2);
  assert.ok(Number.isNaN(depth.data[4]));
});

test("uniform depth keeps the centre and flags disocclusion holes", () => {
  const size = 256;
  const centre = (size - 1) / 2;
  const depth = {width: size, height: size, data: new Float32Array(size * size)};
  depth.data.fill(50);
  // Valid only inside radius 64 px: the forward map stops at about 78.7 px,
  // so anything farther is a disocclusion at dolly 10 mm.
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      if (Math.hypot(x - centre, y - centre) > 64) {
        depth.data[y * size + x] = Number.NaN;
      }
    }
  }
  const rgba = solidRgba(size, [240, 20, 20, 255]);
  const frame = reproject.reprojectFrame(rgba, depth, 10);
  assert.notEqual(frame, null);
  assert.equal(alphaAt(frame, 128, 128), 255);
  assert.equal(alphaAt(frame, Math.round(centre + 40), 128), 255);
  assert.equal(alphaAt(frame, Math.round(centre + 100), 128), 0);
  assert.equal(frame.data[(128 * size + 128) * 4], 240);
});

test("an all-invalid depth map leaves the target fully transparent", () => {
  const size = 256;
  const depth = {width: size, height: size, data: new Float32Array(size * size)};
  depth.data.fill(Number.NaN);
  const frame = reproject.reprojectFrame(solidRgba(size, [1, 2, 3, 255]), depth, 4);
  for (let i = 3; i < frame.data.length; i += 4) {
    assert.equal(frame.data[i], 0);
  }
});
