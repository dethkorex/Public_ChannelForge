(() => {
    const notice = 'This private alpha can buffer, stop, or crash. Only agree if you want ChannelForge to collect app diagnostics on this device. Review the report before choosing to send it to the developer. Reports include playback and guide metrics. They exclude accounts, media titles, and remote device access.';
    const video = document.querySelector('#player');
    const settings = document.querySelector('[data-section-block="developer"]');
    const timeline = [];
    const headers = {'Content-Type':'application/json', 'X-ChannelForge-Tester':'desktop'};
    let tracking = false, platform, version, revision;
    let loadStart = null, firstFrame = null, stallStart = null, stallMs = 0, stalls = 0;
    let httpError = 0, httpResource, lastPlayback;
    let segmentLoads = 0, segmentErrors = 0, manifestErrors = 0;
    let segmentMs = 0, segmentBytes = 0, slowestSegment = 0, slowestManifest = 0;

    function record(kind) {
        if (!tracking) return;
        if (timeline.length === 40) timeline.shift();
        timeline.push({kind, ms:Math.round(performance.now())});
    }

    function finishStall() {
        if (stallStart === null) return;
        stallMs += performance.now() - stallStart;
        stallStart = null;
    }

    video.addEventListener('loadstart', () => {
        if (!tracking) return;
        loadStart = performance.now();
        firstFrame = null;
        stallStart = null;
        stallMs = 0;
        stalls = 0;
        httpError = 0;
        httpResource = undefined;
        segmentLoads = segmentErrors = manifestErrors = 0;
        segmentMs = segmentBytes = slowestSegment = slowestManifest = 0;
        record('load');
    });
    video.addEventListener('loadeddata', () => {
        if (!tracking || firstFrame !== null || loadStart === null) return;
        firstFrame = performance.now() - loadStart;
        record('first-frame');
    });
    video.addEventListener('waiting', () => {
        if (!tracking || firstFrame === null || video.paused || video.seeking || stallStart !== null) return;
        stallStart = performance.now();
        stalls++;
        record('buffering');
    });
    video.addEventListener('playing', () => {
        finishStall();
        record('playing');
    });
    video.addEventListener('ended', () => {
        finishStall();
        record('ended');
    });
    video.addEventListener('error', () => record('error'));
    addEventListener('cf-tester-stream-error', event => {
        if (!tracking) return;
        const {status, kind} = event.detail;
        if (status >= 100 && status <= 599) {
            httpError = status;
            httpResource = kind;
        }
        if (kind === 'segment') segmentErrors++;
        if (kind === 'manifest') manifestErrors++;
        record('error');
    });
    addEventListener('cf-tester-transfer', event => {
        if (!tracking) return;
        const {kind, bytes, duration} = event.detail;
        if (kind === 'segment' && bytes > 0) {
            segmentLoads++;
            segmentMs += duration;
            segmentBytes += bytes;
            slowestSegment = Math.max(slowestSegment, duration);
        } else if (kind === 'manifest') {
            slowestManifest = Math.max(slowestManifest, duration);
        }
    });
    addEventListener('cf-tester-stop', () => {
        const playback = window.ChannelForgeTesterSnapshot?.();
        if (tracking && playback?.stream_selected) {
            lastPlayback = {...playback, ...metrics()};
        }
    });

    function metrics() {
        return {
            first_frame_ms: firstFrame === null ? -1 : Math.round(firstFrame),
            stall_ms: Math.round(stallMs + (stallStart === null ? 0 : performance.now() - stallStart)),
            stalls, httpError, http_resource: httpResource,
            segment_loads: segmentLoads, segment_errors: segmentErrors, manifest_errors: manifestErrors,
            slowest_segment_ms: Math.round(slowestSegment), slowest_manifest_ms: Math.round(slowestManifest),
            segment_rate_bps: segmentMs > 0 ? Math.round(8000 * segmentBytes / segmentMs) : -1
        };
    }

    const report = window.createTesterReport({
        section: settings,
        read: () => {
            const current = window.ChannelForgeTesterSnapshot?.() || {};
            const {resolution, ...playback} = current.stream_selected || !lastPlayback
                ? {...current, ...metrics()} : lastPlayback;
            return {...playback, platform, app:version, build_revision:revision, component:"tv",
                studios:current.studios, channels:current.channels, reconnecting:current.reconnecting,
                ...(resolution ? {width:resolution[0], height:resolution[1]} : {}), timeline:[...timeline]};
        },
        send: data => localJson('/api/tester/report', {method:'POST', headers, body:JSON.stringify(data)}),
        readAutomatic: async () => ({enabled:(await localJson('/api/tester/status')).auto_reports}),
        setAutomatic: agreed => localJson('/api/tester/automatic', {method:'POST', headers, body:JSON.stringify({agreed})}),
        stopTesting: async () => {
            await localJson('/api/tester/consent', {method:'POST', headers, body:'{"agreed":false}'});
            tracking = false;
            location.replace('/');
        }
    });

    function start() {
        tracking = true;
        record('guide');
        load();
    }

    const consent = document.createElement('dialog');
    consent.className = 'tester-consent';
    consent.setAttribute('aria-label', 'ChannelForge TV Tester consent');
    consent.innerHTML = '<h2>ChannelForge TV Tester</h2><p></p><div class="tester-report-actions"><button type="button">Decline</button><button type="button">I agree</button></div><p role="status"></p>';
    consent.querySelector('p').textContent = notice;
    consent.addEventListener('cancel', event => event.preventDefault());
    const [decline, agree] = consent.querySelectorAll('button');
    const consentStatus = consent.querySelector('[role="status"]');
    decline.onclick = () => {
        consentStatus.textContent = 'Testing is disabled. Close the Tester app to exit.';
        agree.hidden = true;
        decline.disabled = true;
    };
    agree.onclick = async () => {
        agree.disabled = true;
        try {
            await localJson('/api/tester/consent', {method:'POST', headers, body:'{"agreed":true}'});
            consent.close();
            start();
        } catch (error) {
            consentStatus.textContent = error.message;
            agree.disabled = false;
        }
    };
    document.body.append(consent);
    localJson('/api/tester/status').then(data => {
        ({platform, version} = data);
        revision = data.build_revision;
        report.automatic.checked = data.auto_reports;
        if (data.consented) start();
        else consent.showModal();
    }).catch(() => {
        agree.disabled = true;
        consentStatus.textContent = 'Unable to open tester settings. Restart the Tester app.';
        consent.showModal();
    });
})();
