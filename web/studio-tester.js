(() => {
    const sources = document.querySelector('#settings-sources');
    const plexSignIn = sources.querySelector('#setup-plex-signin');
    const plexStatus = sources.querySelector('#setup-plex-signin-status');
    const plexSignOut = document.createElement('button');
    plexSignOut.type = 'button';
    plexSignOut.textContent = 'Sign out of Plex';
    plexSignOut.setAttribute('data-tester-visibility', '');
    plexSignIn.setAttribute('data-tester-visibility', '');
    plexSignIn.after(plexSignOut);
    plexSignOut.onclick = async () => {
        if (!confirm('Sign out of Plex in Studio Tester? Channels can use Plex titles again after you sign back in.')) return;
        plexSignOut.disabled = true;
        plexStatus.textContent = 'Signing out of Plex…';
        try {
            await api('/api/tester/plex/signout', {method:'POST',headers:{'X-ChannelForge-Tester':'studio'}});
            await reloadConnectedSources();
            plexStatus.textContent = 'Signed out of Plex.';
        } catch (error) {
            plexStatus.textContent = error.message;
        } finally {
            plexSignOut.disabled = false;
        }
    };

    const jellyfin = sources.querySelector('#jellyfin-form');
    const jellyfinRows = jellyfin.querySelector('#jellyfin-connections');
    const jellyfinInputs = Array.from(jellyfin.children).filter(node => node.tagName !== 'H3' && node !== jellyfinRows);
    for (const node of jellyfinInputs) node.setAttribute('data-tester-visibility', '');
    const addJellyfin = document.createElement('button');
    addJellyfin.type = 'button';
    addJellyfin.className = 'wide';
    addJellyfin.setAttribute('data-tester-visibility', '');
    jellyfin.append(addJellyfin);
    let addingJellyfin = false;
    addJellyfin.onclick = () => {
        addingJellyfin = !addingJellyfin;
        window.ChannelForgeTesterSources();
        if (addingJellyfin) jellyfin.querySelector('#jellyfin-url').focus();
    };
    jellyfin.addEventListener('reset', () => { addingJellyfin = false; });
    window.ChannelForgeTesterSources = () => {
        plexSignIn.hidden = Boolean(state.config.token_saved);
        plexSignOut.hidden = !plexSignIn.hidden;
        const connected = (state.config.jellyfin_connections || []).length > 0;
        for (const node of jellyfinInputs) node.hidden = connected && !addingJellyfin;
        addJellyfin.hidden = !connected;
        addJellyfin.textContent = addingJellyfin ? 'Cancel adding server' : 'Add another Jellyfin server';
        addJellyfin.setAttribute('aria-expanded', String(addingJellyfin));
        for (const button of jellyfinRows.querySelectorAll('[data-delete-jellyfin]')) button.textContent = 'Sign out';
    };

    document.querySelector('.broadcast-monitor').remove();
    const main = document.querySelector('main');
    const analytics = document.createElement('section');
    analytics.id = 'channel-analytics';
    analytics.className = 'view';
    analytics.innerHTML = '<div class="page-heading"><div><h2>Channel analytics</h2><p></p></div><button data-view="channels">Back to Channels</button></div><div class="panel tester-channel-data"></div><p class="muted tester-broadcast-notes">Viewer counts reflect active player sessions. Older players may show activity without a count. Traffic is the average video served over the last 10 seconds. Peak counts reset between broadcasts.</p>';
    main.append(analytics);

    function metric(label, value) {
        const tile = document.createElement('div');
        const name = document.createElement('small');
        const number = document.createElement('strong');
        name.textContent = label;
        number.textContent = value;
        tile.append(name, number);
        return tile;
    }

    const mbps = bitsPerSecond => ((bitsPerSecond || 0) / 1000000).toFixed(2) + ' Mbps';
    const clock = () => new Date().toLocaleTimeString();
    let activeChannelId;
    let currentBroadcasts;
    window.ChannelForgeTesterDecorateChannels = () => {
        for (const card of document.querySelectorAll('[data-channel-card]')) {
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = 'Analytics';
            button.onclick = () => {
                activeChannelId = card.dataset.channelCard;
                showView('channel-analytics');
                window.ChannelForgeTesterBroadcasts(currentBroadcasts);
                api('/api/status?brief=1').then(value => window.ChannelForgeTesterBroadcasts(value.broadcasts)).catch(() => {});
            };
            card.querySelector('.actions').prepend(button);
        }
    };

    window.ChannelForgeTesterBroadcasts = broadcasts => {
        currentBroadcasts = broadcasts;
        if (!activeChannelId || !analytics.classList.contains('active')) return;
        const channel = state.channels.find(item => item.id === activeChannelId);
        const container = analytics.querySelector('.tester-channel-data');
        if (!channel) {
            container.textContent = 'This channel is unavailable.';
            return;
        }
        const current = broadcasts?.[channel.id] || {};
        const status = current.state || 'idle';
        const labels = {idle:'Idle', starting:'Starting', live:'Live', cooldown:'Cooling down'};
        analytics.querySelector('h2').textContent = channel.name + ' analytics';
        analytics.querySelector('.page-heading p').textContent = 'Channel ' + channel.number + ' · Updated ' + clock();
        const row = document.createElement('article');
        row.className = 'tester-broadcast-row';
        row.dataset.status = status;
        const title = document.createElement('header');
        const badge = document.createElement('span');
        const program = document.createElement('strong');
        badge.textContent = labels[status] || 'Idle';
        program.textContent = current.program || state.now_playing?.[channel.id]?.title || 'Scheduled programming';
        title.append(program, badge);
        const description = document.createElement('p');
        description.textContent = ({
            idle:'Encoding starts when a viewer tunes in.',
            starting:'Studio is preparing the first video segments.',
            cooldown:'Studio is keeping the stream ready briefly for reconnects.'
        })[status] || 'Broadcasting the scheduled program.';
        if (current.legacy_activity) description.append(' Older player activity detected.');
        const values = document.createElement('div');
        values.className = 'tester-broadcast-metrics';
        values.append(metric('Viewers', current.viewers || 0), metric('Peak viewers', current.peak_viewers || 0),
            metric('Encoders', current.encoders || 0), metric('Recent video traffic', mbps(current.upload_bps)),
            metric(status === 'cooldown' ? 'Idle in' : 'Segments published',
                status === 'cooldown' ? (current.cooldown_seconds || 0) + ' s' : current.segments || 0));
        row.append(title, description, values);
        container.replaceChildren(row);
    };

    window.ChannelForgeTesterDecorateChannels();
    window.ChannelForgeTesterSources();

    const post = (path, data) => api(path, {method:'POST',
        headers:{'X-ChannelForge-Tester':'studio', 'Content-Type':'application/json'}, body:JSON.stringify(data)});
    window.createTesterReport({
        section:document.querySelector('#developer'),
        read:() => api('/api/tester/studio-report'),
        send:report => post('/api/tester/studio-report', report),
        readAutomatic:() => api('/api/tester/automatic'),
        setAutomatic:agreed => post('/api/tester/automatic', {agreed})
    });
})();
