window.createTesterReport = function ({section, read, send, readAutomatic, setAutomatic, stopTesting}) {
    const controls = document.createElement("article");
    controls.className = "tester-report";
    controls.innerHTML = '<p>Review the app diagnostics before sharing. Reports include playback and guide metrics and exclude accounts and media titles.</p><pre aria-label="Diagnostics preview" tabindex="0">Select Refresh report to collect diagnostics.</pre><div class="tester-report-actions"><button type="button">Refresh report</button><button type="button">Send report</button><button type="button">Save report</button></div><label><input type="checkbox"> Send app crash reports automatically on the next launch</label><p role="status"></p>';
    section.append(controls);
    const [refresh, publish, save] = controls.querySelectorAll("button");
    const preview = controls.querySelector("pre");
    const status = controls.querySelector("[role=status]");
    const automatic = controls.querySelector("input");
    let report;
    publish.disabled = save.disabled = true;

    async function perform(button, busy, action) {
        button.disabled = true;
        status.textContent = busy;
        try {
            await action();
        } catch (error) {
            status.textContent = error.message;
        } finally {
            button.disabled = false;
        }
    }

    refresh.onclick = () => perform(refresh, "Collecting diagnostics…", async () => {
        report = await read();
        preview.textContent = JSON.stringify(report, null, 2);
        publish.disabled = save.disabled = false;
        status.textContent = "Review the report before sending.";
    });
    publish.onclick = () => perform(publish, "Sending report…", async () => {
        const {receipt} = await send(report);
        status.textContent = "Report sent. ID " + receipt;
    });
    save.onclick = () => {
        const link = document.createElement("a");
        link.href = URL.createObjectURL(new Blob([preview.textContent], {type:"application/json"}));
        link.download = "ChannelForge-tester-report.json";
        link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 2000);
        status.textContent = "Report saved.";
    };
    if (stopTesting) {
        const stop = document.createElement("button");
        stop.type = "button";
        stop.textContent = "Stop testing";
        stop.onclick = () => perform(stop, "Stopping testing…", stopTesting);
        controls.querySelector(".tester-report-actions").append(stop);
    }
    automatic.onchange = () => {
        const agreed = automatic.checked;
        return perform(automatic, "Saving reporting preference…", async () => {
            try {
                automatic.checked = (await setAutomatic(agreed)).enabled;
                status.textContent = automatic.checked ? "Automatic crash reports enabled." : "Automatic crash reports disabled.";
            } catch (error) {
                automatic.checked = !agreed;
                throw error;
            }
        });
    };
    new MutationObserver(() => {
        if (section.hidden || !section.classList.contains("active") && section.classList.contains("view")) return;
        if (!report && !refresh.disabled) refresh.onclick();
        readAutomatic().then(({enabled}) => { automatic.checked = enabled; }).catch(() => {});
    }).observe(section, {attributes:true, attributeFilter:["class", "hidden"]});
    return {automatic, refresh: refresh.onclick};
};
