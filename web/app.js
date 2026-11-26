let state={channels:[],assets:[],tools:[],config:{}},libraries=[],mediaChoices=[],workspaceMedia=[],libraryMediaCache=new Map(),hlsPlayer=null,activeChannel="",draggedChannel="",builderStep=1,retuneTimer=0,updateRelease=null,plexSigninTimer=0,guideOffsetHours=0;
const selectedLineup = new Map();
let mediaLoadRequest = 0, workspaceLibraryRequest = 0, workspaceMediaRequest = 0;
let libraryRequest, libraryFetchedAt = 0;
let builderLibraryKind = "all", builderLibraries = [], builderLibraryRequest = 0;
const builderLibrarySelections = new Map();
const workspaceBatchSize = 96;
let workspaceDisplayLimit = workspaceBatchSize;
const $=s=>document.querySelector(s),escapeHtml=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const mediaThumb=(item,section)=>item.source==="local"?`/api/local/image?section=${encodeURIComponent(section)}&id=${encodeURIComponent(item.id)}`:`/api/plex/image?path=${encodeURIComponent(item.thumb)}&server=${encodeURIComponent(item.plex_server_id||"")}`;
const nextChannelNumber=()=>String(Math.max(0,...state.channels.map(channel=>Number.parseInt(channel.number,10)||0))+1);
if(window.Hls){Hls.DefaultConfig.manifestLoadingTimeOut=30000;Hls.DefaultConfig.fragLoadingTimeOut=30000}
async function api(path,options={}){const response=await fetch(path,{headers:{"Content-Type":"application/json"},...options});const data=await response.json();if(!response.ok)throw new Error(data.error||`Request failed (${response.status})`);return data}
function toast(message,error=false){const el=$("#toast");el.textContent=message;el.className=error?"show error":"show";setTimeout(()=>el.className="",3500)}
function renderChannels(){const box=$("#channel-list");box.innerHTML=state.channels.length?state.channels.map(c=>{const logo=state.assets.find(a=>a.id===c.logo_asset_id),live=state.now_playing?.[c.id],finished=Date.parse(c.air_until)<=Date.now(),mode={ordered:"In order",shuffle:"Shuffled",shuffle_in_order:"Shuffle shows · episodes in order",marathon:"Series marathon",franchise:"Smart franchise timeline"}[c.playback_mode||"ordered"];return `<article class="channel-card" draggable="true" data-channel-card="${c.id}" data-search="${escapeHtml(`${c.name} ${c.number}`.toLowerCase())}">${logo?`<img class="channel-logo-small" src="/user-assets/${encodeURIComponent(logo.filename)}" alt="">`:""}<span class="drag-handle">⋮⋮</span><span class="channel-number">CH ${escapeHtml(c.number)} · ${finished?"OFFLINE":"LIVE"}</span><h3>${escapeHtml(c.name)}</h3><p class="meta">${finished?"Lineup finished":live?`Now playing: ${escapeHtml(live.title)}`:`${c.items.length} titles in rotation`} · ${mode}</p><div class="actions">${finished?`<button class="primary" data-channel-action="reair" data-id="${c.id}">Put back on TV</button>`:`<button class="primary" data-watch="${c.id}" data-view="watch">Test in player</button>`}<button data-channel-action="refresh" data-id="${c.id}">Refresh guide</button><button data-channel-action="shuffle" data-id="${c.id}">Reshuffle lineup</button><button data-edit="${c.id}">Edit</button><button class="danger" data-delete="${c.id}">Delete</button></div></article>`}).join(""):`<div class="empty">No channels yet. Add a local folder or connect Plex in Media Sources, then create your first channel.</div>`}
function showView(view){if(view!=="watch")stopCreatorPreview();document.querySelectorAll(".view,nav button").forEach(element=>element.classList.remove("active"));$(`#${view}`)?.classList.add("active");document.querySelector(`nav button[data-view="${view}"]`)?.classList.add("active");window.scrollTo({top:0,behavior:"smooth"})}
function renderStudioSummary(){
  const count=state.channels.length;
  $("#overview-channel-count").textContent=`${count} channel${count===1?"":"s"}`;
  $("#creator-overview-title").textContent=state.config.creator_profile?.name||"Your ChannelForge studio";
  const checks=[{name:"Plex connection",ok:state.config.token_saved,detail:state.config.plex_profile?.server_name||"Not connected"},{name:"Standalone encoder",ok:state.playback_engine?.available!==false,detail:state.playback_engine?.source||"FFmpeg HLS engine"},{name:"Hosted guide",ok:true,detail:`${state.channels.length} channels published`},{name:"Branding library",ok:state.assets.length>0,detail:`${state.assets.length} assets available`}].map(check=>`<article class="${check.ok?"ok":""}"><span class="check-icon">${check.ok?"✓":"!"}</span><div><b>${check.name}</b><small>${escapeHtml(check.detail)}</small></div></article>`).join("");if($("#system-checks").innerHTML!==checks)$("#system-checks").innerHTML=checks;const now=state.channels.map(channel=>{const live=state.now_playing?.[channel.id],logo=state.assets.find(asset=>asset.id===channel.logo_asset_id);return `<article>${logo?`<img class="channel-logo-small" src="/user-assets/${encodeURIComponent(logo.filename)}" alt="">`:""}<span class="channel-number">CH ${escapeHtml(channel.number)}</span><h3>${escapeHtml(channel.name)}</h3><p>${escapeHtml(live?.title||"Ready to play")}</p><div class="actions"><button data-watch="${channel.id}" data-view="watch">Watch now</button><button data-edit="${channel.id}">Edit lineup</button></div></article>`}).join("")||'<div class="empty">Create a channel to begin your live lineup.</div>';if($("#overview-now-playing").innerHTML!==now)$("#overview-now-playing").innerHTML=now;const artwork=state.channels.map(channel=>{const logo=state.assets.find(asset=>asset.id===channel.logo_asset_id);return `<article class="guide-artwork-card">${logo?`<img src="/user-assets/${encodeURIComponent(logo.filename)}" alt="">`:'<span class="poster-placeholder">CF</span>'}<b>CH ${escapeHtml(channel.number)} · ${escapeHtml(channel.name)}</b><small>${logo?"Published in XMLTV":"Uses text identity — assign a logo in Channels"}</small></article>`}).join("");if($("#guide-artwork-grid").innerHTML!==artwork)$("#guide-artwork-grid").innerHTML=artwork}
function libraryOptions(items) {
  return '<option value="">Choose a library</option>' + items.map(item =>
    `<option value="${escapeHtml(item.key)}">${escapeHtml(item.title)} (${item.source === "local" ? "local folder" : escapeHtml(item.type)})</option>`
  ).join("");
}
function requestLibraries(force = false) {
  if (libraryRequest) return libraryRequest;
  if (!force && libraryFetchedAt && Date.now() - libraryFetchedAt < 60000) return Promise.resolve(libraries);
  libraryRequest = api("/api/libraries").then(data => {
    libraries = data.libraries;
    libraryFetchedAt = Date.now();
    return libraries;
  }).finally(() => { libraryRequest = null; });
  return libraryRequest;
}
async function loadWorkspaceLibraries(force = false) {
  const request = ++workspaceLibraryRequest;
  const select = $("#workspace-library"), status = $("#workspace-library-status");
  const render = items => {
    if (request !== workspaceLibraryRequest) return;
    const selected = select.value;
    select.innerHTML = libraryOptions(items);
    if (items.some(item => item.key === selected)) select.value = selected;
  };
  let finished = false;
  status.textContent = "Finding local and shared libraries…";
  if (libraries.length) render(libraries);
  else api("/api/libraries?scope=connected").then(data => {
    if (!finished && data.libraries.length) render(data.libraries);
  }).catch(() => {});
  try {
    const items = await requestLibraries(force);
    finished = true;
    if (request !== workspaceLibraryRequest) return;
    render(items);
    status.textContent = items.length ? "" : "Add a media source to browse your library.";
  } catch (error) {
    finished = true;
    if (request === workspaceLibraryRequest) status.textContent = error.message;
  }
}
function renderWorkspaceMedia(){
  const query=$("#workspace-library-search").value.trim().toLowerCase();
  const visible=workspaceMedia.filter(item=>`${item.title} ${item.year}`.toLowerCase().includes(query));
  const grid=$("#workspace-library-grid"),section=$("#workspace-library").value;
  $("#workspace-library-count").textContent=`${visible.length} playable titles`;
  grid.innerHTML=visible.length?visible.slice(0,workspaceDisplayLimit).map(item=>
    `<article class="studio-media-card">${item.thumb?`<img src="${mediaThumb(item,section)}" alt="" loading="lazy" decoding="async">`:'<span class="poster-placeholder">CF</span>'}<div><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.type)}${item.year?` · ${escapeHtml(item.year)}`:""}</small></div></article>`
  ).join(""):'<div class="empty">No matching titles.</div>';
  if(visible.length>workspaceDisplayLimit){
    const more=document.createElement("button");
    more.textContent=`Show ${Math.min(workspaceBatchSize,visible.length-workspaceDisplayLimit)} more titles`;
    more.className="workspace-more-media";
    more.onclick=()=>{
      const previous=workspaceDisplayLimit;
      workspaceDisplayLimit+=workspaceBatchSize;
      renderWorkspaceMedia();
      grid.children[previous]?.scrollIntoView({block:"nearest"});
    };
    grid.append(more);
  }
}
function updateMediaSelection() {
  const choices = [...selectedLineup.values()];
  const videos = choices.reduce((total, choice) => total + choice.items.length, 0);
  $("#selection-count").textContent = `${choices.length} selected · ${videos} playable videos`;
  const list = $("#selected-media-list");
  list.innerHTML = choices.length ? choices.map((choice, index) => `
    <button type="button" data-remove-media="${index}">
      <span><b>${escapeHtml(choice.title)}</b>
      <small>${choice.type === "show" ? choice.items.length + " episodes" : escapeHtml(choice.type)}${choice.year ? " · " + escapeHtml(choice.year) : ""}</small></span><i>×</i>
    </button>`).join("") : '<div class="muted">No shows or movies selected yet.</div>';
  document.querySelectorAll("#media-list input").forEach(input => {
    input.checked = selectedLineup.has(mediaChoices[Number(input.value)]?.key);
  });
}
async function diagnosePlayback(channelId){for(let attempt=0;attempt<12;attempt++){await new Promise(resolve=>setTimeout(resolve,1000));try{const status=await api(`/api/playback/status?channel=${encodeURIComponent(channelId)}`);if(status.error){$("#playback-status").className="danger";$("#playback-status").textContent=status.error;return}if($("#tv-player").readyState>=3)return}catch{return}}}
function setBuilderStep(step){builderStep=Math.max(1,Math.min(4,step));document.querySelectorAll("[data-builder-step]").forEach(section=>section.classList.toggle("active",Number(section.dataset.builderStep)===builderStep));document.querySelectorAll(".builder-steps span").forEach((dot,index)=>{dot.classList.toggle("active",index+1===builderStep);dot.classList.toggle("done",index+1<builderStep)});$("#builder-back").hidden=builderStep===1;$("#builder-next").hidden=builderStep===4;$("#builder-save").hidden=builderStep!==4;if(builderStep===4){const choices=[...selectedLineup.values()],episodes=choices.reduce((count,choice)=>count+choice.items.length,0);$("#builder-summary").innerHTML=`<b>CH ${escapeHtml($("#channel-number").value)} · ${escapeHtml($("#channel-name").value)}</b><p>${choices.length} selected shows/titles · ${episodes} playable videos · ${escapeHtml($("#playback-mode").selectedOptions[0]?.textContent||"")}</p><small class="muted">Selecting a show automatically adds every playable episode in season and episode order.</small>`}}
function renderAssets(){const box=$("#asset-list");box.innerHTML=state.assets.length?state.assets.map(a=>`<article class="asset-card"><img loading="lazy" decoding="async" class="asset-preview" src="/user-assets/${encodeURIComponent(a.filename)}" alt="${escapeHtml(a.name)}"><div class="asset-body"><h4>${escapeHtml(a.name)}</h4><span class="meta">${escapeHtml(a.kind.replaceAll("-"," "))}${a.built_in?" · starter pack":""}</span><div class="actions"><a class="button" href="/user-assets/${encodeURIComponent(a.filename)}" download>Download</a>${a.built_in?"":`<button class="danger" data-delete-asset="${a.id}">Delete</button>`}</div></div></article>`).join(""):`<div class="empty">No artwork yet. Generate a design or upload your own.</div>`}
function renderTools(){const box=$("#tool-list");box.innerHTML=state.tools.length?state.tools.map(t=>`<article><h3>${escapeHtml(t.name)}</h3><p>${escapeHtml(t.description)}</p><button data-tool="${t.id}" ${t.disabled?"disabled":""}>Run tool</button></article>`).join(""):`<div class="empty">No tools installed. See PLUGIN_DEVELOPMENT.md to create one.</div>`}
function renderPlexProfile(){const profile=state.config.plex_profile||{},box=$("#plex-profile");box.innerHTML=state.config.token_saved?`<span class="plex-dot"></span><div><b>${escapeHtml(profile.server_name||"Plex connected")}</b><small>${profile.username?`Signed in as ${escapeHtml(profile.username)} · `:""}${profile.version?`Server ${escapeHtml(profile.version)}`:"Connection verified"}</small></div>`:`<span class="plex-dot offline"></span><div><b>Plex not connected</b><small>Sign in to index movies and build channels.</small></div>`}
function renderLocalLibraries(){const box=$("#local-library-list"),values=state.config.local_libraries||[];box.innerHTML=values.length?values.map(item=>`<article class="local-library-row"><div><b>${escapeHtml(item.name)}</b><small>${escapeHtml(item.path)}</small></div><button type="button" class="danger" data-delete-local="${item.id}">Remove</button></article>`).join(""):'<p class="muted">No local folders added yet.</p>';const selected=$("#upload-library").value;$("#upload-library").innerHTML='<option value="">Choose a local library</option>'+values.map(item=>`<option value="local:${item.id}">${escapeHtml(item.name)}</option>`).join("");$("#upload-library").value=selected;renderCreatorPublish()}
function renderCreatorPublish(){
  const profile=state.config.creator_profile||{};
  const set=(selector,value)=>{if(document.activeElement!==$(selector))$(selector).value=value||""};
  set("#creator-name",profile.name);
  set("#creator-slug",profile.slug);
  set("#creator-description",profile.description);
  set("#creator-logo-url",profile.logo_asset_id?"":profile.logo_url);
  const logoSelect=$("#creator-logo-asset"),selected=logoSelect.value||profile.logo_asset_id||"";
  logoSelect.innerHTML='<option value="">No studio asset</option>'+state.assets.filter(asset=>asset.kind==="logo").map(asset=>`<option value="${asset.id}">${escapeHtml(asset.name)}</option>`).join("");
  logoSelect.value=selected;
  set("#directory-url",state.config.directory_url);
  set("#creator-pairing-url",state.config.public_tunnel?"":state.config.public_url);
  renderSharingStatus(state.config);
}
function renderSharingStatus(config){
  const privateStudio=config.auto_publish_channels===false&&!config.creator_published;
  $("#unpublish-creator").hidden=privateStudio;
  $("#resume-publishing").hidden=!privateStudio;
  const sharing=privateStudio?"private":config.sharing_status;
  const status=$("#creator-publish-status");
  let host="";
  try { host=new URL(config.public_url).hostname; } catch {}
  const route=host.endsWith(".app.lhr.life")?"Free relay":host.endsWith(".ts.net")?"Tailscale Funnel":"Public HTTPS";
  status.className="connection-result"+(sharing==="live"?" success":"");
  status.textContent=({
    live:`Your studio is listed in ChannelForge TV via ${route}. Keep Creator running while sharing.`,
    connecting:"Connecting your studio to ChannelForge TV…",
    retrying:"The connection dropped. Creator will retry automatically.",
    private:"Your studio is offline.",
  })[sharing]||"Channels and scanned Plex movies appear in ChannelForge TV automatically.";
  const overview=$("#overview-sharing");
  overview.dataset.sharing=sharing||"idle";
  overview.textContent=({live:"On air worldwide",connecting:"Connecting",retrying:"Reconnecting",private:"Private"})[sharing]||"Ready to publish";
  if(config.creator_profile?.name)$("#creator-overview-title").textContent=config.creator_profile.name;
  const scan=config.movie_scan;
  $("#movie-scan-status").textContent=scan?.state==="scanning"?"Scanning Plex movies…":scan?.state==="ready"
    ? `${scan.movies} movies indexed for Rewind Cinema.` : scan?.state==="retry"
    ? "The scan could not finish. Check Plex, then use Scan libraries to try again."
    : "Creator scans Plex movies on startup.";
  $("#overview-movies").textContent=$("#movie-scan-status").textContent;
}
async function startPlexSignin(event){
  const fromSetup=event?.currentTarget?.id==="setup-plex-signin";
  const button=$(fromSetup?"#setup-plex-signin":"#plex-signin"),status=$(fromSetup?"#setup-plex-signin-status":"#plex-signin-status");
  button.disabled=true;status.textContent="Creating a secure Plex sign-in…";
  try{
    const signin=await api("/api/plex/signin/start",{method:"POST",body:"{}"});
    status.textContent="Plex opened in your default browser. Finish signing in there; ChannelForge will connect automatically.";
    clearInterval(plexSigninTimer);const started=Date.now();
    plexSigninTimer=setInterval(async()=>{
      try{
        const result=await api(`/api/plex/signin/status?pin_id=${encodeURIComponent(signin.pin_id)}`);
        if(result.complete){clearInterval(plexSigninTimer);plexSigninTimer=0;await refresh();status.textContent="Plex connected. Movie scanning is automatic.";button.disabled=false;libraryFetchedAt=0;if($("#library").classList.contains("active"))loadWorkspaceLibraries(true);toast("Plex connected.");return}
        if(Date.now()-started>(signin.expires_in||900)*1000){clearInterval(plexSigninTimer);plexSigninTimer=0;button.disabled=false;status.textContent="The Plex sign-in expired. Select Sign in with Plex to try again."}
      }catch(error){clearInterval(plexSigninTimer);plexSigninTimer=0;button.disabled=false;status.textContent=error.message;toast(error.message,true)}
    },1200);
  }catch(error){if(popup&&!popup.closed)popup.close();button.disabled=false;status.textContent=error.message;toast(error.message,true)}
}
async function checkForUpdate(notifyOnLaunch=false){const status=$("#update-status"),button=$("#install-update");status.textContent="Checking for updates…";button.hidden=true;try{updateRelease=await api("/api/update");$("#installed-version").textContent=`v${updateRelease.current_version}`;const notes=$("#update-notes");notes.textContent=updateRelease.notes||"Playback and reliability improvements.";notes.hidden=false;if(updateRelease.update_available){status.textContent=`Version v${updateRelease.latest_version} is available.`;button.textContent="Download and install";button.hidden=false;if(notifyOnLaunch){showView("updates");$("#workspace-title").textContent="Updates";if(confirm(`ChannelForge v${updateRelease.latest_version} is available. Download, verify, and install it now?`))installUpdate(true)}}else{status.textContent="You have the latest version."}}catch(error){status.textContent=error.message;toast(error.message,true)}}
function formatBytes(bytes){if(!bytes)return "0 B";const units=["B","KB","MB","GB"],power=Math.min(Math.floor(Math.log(bytes)/Math.log(1024)),units.length-1);return `${(bytes/1024**power).toFixed(power?1:0)} ${units[power]}`}
async function installUpdate(skipConfirmation=false){if(!updateRelease?.update_available)return;if(!skipConfirmation&&!confirm(`Download, verify, and install ChannelForge v${updateRelease.latest_version} in place?`))return;const button=$("#install-update"),wrap=$("#update-progress-wrap"),bar=$("#update-progress");button.disabled=true;button.textContent="Update in progress…";wrap.hidden=false;bar.value=0;try{const started=await api("/api/update/start",{method:"POST",body:JSON.stringify({launch:true})});while(true){const job=await api(`/api/update/progress?id=${encodeURIComponent(started.job_id)}`),labels={starting:"Preparing update…",checking:"Checking release…",checksum:"Downloading checksum…",downloading:"Downloading update…",verifying:"Verifying SHA-256…",installing:"Installing verified update…",complete:"Update verified",error:"Update failed"};$("#update-stage").textContent=labels[job.stage]||job.stage;$("#update-percent").textContent=`${Math.round(job.percent||0)}%`;bar.value=job.percent||0;$("#update-bytes").textContent=job.total?`${formatBytes(job.downloaded)} of ${formatBytes(job.total)}`:job.stage==="verifying"?"Checking downloaded file integrity…":"Contacting GitHub…";if(job.status==="error")throw new Error(job.error||"Update failed.");if(job.status==="complete"){const result=job.result,applied=result.can_launch;button.textContent=applied?"Restarting to update…":"Downloaded — install required";$("#update-status").textContent=applied?"Download verified. ChannelForge will restart and replace the existing application automatically; the setup wizard will not reopen.":"Download verified, but the running version has not changed yet. Complete the installation below.";if(!applied){if(result.install_error)$("#update-status").textContent="Automatic installation could not finish. Use the verified command below.";$("#install-command").textContent=result.command;$("#install-instructions").hidden=false}toast(`Update v${result.version} downloaded and verified.`);break}await new Promise(resolve=>setTimeout(resolve,400))}}catch(error){button.disabled=false;button.textContent="Download and install";$("#update-stage").textContent="Update failed";$("#update-bytes").textContent=error.message;toast(error.message,true)}}
function renderWatchChannels(){const box=$("#watch-channels"),onAir=state.channels.filter(channel=>!(Date.parse(channel.air_until)<=Date.now()));box.innerHTML=onAir.length?onAir.map(channel=>{const logo=state.assets.find(asset=>asset.id===channel.logo_asset_id),live=state.now_playing?.[channel.id];return `<div class="watch-channel-row"><button class="watch-channel ${activeChannel===channel.id?"active":""}" data-watch="${channel.id}">${logo?`<img src="/user-assets/${encodeURIComponent(logo.filename)}" alt="">`:`<span class="mark">${escapeHtml(channel.number)}</span>`}<span><b>${escapeHtml(channel.name)}</b><small>${escapeHtml(live?.title||"Ready to watch")}</small></span></button><button data-edit="${channel.id}" title="Edit ${escapeHtml(channel.name)}" aria-label="Edit ${escapeHtml(channel.name)}">Edit</button><button class="watch-delete danger" data-delete="${channel.id}" title="Delete ${escapeHtml(channel.name)}" aria-label="Delete ${escapeHtml(channel.name)}">Delete</button></div>`}).join(""):'<div class="empty">No channels on air. Open Channels to create or air a lineup.</div>'}
function applyChannelOverlays(channel){const background=state.assets.find(asset=>asset.id===channel.background_asset_id),bug=state.assets.find(asset=>asset.id===channel.corner_bug_asset_id),bg=$("#channel-bg"),corner=$("#channel-bug");bg.src=background?`/user-assets/${encodeURIComponent(background.filename)}`:"";corner.src=bug?`/user-assets/${encodeURIComponent(bug.filename)}`:"";corner.classList.toggle("active",Boolean(bug))}
async function loadGuide(){try{const guide=await api(`/api/guide?offset=${guideOffsetHours}`),now=Date.now(),start=Date.parse(guide.generated_at),end=start+6*3600000;$("#guide-range").textContent=`${new Date(start).toLocaleString([], {weekday:"short",hour:"numeric",minute:"2-digit"})} – ${new Date(end).toLocaleString([], {weekday:"short",hour:"numeric",minute:"2-digit"})}`;$("#guide-grid").innerHTML=guide.channels.length?guide.channels.map(channel=>`<div class="guide-row"><button class="guide-station" data-watch="${channel.channel_id}"><b>CH ${escapeHtml(channel.number)}</b><br>${escapeHtml(channel.name)}</button><div class="guide-programs">${channel.programs.map(program=>{const programStart=Date.parse(program.start),stop=Date.parse(program.stop),live=programStart<=now&&now<stop;return `<article class="guide-program ${live?"live":""}" data-watch="${channel.channel_id}" title="${escapeHtml(program.title)}"><time>${new Date(programStart).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}</time><b>${escapeHtml(program.title)}</b><small>${escapeHtml(program.type)}${program.year?` · ${escapeHtml(program.year)}`:""}</small></article>`}).join("")}</div></div>`).join(""):'<div class="empty">No channels yet. Use Get Started to create one.</div>'}catch(error){toast(error.message,true)}}
async function showPlaybackError(channelId,fallback="Playback could not start."){if(!channelId)return;const generation=previewGeneration;let message=fallback;try{const status=await api(`/api/playback/status?channel=${encodeURIComponent(channelId||"")}`);if(status.error)message=status.error}catch(_error){}if(generation!==previewGeneration)return;const box=$("#playback-status");box.className="danger";box.textContent=message;toast(message,true)}
let previewGeneration = 0;
function stopCreatorPreview() {
  ++previewGeneration;
  clearTimeout(retuneTimer);
  retuneTimer = 0;
  activeChannel = "";
  const video = $("#tv-player");
  const attached = Boolean(hlsPlayer || video.currentSrc || video.srcObject || video.hasAttribute("src"));
  if (hlsPlayer) {
    hlsPlayer.destroy();
    hlsPlayer = null;
  }
  if (attached || !video.paused) {
    video.pause();
    video.srcObject = null;
    video.removeAttribute("src");
    video.load();
  }
  video.style.display = "none";
  $("#player-placeholder").hidden = false;
  $("#playing-channel").textContent = "ChannelForge Live";
  $("#playing-title").textContent = "Select a channel from the guide";
  $("#playing-progress").style.width = "0";
  $("#playing-time").textContent = "";
  $("#playback-status").textContent = "";
}
window.addEventListener("pagehide", stopCreatorPreview);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopCreatorPreview();
});
function watchChannel(channelId){const channel=state.channels.find(item=>item.id===channelId);if(!channel||document.hidden)return;const generation=++previewGeneration;activeChannel=channelId;$("#playback-status").textContent="";clearTimeout(retuneTimer);renderWatchChannels();applyChannelOverlays(channel);const live=state.now_playing?.[channel.id];$("#playing-channel").textContent=`CH ${channel.number} · ${channel.name}`;$("#playing-title").textContent=live?.title||"Live stream";if(live){const start=Date.parse(live.start),stop=Date.parse(live.stop),percent=Math.max(0,Math.min(100,(Date.now()-start)/(stop-start)*100));$("#playing-progress").style.width=`${percent}%`;$("#playing-time").textContent=`${new Date(start).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})} – ${new Date(stop).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}`;retuneTimer=setTimeout(async()=>{await refresh();if(generation===previewGeneration&&!document.hidden)watchChannel(channelId)},Math.max(1000,stop-Date.now()+500))}const video=$("#tv-player"),url=`/live/${encodeURIComponent(channelId)}.m3u8`;$("#player-placeholder").hidden=true;video.style.display="block";video.pause();video.removeAttribute("src");video.load();if(hlsPlayer){hlsPlayer.destroy();hlsPlayer=null}if(window.Hls&&Hls.isSupported()){hlsPlayer=new Hls({enableWorker:true,startPosition:-1,liveSyncDurationCount:3});hlsPlayer.loadSource(url);hlsPlayer.attachMedia(video);hlsPlayer.on(Hls.Events.MANIFEST_PARSED,()=>{if(generation===previewGeneration&&!document.hidden)video.play().catch(()=>{})});hlsPlayer.on(Hls.Events.ERROR,(_event,data)=>{if(generation!==previewGeneration||!data.fatal)return;if(data.type===Hls.ErrorTypes.MEDIA_ERROR){hlsPlayer.recoverMediaError();return}showPlaybackError(channelId,`Playback error: ${data.details}`)})}else if(video.canPlayType("application/vnd.apple.mpegurl")){video.src=url;video.play().catch(()=>{})}else{showPlaybackError(channelId,"This browser cannot play HLS video.")}}
async function refresh(){state=await api("/api/status");$("#version").textContent=`v${state.version}`;$("#plex-url").value=state.config.plex_url;$("#guide-hours").value=state.config.guide_hours;$("#bind-host").value=state.config.bind_host;$("#public-url").value=state.config.public_url;$("#token-status").textContent=state.config.token_saved?"Connected and ready.":"Plex is optional and not connected.";const hasMedia=state.config.token_saved||state.config.local_libraries?.length;$("#step-connect-status").textContent=state.config.local_libraries?.length?`${state.config.local_libraries.length} local folder(s) ready${state.config.token_saved?" · Plex connected":""}.`:state.config.token_saved?`Connected to ${state.config.plex_profile?.server_name||"Plex"}.`:"Add a local folder or connect Plex.";$("#data-dir").textContent=`Application data: ${state.data_dir}`;const base=state.server_base||location.origin;$("#xmltv-url").textContent=`${base}/exports/guide.xml`;$("#m3u-url").textContent=`${base}/exports/channels.m3u`;renderChannels();renderWatchChannels();renderAssets();renderTools();renderPlexProfile();renderLocalLibraries();renderStudioSummary();if(document.querySelector("#watch.active"))loadGuide();const onboarding=$("#plex-onboarding");if(hasMedia&&onboarding.open)onboarding.close()}
function resetBuilderFilters() {
  $("#media-filter").value = "";
  $("#smart-genre").value = $("#smart-rating").value = "all";
  $("#smart-year-min").value = $("#smart-year-max").value = "";
}
function setBuilderLibraries(preferred = "") {
  const select = $("#channel-library"), old = select.value;
  const sources = builderLibraries.filter(library => builderLibraryKind === "all" || library.type === builderLibraryKind);
  const remembered = builderLibrarySelections.get(builderLibraryKind) || preferred;
  const available = sources.find(library => library.key === remembered) || sources[0];
  select.innerHTML = libraryOptions(sources);
  select.value = available?.key || "";
  for (const button of document.querySelectorAll("[data-builder-library-kind]")) {
    button.setAttribute("aria-pressed", String(button.dataset.builderLibraryKind === builderLibraryKind));
  }
  if (!available) {
    loadMedia("");
    return;
  }
  if (select.value !== old) {
    resetBuilderFilters();
    loadMedia(select.value);
  }
}
async function loadBuilderLibraries(preferred) {
  const request = ++builderLibraryRequest;
  const status = $("#builder-library-status");
  status.textContent = "Finding connected and shared libraries…";
  let finished = false;
  const show = items => {
    if (request !== builderLibraryRequest || !$("#channel-dialog").open) return;
    builderLibraries = items;
    setBuilderLibraries(preferred);
  };
  if (libraries.length) show(libraries);
  else api("/api/libraries?scope=connected").then(data => {
    if (!finished) show(data.libraries);
  }).catch(() => {});
  try {
    const items = await requestLibraries();
    finished = true;
    if (request !== builderLibraryRequest || !$("#channel-dialog").open) return;
    show(items);
    status.textContent = "";
  } catch (error) {
    finished = true;
    if (request === builderLibraryRequest && $("#channel-dialog").open) status.textContent = error.message;
  }
}
function smartMatches(choice){const genre=$("#smart-genre").value.toLocaleLowerCase(),rating=$("#smart-rating").value.toLocaleLowerCase(),min=Number($("#smart-year-min").value)||0,max=Number($("#smart-year-max").value)||9999,year=Number(choice.year)||0,genres=(choice.genres||[]).map(value=>String(value).toLocaleLowerCase()),contentRating=String(choice.content_rating||"Unrated").toLocaleLowerCase();return(genre==="all"||genres.includes(genre))&&(rating==="all"||contentRating===rating)&&(!year||year>=min&&year<=max)}
function mediaSearchMatches(choice){const query=$("#media-filter").value.trim().toLocaleLowerCase();return!query||`${choice.title} ${choice.year||""} ${choice.content_rating||""} ${(choice.genres||[]).join(" ")} ${choice.summary||""}`.toLocaleLowerCase().includes(query)}
function filteredMediaChoices(){return mediaChoices.filter(choice=>smartMatches(choice)&&mediaSearchMatches(choice))}
function applyMediaFilters(){const visible=new Set(filteredMediaChoices());document.querySelectorAll("#media-list .media-item").forEach((row,index)=>row.hidden=!visible.has(mediaChoices[index]));return [...visible]}
function refreshSmartMatches(){if(!mediaChoices.length){$("#smart-match-count").textContent="Load a library to see matches.";return}const matches=applyMediaFilters();$("#smart-match-count").textContent=`Showing ${matches.length} of ${mediaChoices.length} shows/titles · ${matches.reduce((sum,choice)=>sum+choice.items.length,0)} playable videos`}
function buildMediaChoices(items, librarySection) {
  const groups = new Map();
  for (const item of items) {
    const section = item.section || librarySection;
    const isShow = item.type === "episode" && Boolean(item.series);
    const title = isShow ? item.series : item.title;
    const key = JSON.stringify([section, item.plex_server_id || "", isShow ? "show" : "title", isShow ? item.series : String(item.id)]);
    if (!groups.has(key)) {
      groups.set(key, {
        key, section, title, id: item.id, type: isShow ? "show" : item.type,
        thumb: (isShow && item.series_thumb) || item.thumb, source: item.source,
        plex_server_id: item.plex_server_id, summary: item.summary,
        content_rating: item.content_rating || "Unrated", year: item.year, genres: [], items: []
      });
    }
    groups.get(key).items.push({...item, section});
  }
  for (const choice of groups.values()) {
    choice.items.sort((a, b) => (a.season - b.season) || (a.episode - b.episode) || a.title.localeCompare(b.title));
    choice.genres = [...new Set(choice.items.flatMap(item => item.genres || []).filter(Boolean))].sort();
    if (choice.type === "show") {
      const years = choice.items.map(item => Number(item.year)).filter(Boolean);
      if (years.length) choice.year = Math.min(...years);
    }
  }
  return [...groups.values()];
}

async function loadMedia(section) {
  const request = ++mediaLoadRequest;
  const status = $("#media-status");
  const box = $("#media-list");
  mediaChoices = [];
  if (!section) {
    status.textContent = builderLibraryKind === "show" ? "No TV show libraries available."
      : builderLibraryKind === "movie" ? "No movie libraries available." : "Choose a media library to add titles.";
    box.innerHTML = "";
    refreshSmartMatches();
    return;
  }
  status.textContent = "Loading titles…";
  box.innerHTML = '<div class="empty">Loading your library…</div>';
  refreshSmartMatches();
  try {
    const data = libraryMediaCache.get(section) || await api(`/api/library/items?section=${encodeURIComponent(section)}`);
    libraryMediaCache.set(section, data);
    if (request !== mediaLoadRequest || !$("#channel-dialog").open) return;
    mediaChoices = buildMediaChoices(data.items, section).sort((a,b) => a.title.localeCompare(b.title));
    const genres = [...new Set(mediaChoices.flatMap(choice => choice.genres))].sort();
    const ratings = [...new Set(mediaChoices.map(choice => choice.content_rating))].sort();
    const options = (title, values) => '<option value="all">' + title + '</option>'
      + values.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
    $("#smart-genre").innerHTML = options("All genres", genres);
    $("#smart-rating").innerHTML = options("All ratings", ratings);
    status.textContent = `${mediaChoices.length} shows and titles · ${data.items.length} playable videos`;
    box.innerHTML = mediaChoices.map((choice, index) => `
      <label class="media-item poster-item show-choice" tabindex="0">
        <input type="checkbox" value="${index}">
        ${choice.thumb ? `<img src="${mediaThumb(choice, choice.section)}" alt="" loading="lazy">` : '<span class="poster-placeholder">CF</span>'}
        <span><b>${escapeHtml(choice.title)}</b>
          <small>${choice.type === "show" ? choice.items.length + " episodes · Entire series" : escapeHtml(choice.type)}${choice.year ? " · " + escapeHtml(choice.year) : ""} · ${escapeHtml(choice.content_rating)}</small>
          ${choice.genres.length ? `<em>${escapeHtml(choice.genres.join(" · "))}</em>` : ""}
        </span>
      </label>`).join("");
    refreshSmartMatches();
    updateMediaSelection();
  } catch (error) {
    if (request !== mediaLoadRequest || !$("#channel-dialog").open) return;
    status.textContent = error.message;
    box.innerHTML = "";
    updateMediaSelection();
  }
}
function renderChannelBrandingPreview(){
  for(const [input,target] of [["#channel-background","#branding-preview-background"],
    ["#channel-logo","#branding-preview-logo"],["#channel-corner-bug","#branding-preview-bug"]]){
    const asset=state.assets.find(entry=>entry.id===$(input).value), image=$(target);
    image.hidden=!asset;
    if(asset)image.src=`/user-assets/${encodeURIComponent(asset.filename)}`;
    else image.removeAttribute("src");
  }
  $("#branding-preview-name").textContent=$("#channel-name").value.trim()||"Your channel";
  $("#branding-preview-number").textContent="CH "+($("#channel-number").value.trim()||"1");
}
for(const control of ["#channel-background","#channel-logo","#channel-corner-bug","#channel-name","#channel-number"]){
  $(control).addEventListener("input",renderChannelBrandingPreview);
}
let restorePresetOnSave = false;
$("#restore-preset").onclick = () => {
  restorePresetOnSave = true;
  toast("Save the channel to start its selected preset from the beginning.");
};
async function openChannel(channel = null) {
  if (!state.config.token_saved && !state.config.local_libraries?.length) {
    toast("Add a local folder or connect Plex in Sources & Sharing first.", true);
    return;
  }
  $("#channel-form").reset();
  restorePresetOnSave = false;
  $("#restore-preset").hidden = !channel;
  $("#channel-id").value = channel?.id || "";
  $("#channel-name").value = channel?.name || "";
  $("#channel-number").value = channel?.number || nextChannelNumber();
  $("#playback-mode").value = channel?.playback_mode || "ordered";
  for (const [control, kind, fallback, selected] of [
    ["#channel-logo", "logo", "No logo", channel?.logo_asset_id],
    ["#channel-background", "guide-background", "Default background", channel?.background_asset_id],
    ["#channel-corner-bug", "corner-bug", "No corner bug", channel?.corner_bug_asset_id],
  ]) {
    const select = $(control);
    select.innerHTML = '<option value="">' + fallback + '</option>' +
      state.assets.filter(asset => asset.kind === kind)
        .map(asset => '<option value="' + escapeHtml(asset.id) + '">' + escapeHtml(asset.name) + '</option>').join("");
    select.value = selected || "";
  }
  $("#dialog-title").textContent = channel ? "Edit channel" : "Create channel";
  ++mediaLoadRequest;
  mediaChoices = [];
  selectedLineup.clear();
  for (const choice of buildMediaChoices(channel?.items || [], channel?.section || "")) {
    selectedLineup.set(choice.key, choice);
  }
  builderLibrarySelections.clear();
  builderLibraryKind = "all";
  builderLibraries = [];
  resetBuilderFilters();
  setBuilderLibraries();
  updateMediaSelection();
  $("#worldwide-on-save").hidden = Boolean(channel);
  $("#publish-on-save").checked = state.config.creator_published || state.config.auto_publish_channels !== false;
  $("#publish-on-save").disabled = Boolean(state.config.creator_published);
  renderChannelBrandingPreview();
  $("#channel-dialog").showModal();
  setBuilderStep(1);
  await loadBuilderLibraries(channel?.section);
}

document.addEventListener("click",async event=>{const button=event.target.closest("button,article[data-watch]");if(!button)return;if(button.dataset.watch)watchChannel(button.dataset.watch);if(button.dataset.view){document.querySelectorAll(".view,nav button").forEach(e=>e.classList.remove("active"));$(`#${button.dataset.view}`).classList.add("active");button.classList.add("active")}if(button.id==="refresh-guide")loadGuide();if(button.id==="new-channel")openChannel();if(button.dataset.edit)openChannel(state.channels.find(c=>c.id===button.dataset.edit));if(button.dataset.delete&&confirm("Delete this channel?")){try{await api(`/api/channels/${button.dataset.delete}`,{method:"DELETE"});await refresh();toast("Channel deleted.")}catch(e){toast(e.message,true)}}if(button.dataset.deleteAsset&&confirm("Delete this artwork? Channels using it will keep working without the logo.")){try{await api(`/api/assets/${button.dataset.deleteAsset}`,{method:"DELETE"});await refresh();toast("Artwork deleted.")}catch(e){toast(e.message,true)}}if(button.dataset.tool){try{const data=await api(`/api/tools/${button.dataset.tool}`,{method:"POST",body:"{}"});toast(typeof data.result==="string"?data.result:"Tool finished.")}catch(e){toast(e.message,true)}}});
$("#setup-form").addEventListener("submit",async event=>{event.preventDefault();try{await api("/api/setup",{method:"POST",body:JSON.stringify({plex_url:$("#plex-url").value,plex_token:$("#plex-token").value,guide_hours:$("#guide-hours").value,bind_host:$("#bind-host").value,public_url:$("#public-url").value,directory_url:$("#directory-url").value})});$("#plex-token").value="";await refresh();toast("Standalone TV server settings saved.")}catch(e){toast(e.message,true)}});
async function publishStudio(event){
  event.preventDefault();
  const status=$("#creator-publish-status"),button=$("#publish-creator");
  status.className="connection-result wide";
  status.textContent="Connecting your studio to ChannelForge TV…";
  button.disabled=true;
  const payload={
    name:$("#creator-name").value,slug:$("#creator-slug").value,description:$("#creator-description").value,
    logo_asset_id:$("#creator-logo-asset").value,logo_url:$("#creator-logo-url").value,
    public_url:$("#creator-pairing-url").value,directory_url:$("#directory-url").value
  };
  try{
    await api("/api/directory/publish",{method:"POST",body:JSON.stringify(payload)});
    await refresh();
    toast("Studio published to the ChannelForge TV directory.");
  }catch(error){
    status.classList.add("error");
    status.textContent=error.message;
  }finally{button.disabled=false}
}
$("#creator-publish-form").addEventListener("submit",publishStudio);
$("#unpublish-creator").onclick=async()=>{if(!confirm("Remove this studio from ChannelForge TV? Your local channels will not be deleted."))return;try{await api("/api/directory/publish",{method:"DELETE"});await refresh();toast("Studio removed from the TV directory.")}catch(error){toast(error.message,true)}};
$("#builder-library-types").addEventListener("click", event => {
  const type = event.target.closest("[data-builder-library-kind]")?.dataset.builderLibraryKind;
  if (!type || type === builderLibraryKind) return;
  builderLibraryKind = type;
  setBuilderLibraries();
});
$("#channel-library").addEventListener("change", event => {
  builderLibrarySelections.set(builderLibraryKind, event.target.value);
  resetBuilderFilters();
  loadMedia(event.target.value);
});
$("#channel-form").addEventListener("submit",async event=>{event.preventDefault();const items=[...selectedLineup.values()].flatMap(choice=>choice.items);if(!items.length){toast("Select at least one playable title.",true);return}const id=$("#channel-id").value,payload={name:$("#channel-name").value,number:$("#channel-number").value,section:items[0].section,playback_mode:$("#playback-mode").value,logo_asset_id:$("#channel-logo").value,background_asset_id:$("#channel-background").value,corner_bug_asset_id:$("#channel-corner-bug").value,items};if(id && restorePresetOnSave)payload.restore_preset=true;if(!id)payload.publish_worldwide=$("#publish-on-save").checked;try{const saved=await api(id?`/api/channels/${id}`:"/api/channels",{method:id?"PUT":"POST",body:JSON.stringify(payload)});$("#channel-dialog").close();await refresh();toast(saved.publishing_pending?"Channel saved. Publishing this studio worldwide in the background.":"Channel saved and guide regenerated.")}catch(e){toast(e.message,true)}});
$("#generate-asset").addEventListener("submit",async event=>{event.preventDefault();try{await api("/api/assets/generate",{method:"POST",body:JSON.stringify({name:$("#asset-name").value,kind:$("#asset-kind").value,primary:$("#asset-primary").value,secondary:$("#asset-secondary").value})});await refresh();toast("Original artwork generated.")}catch(e){toast(e.message,true)}});
$("#upload-asset").addEventListener("submit",async event=>{event.preventDefault();const file=$("#upload-file").files[0];if(!file)return;try{const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error("Could not read that file."));reader.readAsDataURL(file)});await api("/api/assets/upload",{method:"POST",body:JSON.stringify({name:$("#upload-name").value,kind:$("#upload-kind").value,data})});event.target.reset();await refresh();toast("Artwork added to your library.")}catch(e){toast(e.message,true)}});
$("#close-dialog").onclick=$("#cancel-dialog").onclick=()=>$("#channel-dialog").close();
$("#regenerate").onclick=()=>api("/api/exports",{method:"POST",body:"{}"}).then(()=>toast("Exports regenerated.")).catch(e=>toast(e.message,true));
$("#check-update").onclick=()=>checkForUpdate(false);$("#install-update").onclick=()=>installUpdate(false);$("#plex-signin").onclick=startPlexSignin;
$("#start-connect").onclick=()=>{showView("setup");$("#workspace-title").textContent="Sources & sharing"};
$("#setup-plex-signin").onclick=startPlexSignin;
$("#resume-publishing").onclick=async()=>{
  try{
    await api("/api/directory/resume",{method:"POST",body:"{}"});
    await refresh();
  }catch(error){toast(error.message,true)}
};
$("#local-library-form").addEventListener("submit",async event=>{event.preventDefault();try{await api("/api/local/libraries",{method:"POST",body:JSON.stringify({name:$("#local-library-name").value,path:$("#local-library-path").value})});event.target.reset();await refresh();await loadWorkspaceLibraries(true);toast("Local media library added.")}catch(error){toast(error.message,true)}});
$("#movie-upload-form").addEventListener("submit",event=>{event.preventDefault();const file=$("#movie-upload-file").files[0],section=$("#upload-library").value;if(!file||!section)return;const wrap=$("#movie-upload-progress"),bar=$("#movie-upload-bar"),percent=$("#movie-upload-percent"),button=event.target.querySelector("button[type=submit],button:not([type])");wrap.hidden=false;bar.value=0;button.disabled=true;const request=new XMLHttpRequest();request.open("POST",`/api/local/upload?section=${encodeURIComponent(section)}`);request.setRequestHeader("X-File-Name",encodeURIComponent(file.name));request.upload.onprogress=progress=>{if(!progress.lengthComputable)return;const value=Math.round(progress.loaded/progress.total*100);bar.value=value;percent.textContent=`${value}%`;$("#movie-upload-stage").textContent=`Uploading ${file.name}…`};request.onload=async()=>{button.disabled=false;if(request.status>=200&&request.status<300){event.target.reset();await refresh();toast("Movie uploaded and added to the library.")}else{try{toast(JSON.parse(request.responseText).error,true)}catch{toast("Movie upload failed.",true)}}};request.onerror=()=>{button.disabled=false;toast("Movie upload failed.",true)};request.send(file)});
document.addEventListener("click",async event=>{const button=event.target.closest("[data-delete-local]");if(!button||!confirm("Remove this local library? Existing channels keep their lineup, but files cannot play until the folder is added again."))return;try{await api(`/api/local/libraries/${button.dataset.deleteLocal}`,{method:"DELETE"});await refresh();await loadWorkspaceLibraries(true);toast("Local library removed.")}catch(error){toast(error.message,true)}});
$("#guide-earlier").onclick=()=>{guideOffsetHours=Math.max(-24,guideOffsetHours-6);loadGuide()};
$("#guide-now").onclick=()=>{guideOffsetHours=0;loadGuide()};
$("#guide-later").onclick=()=>{guideOffsetHours=Math.min(168,guideOffsetHours+6);loadGuide()};
$("#test-plex").onclick=async()=>{const result=$("#setup-result");result.className="connection-result wide";result.textContent="Testing Plex server and library access…";try{const data=await api("/api/plex/test",{method:"POST",body:JSON.stringify({plex_url:$("#plex-url").value,plex_token:$("#plex-token").value})});result.classList.add("success");result.textContent=`Connected to ${data.profile.server_name||"Plex"}. ${data.libraries} libraries are available.`}catch(error){result.classList.add("error");result.textContent=`Connection failed: ${error.message}`}};
$("#media-filter").addEventListener("input",refreshSmartMatches);
for(const selector of ["#smart-genre","#smart-year-min","#smart-year-max","#smart-rating"])$(selector).addEventListener("input",refreshSmartMatches);
$("#smart-select").onclick = () => {
  const matches = filteredMediaChoices();
  for (const choice of matches) selectedLineup.set(choice.key, choice);
  updateMediaSelection();
  toast(matches.length ? `Added ${matches.length} matching shows and titles.` : "No titles match those filters.", !matches.length);
};
$("#select-all-media").onclick = () => {
  for (const choice of filteredMediaChoices()) selectedLineup.set(choice.key, choice);
  updateMediaSelection();
};
$("#clear-media").onclick = () => {
  selectedLineup.clear();
  updateMediaSelection();
};
$("#media-list").addEventListener("change", event => {
  const input = event.target;
  if (!input.matches('input[type="checkbox"]')) return;
  const choice = mediaChoices[Number(input.value)];
  if (!choice) return;
  if (input.checked) selectedLineup.set(choice.key, choice);
  else selectedLineup.delete(choice.key);
  updateMediaSelection();
});
$("#selected-media-list").addEventListener("click", event => {
  const button = event.target.closest("[data-remove-media]");
  if (!button) return;
  const key = [...selectedLineup.keys()][Number(button.dataset.removeMedia)];
  selectedLineup.delete(key);
  updateMediaSelection();
});
$("#overview-create").onclick=$("#library-create-channel").onclick=()=>openChannel();
$("#builder-next").onclick=()=>{if(builderStep===1&&(!$("#channel-name").reportValidity()||!$("#channel-number").reportValidity()))return;if(builderStep===2&&!selectedLineup.size){toast("Select at least one playable title.",true);return}setBuilderStep(builderStep+1)};
$("#builder-back").onclick=()=>setBuilderStep(builderStep-1);
$("#workspace-library").addEventListener("change", async event => {
  const request = ++workspaceMediaRequest, section = event.target.value;
  const grid = $("#workspace-library-grid");
  workspaceMedia = [];
  workspaceDisplayLimit = workspaceBatchSize;
  $("#workspace-library-count").textContent = "";
  if (!section) { grid.textContent = "Choose a media library to browse."; return; }
  grid.textContent = "Loading playable media…";
  try {
    const data = await api(`/api/library/items?indexed=1&section=${encodeURIComponent(section)}`);
    if (request !== workspaceMediaRequest) return;
    workspaceMedia = data.items;
    renderWorkspaceMedia();
  } catch (error) {
    if (request === workspaceMediaRequest) grid.textContent = error.message;
  }
});
$("#workspace-library-search").addEventListener("input",()=>{workspaceDisplayLimit=workspaceBatchSize;renderWorkspaceMedia()});
$("#channel-search").addEventListener("input",event=>{const query=event.target.value.trim().toLowerCase();document.querySelectorAll("[data-channel-card]").forEach(card=>card.hidden=!card.dataset.search.includes(query))});
$("#topbar-scan").onclick=async()=>{const button=$("#topbar-scan");button.disabled=true;button.textContent="Scanning…";libraryMediaCache.clear();try{await loadWorkspaceLibraries(true);if(state.config.token_saved){const result=await api("/api/rewind/refresh",{method:"POST",body:"{}"});toast(`Libraries refreshed · ${result.movies} Plex movies added to Rewind Cinema.`)}else toast("Media libraries refreshed.");if($("#workspace-library").value)$("#workspace-library").dispatchEvent(new Event("change"));await refresh()}catch(error){toast(error.message,true)}finally{button.disabled=false;button.textContent="Scan libraries"}};
$("#copy-install-command").onclick=async()=>{try{await navigator.clipboard.writeText($("#install-command").textContent);toast("Install command copied.")}catch{toast("Could not copy automatically. Select the command and copy it manually.",true)}};
document.addEventListener("click",async event=>{const button=event.target.closest("[data-copy-target]");if(!button)return;try{await navigator.clipboard.writeText($(`#${button.dataset.copyTarget}`).textContent);button.textContent="✓ Copied";setTimeout(()=>button.textContent=button.dataset.copyTarget==="xmltv-url"?"Copy XMLTV URL":"Copy M3U URL",1800)}catch{toast("Could not copy automatically.",true)}});
$("#tv-player").addEventListener("loadstart",()=>{$("#playback-status").className="muted";$("#playback-status").textContent="Connecting through ChannelForge…"});
$("#tv-player").addEventListener("canplay",()=>{$("#playback-status").className="muted";$("#playback-status").textContent="Stream ready."});
$("#tv-player").addEventListener("playing",()=>{$("#playback-status").className="muted";$("#playback-status").textContent="✓ Playing live."});
$("#tv-player").addEventListener("error",()=>showPlaybackError(activeChannel,"Playback failed. Check that the media source is online, then select the channel again."));
$("#manual-plex-setup").onclick=()=>{clearInterval(plexSigninTimer);plexSigninTimer=0;$("#plex-onboarding").close();showView("setup")};
$("#add-local-onboarding").onclick=()=>{$("#plex-onboarding").close();showView("setup");$("#local-library-path").focus()};
refresh().then(()=>{$("#installed-version").textContent=`v${state.version}`;checkForUpdate(true)}).catch(e=>toast(e.message,true));
document.addEventListener("click",event=>{const target=event.target.closest("[data-watch],[data-view]");if(target?.dataset.watch)diagnosePlayback(target.dataset.watch);});
document.addEventListener("click",event=>{const target=event.target.closest("[data-view]");if(!target)return;const names={start:"Overview",watch:"Watch TV",channels:"Channels",library:"Library",assets:"Branding",exports:"Guide artwork",system:"System",setup:"Sources & sharing",updates:"Updates",tools:"Tools"};showView(target.dataset.view);$("#workspace-title").textContent=names[target.dataset.view]||"ChannelForge";if(target.dataset.view==="watch")loadGuide();if(target.dataset.view==="library")loadWorkspaceLibraries()});
document.addEventListener("click",event=>{if(event.target.closest("#new-channel,#overview-create,#library-create-channel"))setTimeout(()=>setBuilderStep(1),0)});
setInterval(()=>{const localCount=state.config.local_libraries?.length||0;$("#topbar-status").textContent=state.config.token_saved&&localCount?`Plex + ${localCount} local`:state.config.token_saved?"Plex connected":localCount?`${localCount} local folder${localCount===1?"":"s"} ready`:"No media source"},1000);
setInterval(()=>{$("#backup-count").textContent=`${state.backup_count||0} backups available`},1000);
$("#channel-list").addEventListener("click", async event => {
  const button = event.target.closest("button[data-channel-action]");
  if (!button) return;
  button.disabled = true;
  try {
    await api(`/api/channels/${encodeURIComponent(button.dataset.id)}/${button.dataset.channelAction}`, {method:"POST", body:"{}"});
    await refresh();
    toast({reair:"Channel is back on TV.",refresh:"Channel guide refreshed.",shuffle:"Lineup reshuffled."}[button.dataset.channelAction]);
  } catch (error) {
    button.disabled = false;
    toast(error.message, true);
  }
});
$("#channel-list").addEventListener("dragstart",event=>{const card=event.target.closest("[data-channel-card]");if(!card)return;draggedChannel=card.dataset.channelCard;card.classList.add("dragging")});
$("#channel-list").addEventListener("dragend",event=>{event.target.closest("[data-channel-card]")?.classList.remove("dragging")});
$("#channel-list").addEventListener("dragover",event=>{if(event.target.closest("[data-channel-card]"))event.preventDefault()});
$("#channel-list").addEventListener("drop",async event=>{event.preventDefault();const target=event.target.closest("[data-channel-card]");if(!target||!draggedChannel||target.dataset.channelCard===draggedChannel)return;const ids=state.channels.map(channel=>channel.id),from=ids.indexOf(draggedChannel),to=ids.indexOf(target.dataset.channelCard);ids.splice(to,0,ids.splice(from,1)[0]);try{const data=await api("/api/channels/order",{method:"POST",body:JSON.stringify({ids})});state.channels=data.channels;await refresh();toast("Channel order saved.")}catch(error){toast(error.message,true)}});
setInterval(async()=>{
  try{
    const current=await api("/api/status?brief=1");
    if(state.version&&current.version!==state.version)location.reload();
    else if(current.config)renderSharingStatus(current.config);
    if (state.channels?.some(c => Date.parse(c.air_until) <= Date.now() && state.now_playing?.[c.id])) {
      await refresh();
    }
  }catch{/* The updater may temporarily stop the server while installing. */}
},3000);

// One navigation model for keyboard, PC gamepads, and television-style D-pads.
function controllerTargets(){const scope=document.querySelector("dialog[open]")||document.querySelector(".view.active")||document;return[...scope.querySelectorAll('button:not([disabled]),a[href],input:not([type="hidden"]):not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])')].filter(element=>!element.hidden&&element.offsetParent!==null&&getComputedStyle(element).visibility!=="hidden")}
function moveControllerFocus(direction){const targets=controllerTargets();if(!targets.length)return;const current=targets.includes(document.activeElement)?document.activeElement:null;if(!current){targets[0].focus({preventScroll:true});targets[0].scrollIntoView({block:"nearest",inline:"nearest"});return}const origin=current.getBoundingClientRect(),ox=origin.left+origin.width/2,oy=origin.top+origin.height/2;let best=null,bestScore=Infinity;for(const target of targets){if(target===current)continue;const rect=target.getBoundingClientRect(),tx=rect.left+rect.width/2,ty=rect.top+rect.height/2,dx=tx-ox,dy=ty-oy,primary=direction==="left"?-dx:direction==="right"?dx:direction==="up"?-dy:dy,secondary=direction==="left"||direction==="right"?Math.abs(dy):Math.abs(dx);if(primary<=4)continue;const score=primary+secondary*2.25;if(score<bestScore){bestScore=score;best=target}}if(best){best.focus({preventScroll:true});best.scrollIntoView({behavior:"smooth",block:"nearest",inline:"nearest"})}}
function controllerBack(){const dialog=document.querySelector("dialog[open]");if(dialog){dialog.close();return}showView("start");document.querySelector('nav button[data-view="start"]')?.focus()}
function controllerActivate(){const target=document.activeElement;if(!target||target===document.body)return;const item=target.closest("label.media-item");if(item){const input=item.querySelector('input[type="checkbox"]');input.checked=!input.checked;input.dispatchEvent(new Event("change",{bubbles:true}));return}target.click()}
document.addEventListener("keydown",event=>{const editable=event.target.matches?.('input,textarea,select,[contenteditable="true"]');if(editable&&event.key!=="Escape")return;const direction={ArrowLeft:"left",ArrowRight:"right",ArrowUp:"up",ArrowDown:"down"}[event.key];if(direction){event.preventDefault();moveControllerFocus(direction)}else if((event.key==="Enter"||event.key===" ")&&document.activeElement?.matches?.("[tabindex]")){event.preventDefault();controllerActivate()}else if(event.key==="Escape"){event.preventDefault();controllerBack()}});
let gamepadState={},gamepadRepeat=0;
function pollGamepads(now){const pad=[...(navigator.getGamepads?.()||[])].find(Boolean);if(pad){const pressed={up:pad.buttons[12]?.pressed||pad.axes[1]<-.55,down:pad.buttons[13]?.pressed||pad.axes[1]>.55,left:pad.buttons[14]?.pressed||pad.axes[0]<-.55,right:pad.buttons[15]?.pressed||pad.axes[0]>.55,activate:pad.buttons[0]?.pressed,back:pad.buttons[1]?.pressed};for(const action of Object.keys(pressed)){const repeatable=["up","down","left","right"].includes(action),canRepeat=repeatable&&pressed[action]&&gamepadState[action]&&now-gamepadRepeat>190;if(pressed[action]&&(!gamepadState[action]||canRepeat)){if(repeatable){moveControllerFocus(action);gamepadRepeat=now}else if(action==="activate")controllerActivate();else if(action==="back")controllerBack()}}gamepadState=pressed}else gamepadState={};requestAnimationFrame(pollGamepads)}
requestAnimationFrame(pollGamepads);

