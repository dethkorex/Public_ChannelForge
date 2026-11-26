// Collect a snapshot only when requested. Keep URLs, paths and credentials out of copied reports.
(() => {
    const creator = Boolean(document.getElementById('tv-player'));
    const container = document.querySelector(creator ? '#system .system-links' : '[data-section-block="updates"] .section-placeholder');
    if (!container) return;
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.textContent = 'Playback diagnostics';
    trigger.title = 'Open playback diagnostics (Ctrl+Shift+D)';
    container.append(trigger);

    const panel = document.createElement('dialog');
    panel.setAttribute('aria-label', 'Playback diagnostics');
    panel.style.cssText = 'width:min(92vw,740px);max-height:85vh;padding:20px;color:#eeefff;background:#101529;border:1px solid #54dcea;border-radius:12px';
    panel.innerHTML = '<h2>Playback diagnostics</h2><p role="status"></p><textarea aria-label="Troubleshooting report" readonly spellcheck="false" style="width:100%;height:42vh;font:13px monospace"></textarea><div><button type="button">Refresh</button> <button type="button">Copy report</button> <button type="button">Close</button></div>';
    document.body.append(panel);
    const status = panel.querySelector('[role="status"]');
    const report = panel.querySelector('textarea');
    const [refresh, copy, close] = panel.querySelectorAll('button');

    function currentPlayback() {
        const native = !creator && window.ChannelForgeNative?.diagnostics();
        if (native) return native;
        const video = document.querySelector(creator ? '#tv-player' : '#player');
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
                recoveries_this_attempt: liveMode ? liveRetries : movieRetries})
        };
    }

    async function sample() {
        refresh.disabled = true;
        copy.disabled = true;
        try {
            const data = {captured_utc: new Date().toISOString(), app: creator ? 'Creator' : 'TV',
                playback: currentPlayback()};
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
