(() => {
    const notice = 'This private alpha can buffer, stop, or crash. Only agree if you want ChannelForge to collect app diagnostics on this device. Review the report before choosing to send it to the developer. Reports include playback and guide metrics. They exclude accounts, media titles, and remote device access.';
    const video = document.querySelector('#player');
    const settings = document.querySelector('[data-section-block="developer"]');
    const timeline = [];
    const headers = {'Content-Type':'application/json', 'X-ChannelForge-Tester':'desktop'};
    let tracking = false, platform, version, revision;
    let lastPlayback;

    function record(kind) {
        if (!tracking) return;
        if (timeline.length === 40) timeline.shift();
        timeline.push({kind, ms:Math.round(performance.now())});
    }

    for (const [event, kind] of Object.entries({loadstart:'load', loadeddata:'first-frame', waiting:'buffering',
        playing:'playing', ended:'ended', error:'error'})) video.addEventListener(event, () => record(kind));
    addEventListener('cf-tester-stream-error', () => record('error'));
    addEventListener('cf-tester-stop', () => {
        const playback = window.ChannelForgeTesterSnapshot?.();
        if (tracking && playback?.stream_selected) lastPlayback = playback;
    });

    const report = window.createTesterReport({
        section: settings,
        read: () => {
            const current = window.ChannelForgeTesterSnapshot?.() || {};
            const {resolution, ...playback} = current.stream_selected || !lastPlayback
                ? current : lastPlayback;
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
