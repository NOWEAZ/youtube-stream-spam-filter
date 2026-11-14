// 의존성 없이 PNG 아이콘을 그린다: 방패 모양 + 체크 표시. 실행: node tools/make_icons.js
const zlib = require('node:zlib');
const fs = require('node:fs');
const path = require('node:path');

function crc32(buf) {
  let c, crc = 0xffffffff;
  for (let n = 0; n < buf.length; n++) { c = (crc ^ buf[n]) & 0xff; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; crc = (crc >>> 8) ^ c; }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}
function png(size, pixel) {
  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0;
    for (let x = 0; x < size; x++) {
      // 4x4 슈퍼샘플링으로 가장자리를 부드럽게
      let r = 0, g = 0, b = 0, a = 0;
      for (let sy = 0; sy < 4; sy++) for (let sx = 0; sx < 4; sx++) {
        const p = pixel((x + (sx + 0.5) / 4) / size, (y + (sy + 0.5) / 4) / size);
        r += p[0] * p[3]; g += p[1] * p[3]; b += p[2] * p[3]; a += p[3];
      }
      const o = y * (size * 4 + 1) + 1 + x * 4;
      if (a > 0) { raw[o] = r / a; raw[o + 1] = g / a; raw[o + 2] = b / a; raw[o + 3] = Math.round((a / 16) * 255); }
    }
  }
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(size, 0); ihdr.writeUInt32BE(size, 4); ihdr[8] = 8; ihdr[9] = 6;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}
// 단위 좌표(0~1)에서 방패 안쪽인지
function inShield(u, v) {
  if (v < 0.08 || v > 0.94) return false;
  const half = v < 0.5 ? 0.40 : 0.40 * (1 - (v - 0.5) / 0.44) ** 0.8;
  return Math.abs(u - 0.5) <= half && !(v < 0.18 && Math.abs(u - 0.5) > 0.36);
}
function distToSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay, t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}
const pixel = (u, v) => {
  if (!inShield(u, v)) return [0, 0, 0, 0];
  const check = distToSegment(u, v, 0.30, 0.50, 0.45, 0.65) < 0.065 || distToSegment(u, v, 0.45, 0.65, 0.72, 0.34) < 0.065;
  return check ? [255, 255, 255, 1] : [26, 86, 219, 1];
};
const out = path.join(__dirname, '..', 'extension', 'icons');
fs.mkdirSync(out, { recursive: true });
for (const size of [16, 48, 128]) fs.writeFileSync(path.join(out, `icon${size}.png`), png(size, pixel));
console.log('icons written to', out);
