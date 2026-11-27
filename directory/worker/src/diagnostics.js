const platforms = new Set(["windows", "linux", "fire-tv", "android", "roku"]);
const fields = new Set(`sdk firstFrameMs first_frame_ms first_progress_ms width height postStartRebuffers postStartRebufferMs
  stalls stall_ms retries recoveries_this_attempt playerErrorCode media_error_code httpError segment_loads segment_errors
  manifest_errors slowest_segment_ms slowest_manifest_ms videoBitrateBps variant_bps decoded_frames dropped_frames
  renderedFrames droppedFrames bandwidthEstimateBps estimated_bandwidth_bps segment_rate_bps relay_cache_hits relay_cache_misses
  live_channels viewer_sessions encoder_count encoder_starting video_bps movie_sessions startup_ms exit_ms`.split(/\s+/));
const labels = {
  kind: new Set(["live", "movie"]),
  delivery_path: new Set(["managed-vps", "legacy-tunnel", "local", "direct", "unknown"]),
  delivery_route: new Set(["preferred-relay", "registered-route", "registered-fallback"]),
  sharing: new Set(["live", "retrying", "connecting", "idle", "private"]),
  crash_kind: new Set(["Exception", "RuntimeError", "OSError", "ValueError", "MemoryError", "IllegalStateException", "OutOfMemoryError", "IOException"]),
};

export async function shutdownReport(request, env) {
  if (!request.headers.get("content-type")?.startsWith("application/json") || !request.body)
    return new Response(null, {status: 415});
  const limit = 4096;
  const reader = request.body.getReader();
  const decoder = new TextDecoder();
  let bytes = 0, json = "";
  for (;;) {
    const {value, done} = await reader.read();
    if (done) break;
    bytes += value.byteLength;
    if (bytes > limit) { await reader.cancel(); return new Response(null, {status: 413}); }
    json += decoder.decode(value, {stream:true});
  }
  let report;
  try { report = JSON.parse(json + decoder.decode()); }
  catch { return new Response(null, {status: 400}); }
  if (!report || !platforms.has(report.platform) || !["studio", "tv"].includes(report.component) ||
      !["exit", "crash"].includes(report.report_type) || typeof report.app !== "string" || !/^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(report.app))
    return new Response(null, {status: 400});
  const clean = {platform: report.platform, component: report.component, app: report.app, report_type: report.report_type};
  for (const [name, value] of Object.entries(report)) {
    if ((fields.has(name) && typeof value === "number" && Number.isFinite(value) && value >= -1 && value <= 10**12) ||
        (labels[name] instanceof Set && labels[name].has(value))) clean[name] = value;
  }
  const bucket = env.STUDIO_INDEX.getByName("alpha-health:" + clean.platform);
  return new Response(null, {status: await bucket.recordAlphaHealth(clean) ? 204 : 429});
}
