// Keep playback measurements in memory. Sharing requires an explicit user action.
(() => {
    const creator = Boolean(document.getElementById('tv-player'));
    const video = document.querySelector(creator ? '#tv-player' : '#player');
    let transport, started, stallStarted, lastPlayback, segmentBytes, segmentDuration;
    if (!creator && video) {
        const endStall = () => {
            if (stallStarted == null) return;
            transport.stall_ms += Math.round(performance.now() - stallStarted);
            stallStarted = null;
        };
        window.ChannelForgePlaybackStart = (url, traceId, continuing, route) => {
            if (!continuing || !transport) {
                started = performance.now();
                stallStarted = null;
                segmentBytes = segmentDuration = 0;
                transport = {trace_id:traceId, first_frame_ms:-1, stalls:0, stall_ms:0,
                    httpError:0, segment_loads:0, segment_errors:0, manifest_errors:0,
                    slowest_segment_ms:0, slowest_manifest_ms:0, segment_rate_bps:-1, relay_cache_hits:0, relay_cache_misses:0,
                    slowest_relay_upstream_ms:-1, slowest_studio_response_ms:-1};
            }
            transport.delivery_route = route;
            const host = url.hostname.toLowerCase();
            transport.delivery_path = host === 'channelforge-vps.tailb58ce4.ts.net' ? 'managed-vps' :
                host.endsWith('.lhr.life') ? 'legacy-tunnel' : ['localhost','127.0.0.1','[::1]'].includes(host) ? 'local' : 'direct';
        };
        window.ChannelForgePlaybackTransfer = (kind, duration, bytes, response) => {
            if (!transport) return;
            if (kind === 'segment' && bytes > 0) {
                transport.segment_loads++;
                segmentBytes += bytes;
                segmentDuration += duration;
                if (segmentDuration > 0) transport.segment_rate_bps = Math.round(8000 * segmentBytes / segmentDuration);
                transport.slowest_segment_ms = Math.max(transport.slowest_segment_ms, Math.round(duration));
            } else if (kind === 'manifest') {
                transport.slowest_manifest_ms = Math.max(transport.slowest_manifest_ms, Math.round(duration));
            }
            const header = name => response?.getResponseHeader?.(name) ?? response?.headers?.get(name) ?? '';
            if (header('X-ChannelForge-Relay') === 'vps') transport.delivery_path = 'managed-vps';
            const cache = header('X-ChannelForge-Cache');
            if (['HIT','STALE','UPDATING','REVALIDATED'].includes(cache)) {
                transport.relay_cache_hits++;
                return;
            }
            if (['MISS','EXPIRED','BYPASS'].includes(cache)) transport.relay_cache_misses++;
            const upstream = header('X-ChannelForge-Upstream-Header-Time');
            const studio = header('X-ChannelForge-Studio-Response-Ms');
            if (/^\d{1,3}\.\d{3}$/.test(upstream)) transport.slowest_relay_upstream_ms = Math.max(transport.slowest_relay_upstream_ms, Math.round(Number(upstream)*1000));
            if (/^\d{1,6}$/.test(studio)) transport.slowest_studio_response_ms = Math.max(transport.slowest_studio_response_ms, Number(studio));
        };
        video.addEventListener('playing', () => {
            if (transport?.first_frame_ms === -1) transport.first_frame_ms = Math.round(performance.now() - started);
            endStall();
        });
        video.addEventListener('waiting', () => {
            if (!transport || transport.first_frame_ms < 0 || video.paused || video.seeking || stallStarted != null) return;
            stallStarted = performance.now();
            transport.stalls++;
        });
        for (const event of ['pause','ended','seeking']) video.addEventListener(event, endStall);
        addEventListener('cf-tester-stream-error', ({detail:{kind,status}}) => {
            if (!transport) return;
            if (kind === 'segment') transport.segment_errors++;
            if (kind === 'manifest') transport.manifest_errors++;
            if (status >= 400 && status <= 599) Object.assign(transport, {httpError:status, http_resource:kind});
        });
        addEventListener('cf-tester-stop', () => {
            if (!activeMedia || !transport) return;
            endStall();
            lastPlayback = currentPlayback();
        });
        if (document.documentElement.hasAttribute('data-tester')) {
            window.ChannelForgeTesterSnapshot = () => ({...currentPlayback(), studios:allCreators.length,
                channels:liveChannels.length, reconnecting:liveChannels.filter(channel => channel.state === 'reconnecting').length});
        }
    }
    const container = document.querySelector(creator ? '#system .system-links' : '#playback-troubleshooting-controls');
    if (!container) return;
    const existing = container.querySelector('[data-open-diagnostics]');
    const trigger = existing || document.createElement('button');
    trigger.type = 'button';
    if (!existing) trigger.textContent = 'Playback diagnostics';
    trigger.title = 'Open playback diagnostics (Ctrl+Shift+D)';
    if (!existing) container.append(trigger);

    const panel = document.createElement('dialog');
    panel.className = 'diagnostics-dialog';
    panel.setAttribute('aria-label', 'Playback diagnostics');
    panel.style.cssText = 'width:min(92vw,740px);max-height:85vh;padding:20px;color:#eeefff;background:#101529;border:1px solid #54dcea;border-radius:12px';
    panel.innerHTML = '<h2>Playback diagnostics</h2><p role="status"></p><textarea aria-label="Troubleshooting report" readonly spellcheck="false" style="width:100%;height:42vh;font:13px monospace"></textarea><div><button type="button">Refresh</button> <button type="button">Copy report</button> <button type="button">Close</button></div>';
    document.body.append(panel);
    const status = panel.querySelector('[role="status"]');
    const report = panel.querySelector('textarea');
    const [refresh, copy, close] = panel.querySelectorAll('button');

    function currentPlayback() {
        const engine = creator ? hlsPlayer : hls;
        const level = engine?.levels?.[engine.currentLevel];
        const quality = video.getVideoPlaybackQuality?.();
        let secondsAhead = 0;
        for (let i = 0; i < video.buffered.length; i++) {
            if (video.buffered.start(i) <= video.currentTime && video.currentTime <= video.buffered.end(i)) {
                secondsAhead = video.buffered.end(i) - video.currentTime;
                break;
            }
        }
        return {
            engine: engine ? 'hls.js' : video.currentSrc ? 'native HTML video' : 'inactive web player',
            playing: !video.paused && !video.ended,
            media_ready_state: video.readyState,
            resolution: video.videoWidth ? [video.videoWidth, video.videoHeight] : null,
            video_codec: level?.videoCodec || null, audio_codec: level?.audioCodec || null,
            variant_bps: level?.bitrate || null,
            estimated_bandwidth_bps: Number.isFinite(engine?.bandwidthEstimate) ? Math.round(engine.bandwidthEstimate) : null,
            position_seconds: Math.round(video.currentTime),
            buffered_ahead_seconds: Number(secondsAhead.toFixed(2)),
            decoded_frames: quality?.totalVideoFrames ?? null, dropped_frames: quality?.droppedVideoFrames ?? null,
            media_error_code: video.error?.code ?? null,
            ...(creator ? {} : {stream_selected: Boolean(activeStreamUrl), kind: activeMedia?.kind || null,
                trace_id: activeMedia?.traceId || null,
                recoveries_this_attempt: liveMode ? liveRetries : movieRetries,
                ...(activeMedia && transport ? {...transport, stall_ms:transport.stall_ms +
                    (stallStarted == null ? 0 : Math.round(performance.now() - stallStarted))} : {})})
        };
    }

    async function sample() {
        refresh.disabled = true;
        copy.disabled = true;
        try {
            const data = {captured_utc: new Date().toISOString(), app: creator ? 'Creator' : 'TV',
                playback: !creator && !activeMedia && lastPlayback ? lastPlayback : currentPlayback()};
            if (creator) {
                const response = await fetch('/api/diagnostics', {cache:'no-store'});
                if (!response.ok) throw Error();
                data.creator = await response.json();
            } else {
                data.directory = {studios: allCreators.length, channels: liveChannels.length,
                    reconnecting: liveChannels.filter(channel => channel.state === 'reconnecting').length};
            }
            report.value = JSON.stringify(data, null, 2);
            status.textContent = 'Snapshot captured. Copy this report to share it.';
            copy.disabled = false;
        } catch {
            report.value = '';
            status.textContent = 'The app could not collect a snapshot. Select Refresh to retry.';
        } finally {
            refresh.disabled = false;
        }
    }
    function open() {
        if (!panel.open) panel.showModal();
        return sample();
    }
    trigger.addEventListener('click', open);
    document.querySelectorAll('[data-open-diagnostics]').forEach(button => {
        if (button !== trigger) button.addEventListener('click', open);
    });
    refresh.addEventListener('click', sample);
    close.addEventListener('click', () => panel.close());
    copy.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(report.value);
            status.textContent = 'Report copied.';
        } catch {
            report.focus();
            report.select();
            status.textContent = 'Report selected. Press Ctrl+C to copy it.';
        }
    });
    document.addEventListener('keydown', event => {
        if (event.ctrlKey && event.shiftKey && event.code === 'KeyD') {
            event.preventDefault();
            event.stopImmediatePropagation();
            if (panel.open) panel.close(); else open();
        } else if (panel.open && event.key === 'Escape') {
            event.preventDefault();
            event.stopImmediatePropagation();
            panel.close();
        }
    }, true);
})();
