// The guide and cinema own the selection and transitions for both desktop renderers.
(() => {
    let engine = 'web', available = false, session = null;
    const api = () => window.pywebview?.api;

    function stop() {
        const previous = session;
        session = null;
        if (!previous) return;
        clearTimeout(previous.timer);
        api()?.native_playback_command(previous.generation, 'stop').catch(() => {});
    }

    async function start(generation, url, media) {
        const client = api();
        const run = {generation, media, lastFrame: 0, progressed: performance.now()};
        session = run;
        const active = () => session === run && generation === playbackGeneration && !media.ejecting;
        function fallback() {
            if (!active()) return;
            stop();
            media.webOnly = true;
            media.manifestReady = false;
            delete media.startAfterTransport;
            play(activeStreamUrl, media, true);
        }
        const show = () => {
            if (active()) client.native_playback_command(generation, 'show').catch(fallback);
        };
        async function poll() {
            if (!active()) return;
            try {
                const result = await client.native_playback_state(generation);
                if (!active()) return;
                run.sample = result;
                if (result.action) {
                    if (result.action === 'error' || result.action === 'closed') { fallback(); return; }
                    stop();
                    if (result.action === 'previous' || result.action === 'next') {
                        surfChannel(result.action === 'previous' ? -1 : 1);
                    } else if (result.action === 'ended') {
                        $('#player').dispatchEvent(new Event('ended'));
                    } else if (liveMode) {
                        media.webOnly = true;
                        play(activeStreamUrl, media, true);
                        minimizePlayer();
                    } else {
                        closeVideoView();
                    }
                    return;
                }
                if (result.ready && !run.ready) {
                    run.ready = true;
                    setBuffering(false);
                    setPlayerState(liveMode ? '● LIVE' : 'PLAYING');
                    if (media.transportPending) {
                        media.manifestReady = true;
                        media.startAfterTransport = show;
                        $('#player').dispatchEvent(new Event('cinemamanifestready'));
                    } else show();
                }
                if (result.frames > run.lastFrame || result.state === 'Paused' || media.transportPending && run.ready) {
                    run.lastFrame = result.frames;
                    run.progressed = performance.now();
                }
                if (performance.now() - run.progressed > (run.ready ? 15000 : 23000)) { fallback(); return; }
                if (!liveMode && result.time > 0) {
                    media.resumeAt = result.time;
                    rememberMovie(media);
                }
                if (liveMode && run.ready) {
                    const channel = liveChannels.find(item => item.stream_url === activeStreamUrl) || media.channel;
                    const current = programAt(channel), remaining = Date.parse(current?.stop) - Date.now();
                    const next = current && channel.programs[channel.programs.indexOf(current) + 1];
                    const caption = [channel?.name, current?.title || media.title,
                        remaining >= 0 && remaining < 45000 && next ? 'Coming up next · ' + next.title : '']
                        .filter(Boolean).join('   ·   ');
                    if (run.caption !== caption) {
                        run.caption = caption;
                        await client.native_playback_command(generation, {caption});
                    }
                }
                if (active()) run.timer = setTimeout(poll, 300);
            } catch { fallback(); }
        }
        try {
            const opened = await client.start_native_playback(generation, url, media.title, liveMode,
                media.resumeAt || 0, Boolean(media.transportPending), Boolean(media.cinema ||
                document.documentElement.classList.contains('application-fullscreen')));
            if (!active()) {
                if (opened) await client.native_playback_command(generation, 'stop');
                return;
            }
            if (!opened) { fallback(); return; }
            await poll();
        } catch { fallback(); }
    }

    function offerFallback() {
        if (!available || !activeMedia || activeMedia.nativeTried || activeMedia.webOnly) return false;
        const media = activeMedia;
        const button = document.createElement('button');
        button.className = 'retry-stream';
        button.textContent = 'Play with VLC';
        button.onclick = () => {
            if (media !== activeMedia) return;
            media.nativeTried = true;
            media.deferDecode = Boolean(media.transportPending);
            play(activeStreamUrl, media, true);
        };
        $('#player-state').append(' ', button);
        return true;
    }

    async function settings() {
        const container = document.querySelector('[data-section-block="updates"] .section-placeholder');
        if (!container || container.querySelector('#desktop-playback-engine') || !api()?.playback_settings) return;
        let value;
        try { value = await api().playback_settings(); } catch { return; }
        if (container.querySelector('#desktop-playback-engine')) return;
        engine = value.engine;
        available = value.available;
        const label = document.createElement('label'), select = document.createElement('select');
        label.textContent = 'Desktop playback ';
        select.id = 'desktop-playback-engine';
        select.add(new Option('Built-in player', 'web'));
        select.add(new Option(available ? 'VLC player' : 'VLC player (runtime unavailable)', 'vlc'));
        select.options[1].disabled = !available;
        select.value = engine;
        select.onchange = async () => {
            const selected = select.value;
            select.disabled = true;
            try {
                if (await api().select_playback_engine(selected)) engine = selected;
            } finally {
                select.value = engine;
                select.disabled = false;
            }
        };
        label.append(select);
        container.append(label);
    }
    window.ChannelForgeNative = {
        preferred: () => engine === 'vlc',
        canFallback: () => available && !activeMedia?.nativeTried && !activeMedia?.webOnly,
        shouldPlay: media => available && (engine === 'vlc' || media.nativeTried),
        play: start, stop, offerFallback,
        diagnostics: () => session ? {engine: 'libVLC', state: session.sample?.state || 'Opening',
            position_seconds: session.sample?.time || 0, decoded_frames: session.sample?.frames || 0,
            dropped_frames: session.sample?.dropped || 0} : null
    };
    window.addEventListener('pywebviewready', settings);
    settings();
})();
