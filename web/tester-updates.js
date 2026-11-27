(() => {
    const button = document.querySelector("#screen-check-update, #check-update");
    const status = document.querySelector("#screen-update-status, #update-status");
    if (!button || !status) return;

    const homeVersion = document.querySelector("#home-release-version");
    const homeNotes = document.querySelector("#home-changelog");
    const studioVersion = document.querySelector("#version");
    let systemNotes = document.querySelector("#tv-release-notes");
    if (!systemNotes) {
        systemNotes = document.createElement("div");
        status.after(systemNotes);
    }

    function showNotes(container, version, notes) {
        if (!container || !version || !notes?.length) return;
        const title = document.createElement("strong");
        title.textContent = `${version} patch notes`;
        const list = document.createElement("ul");
        for (const note of notes) {
            const item = document.createElement("li");
            item.textContent = note;
            list.append(item);
        }
        container.replaceChildren(title, list);
    }

    async function updateRequest(path, method = "GET") {
        const response = await fetch("/api/tester-update" + path, {
            method, headers: {"X-ChannelForge-Tester-Update": "1"}
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        return result;
    }

    let checked = false;
    updateRequest("/installed").then(({display_version: version, release_notes: notes}) => {
        if (homeVersion && version) homeVersion.textContent = version;
        if (studioVersion && version) studioVersion.textContent = version;
        showNotes(homeNotes, version, notes);
        if (!checked) showNotes(systemNotes, version, notes);
    }).catch(() => {});

    button.onclick = async () => {
        checked = true;
        button.disabled = true;
        status.textContent = "Checking for a private Tester update…";
        try {
            const {current, available, display_version: version, release_notes: notes} = await updateRequest("");
            showNotes(systemNotes, version, notes);
            if (!available) {
                status.textContent = version ? `You’re on ${version}.` : `You’re on the latest Tester build (${current}).`;
                return;
            }
            const release = version || `Tester build ${available}`;
            status.textContent = `${release} is available. Starting download…`;
            await updateRequest("/" + available, "POST");
            for (;;) {
                await new Promise(resolve => setTimeout(resolve, 400));
                const progress = await updateRequest("/progress");
                if (progress.status === "error") throw new Error(progress.error);
                if (progress.status === "restarting") {
                    status.textContent = `${version ? release : `Build ${available}`} verified. Restarting TV and Studio…`;
                    continue;
                }
                if (progress.stage === "downloading" && progress.total) {
                    const percent = Math.floor((progress.downloaded || 0) * 100 / progress.total);
                    status.textContent = `Downloading ${release} · ${percent}%`;
                } else if (progress.stage === "verifying") {
                    status.textContent = `Verifying ${release}…`;
                }
            }
        } catch (error) {
            status.textContent = error.message || "The update could not be completed. Try again.";
        } finally {
            button.disabled = false;
        }
    };
})();
