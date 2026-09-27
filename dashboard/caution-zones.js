// Normalized lap intervals, split at the start line and merged where overlapping.
export function cautionRanges(events, length, settings = {}) {
  if (settings.yellow_zones_enabled === false || !(length > 0)) return [];
  const before = Math.max(0, Number(settings.yellow_before_m ?? 200));
  const after = Math.max(0, Number(settings.yellow_after_m ?? 50));
  const ranges = [];
  for (const event of events) {
    if (event.type !== 'incident' || !Number.isFinite(event.track_pos)) continue;
    if (before + after >= length) return [[0, 1]];
    if (before + after <= 0) continue;
    const start = ((event.track_pos - before / length) % 1 + 1) % 1;
    const end = start + (before + after) / length;
    if (end > 1) ranges.push([start, 1], [0, end - 1]);
    else ranges.push([start, end]);
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const range of ranges) {
    const last = merged.at(-1);
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([...range]);
  }
  return merged;
}

export function drawCautionZones(ctx, track, events, project, width) {
  const ranges = cautionRanges(events, track.length_m, track.settings?.scene);
  if (!ranges.length || !track.centerline?.length) return;
  const cl = track.centerline;
  ctx.save();
  ctx.strokeStyle = '#ffd43b'; ctx.lineWidth = width; ctx.lineCap = 'butt';
  ctx.beginPath();
  for (let i = 0; i < cl.length; i++) {
    const a = cl[i], b = cl[(i + 1) % cl.length];
    const end = i + 1 === cl.length ? 1 : b[0];
    if (end <= a[0]) continue;
    for (const [lo, hi] of ranges) {
      const start = Math.max(a[0], lo), stop = Math.min(end, hi);
      if (stop <= start) continue;
      const point = (t) => {
        const f = (t - a[0]) / (end - a[0]);
        return project(a[1] + (b[1] - a[1]) * f, a[3] + (b[3] - a[3]) * f);
      };
      ctx.moveTo(...point(start)); ctx.lineTo(...point(stop));
    }
  }
  ctx.stroke(); ctx.restore();
}
