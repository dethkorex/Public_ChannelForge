sub init()
  m.guide = m.top.findNode("guide") : m.video = m.top.findNode("video") : m.creators = m.top.findNode("creators") : m.channels = m.top.findNode("channels") : m.vault = m.top.findNode("vault") : m.pair = m.top.findNode("pair") : m.updates = m.top.findNode("updates") : m.status = m.top.findNode("status") : m.directoryTimer = m.top.findNode("directoryTimer")
  m.creators.observeField("itemSelected", "creatorSelected") : m.channels.observeField("itemSelected", "channelSelected") : m.vault.observeField("itemSelected", "vaultSelected") : m.pair.observeField("buttonSelected", "showPairing") : m.updates.observeField("buttonSelected", "checkUpdates")
  m.directoryTimer.observeField("fire", "refreshCatalog") : m.directoryTimer.control = "start"
  m.liveRetryTimer = m.top.findNode("liveRetryTimer")
  m.liveRetryTimer.observeField("fire", "resumePlayback")
  m.playbackDeadline = m.top.findNode("playbackDeadline")
  m.playbackDeadline.observeField("fire", "playbackTimedOut")
  m.updates.text = "UPDATES  •  v" + CreateObject("roAppInfo").GetVersion()
  m.progressTimer = m.top.findNode("progressTimer")
  m.progressTimer.observeField("fire", "checkPlaybackProgress")
  m.upcomingTimer = m.top.findNode("upcomingTimer")
  m.upcomingTimer.observeField("fire", "showUpcomingBanner")
  m.programBoundaryTimer = m.top.findNode("programBoundaryTimer")
  m.programBoundaryTimer.observeField("fire", "scheduleNextProgram")
  m.video.observeField("state", "videoStateChanged")
  m.video.observeField("position", "trackPlaybackPosition")
  m.bannerTimer = m.top.findNode("bannerTimer")
  m.bannerTimer.observeField("fire", "hideChannelBanner")
  m.bufferPanel = m.top.findNode("buffering")
  m.bufferTitle = m.top.findNode("bufferTitle")
  m.bufferDetails = m.top.findNode("bufferDetails")
  m.insertMovie = m.top.findNode("insertMovie")
  m.insertMovie.observeField("buttonSelected", "insertSelectedMovie")
  m.moreMovies = m.top.findNode("moreMovies")
  m.moreMovies.observeField("buttonSelected", "loadMoreMovies")
  m.tapeTimer = m.top.findNode("tapeTimer")
  m.tapeTimer.observeField("fire", "tapeMotionDone")
  m.ejectingMovie = false
  m.activeMovie = invalid
  m.liveItem = invalid
  m.liveProgramStop = 0
  m.checkingOffline = false
  m.liveRetries = 0
  m.waitingForVideo = false
  m.retryPending = false
  m.resolvingLive = false
  m.resolvingMovie = false
  m.upcomingDue = false
  m.upcomingShown = false
  m.catalogLoading = false
  m.creatorUrl = ""
  m.creatorId = ""
  m.movieKeys = {}
  m.pairBase = CreateObject("roRegistrySection", "ChannelForge").Read("creator_url")
  refreshCatalog()
end sub
sub refreshCatalog()
  if m.guide.visible then m.status.text = "Refreshing public creator studios…"
  requestCatalog()
end sub
sub requestCatalog()
  if m.catalogLoading then return
  m.catalogLoading = true
  task = CreateObject("roSGNode", "CatalogTask") : task.url = "https://channelforge-directory.fresh-anorak.workers.dev/v1/catalog" : task.fallbackUrl = "https://raw.githubusercontent.com/dethkorex/Public_ChannelForge/main/directory/catalog.json" : task.observeField("content", "catalogLoaded")
  if m.directoryRows <> invalid
    task.previousRows = m.directoryRows
    task.hasPrevious = true
  end if
  m.catalogTask = task
  task.control = "run"
end sub
sub catalogLoaded(event as object)
  if not currentTask(event, m.catalogTask) then return
  m.catalogLoading = false
  m.directoryRows = m.catalogTask.rows
  root = event.GetData()
  if m.pairBase <> "" then addPaired(root, m.pairBase)
  m.creators.content = root
  selected = -1
  for index = 0 to root.GetChildCount() - 1
    creator = root.GetChild(index)
    if (m.creatorId <> "" and creator.studioId = m.creatorId) or creator.url = m.creatorUrl then selected = index
  end for
  if selected < 0 and not m.guide.visible then closeRemovedStudio()
  if root.GetChildCount() > 0
    if selected < 0 then selected = 0
    m.creators.jumpToItem = selected
    if m.guide.visible
      loadCreator(selected)
    else if m.resolvingMovie and m.activeMovie <> invalid and not m.ejectingMovie
      m.resolvingMovie = false
      creator = root.GetChild(selected)
      suffix = "/api/public/vault"
      endpoint = creator.shortDescriptionLine2
      path = Instr(9, m.activeMovie.url, "/vault/")
      if Right(endpoint, Len(suffix)) = suffix and path > 0 and Instr(path, m.activeMovie.url, "?") = 0
        m.activeMovie.url = Left(endpoint, Len(endpoint) - Len(suffix)) + Mid(m.activeMovie.url, path)
      end if
      restartMovie()
    else if m.resolvingLive
      creator = root.GetChild(selected)
      if creator.statusUrl <> invalid and creator.statusUrl <> ""
        m.guideStatusUrl = creator.statusUrl + "?guide=brief"
        refreshOnAirGuide()
      else
        m.resolvingLive = false
        restartLive()
      end if
    else if m.liveItem <> invalid and not m.retryPending and m.video.state = "playing"
      refreshOnAirGuide()
    end if
    return
  end if
  clearStudio()
  if m.catalogTask.stale
    m.status.text = "Directory unavailable. Retrying automatically."
  else
    m.status.text = "No studios are registered."
  end if
  m.pair.SetFocus(true)
end sub
function currentTask(event as object, task as dynamic) as boolean
  if task = invalid then return false
  return event.GetRoSGNode().IsSameNode(task)
end function
sub cancelContentTask(task as dynamic)
  if task = invalid then return
  task.unobserveField("content")
  task.control = "stop"
end sub
sub clearStudio()
  cancelContentTask(m.playlistTask)
  cancelContentTask(m.vaultTask)
  cancelContentTask(m.onAirGuideTask)
  m.playlistTask = invalid
  m.vaultTask = invalid
  m.onAirGuideTask = invalid
  m.channels.content = invalid
  m.vault.content = invalid
  m.selectedMovie = invalid
  m.movieKeys = {}
  m.creatorUrl = ""
  m.creatorId = ""
  m.guideStatusUrl = ""
  m.top.findNode("movieDetails").visible = false
  m.moreMovies.visible = false
end sub
sub closeRemovedStudio()
  m.liveItem = invalid
  m.resolvingLive = false
  m.resolvingMovie = false
  m.retryPending = false
  m.activeMovie = invalid
  m.guide.visible = true
  m.video.control = "stop"
  m.video.visible = false
  m.bufferPanel.visible = false
  m.liveRetryTimer.control = "stop"
  m.upcomingTimer.control = "stop"
  m.programBoundaryTimer.control = "stop"
  m.tapeTimer.control = "stop"
  m.top.findNode("tapeMotion").visible = false
  m.ejectingMovie = false
  clearPlaybackDeadline()
  hideChannelBanner()
end sub
sub showPairing()
  dialog = CreateObject("roSGNode", "KeyboardDialog")
  dialog.title = "Optional direct Creator pairing"
  dialog.message = "Normally public studios appear automatically. Enter a Creator HTTPS URL only when direct recovery is needed."
  dialog.buttons = ["PAIR", "CANCEL"]
  dialog.observeField("buttonSelected", "pairingSelected")
  m.top.dialog = dialog
end sub
sub pairingSelected()
  dialog = m.top.dialog
  if dialog = invalid then return
  if dialog.buttonSelected = 0
    base = dialog.text.Trim()
    while right(base, 1) = "/"
      base = left(base, len(base) - 1)
    end while
    suffix = "/api/public/channels"
    if len(base) > len(suffix) and right(base, len(suffix)) = suffix then base = left(base, len(base) - len(suffix))
    if left(base, 8) <> "https://"
      m.status.text = "Pairing URLs must begin with https://"
    else
      registry = CreateObject("roRegistrySection", "ChannelForge")
      registry.Write("creator_url", base)
      registry.Flush()
      connectPaired(base)
    end if
  end if
  dialog.close = true
end sub
sub connectPaired(base as string)
  m.pairBase = base
  root = m.creators.content
  if root = invalid then root = CreateObject("roSGNode", "ContentNode")
  index = addPaired(root, base)
  m.creators.content = root
  m.creators.jumpToItem = index
  loadCreator(index)
end sub
function addPaired(root as object, base as string) as integer
  for index = 0 to root.GetChildCount() - 1
    if root.GetChild(index).url = base + "/exports/channels.m3u" then return index
  end for
  creator = root.CreateChild("ContentNode")
  creator.title = "Paired Creator"
  creator.description = "Direct secure pairing"
  creator.url = base + "/exports/channels.m3u"
  creator.AddField("statusUrl", "string", false)
  creator.statusUrl = base + "/api/public/channels"
  creator.shortDescriptionLine2 = base + "/api/public/vault"
  return root.GetChildCount() - 1
end function
sub creatorSelected()
  loadCreator(m.creators.itemSelected)
end sub
sub loadCreator(index as integer)
  if m.creators.content = invalid then return
  creator = m.creators.content.GetChild(index)
  if creator = invalid then return
  id = ""
  if creator.studioId <> invalid then id = creator.studioId
  switched = m.creatorUrl <> creator.url or m.creatorId <> id
  if switched then clearStudio()
  m.creatorId = id
  m.creatorUrl = creator.url
  m.status.text = "Loading " + creator.title + "…"
  task = CreateObject("roSGNode", "PlaylistTask") : task.url = creator.url
  if creator.studioId <> invalid then task.studioId = creator.studioId
  if creator.statusUrl <> invalid and creator.statusUrl <> ""
    task.url = creator.statusUrl + "?guide=brief"
    task.onlineCatalog = true
  end if
  m.guideStatusUrl = task.url
  if not task.onlineCatalog then m.guideStatusUrl = ""
  cancelContentTask(m.playlistTask)
  task.observeField("content", "playlistLoaded")
  m.playlistTask = task
  task.control = "run"
  if switched or m.vault.content = invalid
    m.selectedMovie = invalid
    m.top.findNode("movieDetails").visible = false
    m.moreMovies.visible = false
    cancelContentTask(m.vaultTask)
    vaultTask = CreateObject("roSGNode", "VaultTask") : vaultTask.url = creator.shortDescriptionLine2 : vaultTask.observeField("content", "vaultLoaded")
    m.vaultTask = vaultTask
    vaultTask.control = "run"
  end if
end sub
sub vaultLoaded(event as object)
  if not currentTask(event, m.vaultTask) then return
  if not m.vaultTask.loaded
    m.moreMovies.text = "RETRY MOVIES"
    m.moreMovies.visible = true
    return
  end if
  if m.vaultTask.offset = 0 or m.vault.content = invalid
    m.vault.content = m.vaultTask.content
    m.movieKeys = {}
    for index = 0 to m.vault.content.GetChildCount() - 1
      m.movieKeys[m.vault.content.GetChild(index).movieKey] = true
    end for
  else
    current = m.vault.content
    page = m.vaultTask.content
    for index = 0 to page.GetChildCount() - 1
      movie = page.GetChild(index)
      if not m.movieKeys.DoesExist(movie.movieKey)
        m.movieKeys[movie.movieKey] = true
        entry = current.CreateChild("ContentNode")
        entry.title = movie.title
        entry.hdPosterUrl = movie.hdPosterUrl
        entry.description = movie.description
        entry.url = movie.url
        entry.streamFormat = movie.streamFormat
        entry.shortDescriptionLine1 = movie.shortDescriptionLine1
      end if
    end for
    if m.guide.visible then m.vault.SetFocus(true)
  end if
  m.moreMovies.visible = m.vaultTask.nextOffset > 0
  m.moreMovies.text = "MORE MOVIES"
end sub
sub loadMoreMovies()
  if m.vaultTask = invalid then return
  offset = m.vaultTask.offset
  if m.vaultTask.loaded then offset = m.vaultTask.nextOffset
  if offset < 0 then return
  m.moreMovies.visible = false
  task = CreateObject("roSGNode", "VaultTask")
  task.url = m.vaultTask.url
  cancelContentTask(m.vaultTask)
  task.offset = offset
  task.observeField("content", "vaultLoaded")
  m.vaultTask = task
  task.control = "run"
end sub
sub playlistLoaded(event as object)
  if not currentTask(event, m.playlistTask) then return
  if m.playlistTask.stale and m.playlistTask.content.GetChildCount() = 0 and m.lastGuideUrl = m.creatorUrl
    m.status.text = "Studio unavailable. Showing the last guide."
    return
  end if
  m.lastGuideUrl = m.creatorUrl
  firstGuide = m.channels.content = invalid
  m.channels.content = m.playlistTask.content
  m.status.text = m.playlistTask.content.GetChildCount().ToStr() + " channels ready"
  if m.catalogTask <> invalid and m.catalogTask.stale then m.status.text += " • Directory incomplete"
  if m.playlistTask.stale then m.status.text = "Studio unavailable. Reconnecting..."
  if firstGuide and m.guide.visible and not m.insertMovie.HasFocus() and not m.moreMovies.HasFocus() then m.channels.SetFocus(true)
end sub
sub vaultSelected()
  if m.vault.content = invalid then return
  item = m.vault.content.GetChild(m.vault.itemSelected)
  if item = invalid then return
  m.selectedMovie = item
  m.top.findNode("movieTitle").text = item.title
  m.top.findNode("moviePoster").uri = item.hdPosterUrl
  m.top.findNode("movieDetails").visible = true
  m.insertMovie.SetFocus(true)
end sub
sub insertSelectedMovie()
  item = m.selectedMovie
  if item = invalid then return
  m.activeMovie = item
  m.moviePosition = 0
  m.movieRetryPosition = 0
  m.movieRetries = 0
  m.retryPending = false
  m.resolvingLive = false
  m.resolvingMovie = false
  clearPlaybackDeadline()
  m.liveItem = invalid
  m.liveRetryTimer.control = "stop"
  m.upcomingTimer.control = "stop"
  m.programBoundaryTimer.control = "stop"
  hideChannelBanner()
  showBuffering("CONNECTING TO THE STUDIO", item.title)
  item.HttpCertificatesFile = "common:/certs/ca-bundle.crt"
  m.video.content = item : m.video.visible = true : m.guide.visible = false : m.video.SetFocus(true)
  animateTape(true)
end sub
sub animateTape(inserting as boolean)
  if m.activeMovie = invalid then return
  m.ejectingMovie = not inserting
  m.crtWaiting = false
  m.crtLit = false
  m.top.findNode("crtLight").opacity = 0
  m.top.findNode("tapeTitle").text = m.activeMovie.title
  m.top.findNode("tapeArtwork").uri = m.activeMovie.hdPosterUrl
  m.top.findNode("tapeMotion").visible = true
  m.top.findNode("insertTapeMotion").control = "stop"
  m.top.findNode("ejectTapeMotion").control = "stop"
  if inserting
    m.top.findNode("insertTapeMotion").control = "start"
    m.tapeTimer.duration = 1.85
  else
    clearPlaybackDeadline()
    m.resolvingMovie = false
    m.retryPending = false
    m.liveRetryTimer.control = "stop"
    m.video.control = "pause"
    m.top.findNode("ejectTapeMotion").control = "start"
    m.tapeTimer.duration = 0.89
  end if
  m.tapeTimer.control = "stop"
  m.tapeTimer.control = "start"
end sub
sub tapeMotionDone()
  if m.ejectingMovie
    m.top.findNode("tapeMotion").visible = false
    clearPlaybackDeadline()
    m.ejectingMovie = false
    m.activeMovie = invalid
    m.video.visible = false
    m.guide.visible = true
    m.bufferPanel.visible = false
    m.video.control = "stop"
    m.insertMovie.SetFocus(true)
  else if m.crtLit = true
    m.top.findNode("tapeMotion").visible = false
    m.crtLit = false
  else if not m.guide.visible and m.activeMovie <> invalid
    m.crtWaiting = true
    m.video.control = "play"
    startPlaybackDeadline()
  end if
end sub
sub checkUpdates()
  task = CreateObject("roSGNode", "UpdateTask") : task.observeField("result", "updateLoaded") : task.control = "run" : m.updateTask = task
  m.updates.text = "UPDATES  •  CHECKING…"
end sub
sub updateLoaded()
  m.updates.text = m.updateTask.result
  if m.updateTask.releaseTag = "" then return
  dialog = CreateObject("roSGNode", "Dialog")
  dialog.title = "What's new in " + m.updateTask.releaseTag
  dialog.message = m.updateTask.notes
  if dialog.message = "" then dialog.message = "Playback and reliability improvements."
  dialog.buttons = ["DONE"]
  dialog.observeField("buttonSelected", "dismissUpdateNotes")
  m.top.dialog = dialog
end sub
sub dismissUpdateNotes()
  if m.top.dialog <> invalid then m.top.dialog.close = true
end sub
sub channelSelected()
  playChannel(m.channels.itemSelected)
end sub
sub playChannel(index as integer)
  if m.channels.content = invalid or m.channels.content.GetChildCount() = 0 then return
  if index < 0 then index = m.channels.content.GetChildCount() - 1
  if index >= m.channels.content.GetChildCount() then index = 0
  m.channels.jumpToItem = index
  item = m.channels.content.GetChild(index)
  clearPlaybackDeadline()
  m.resolvingLive = false
  m.retryPending = false
  cancelContentTask(m.onAirGuideTask)
  m.onAirGuideTask = invalid
  m.liveStableTime = invalid
  if item.availability = "offline"
    closeRemovedStudio()
    m.status.text = item.title + " is currently offline."
    m.channels.SetFocus(true)
    return
  end if
  m.liveRetryTimer.control = "stop"
  m.liveItem = item
  m.checkingOffline = false
  m.liveRetries = 0
  m.liveProgramStop = 0
  updateLiveProgramStop()
  showBuffering("CONNECTING TO THE STUDIO", item.title)
  item.HttpCertificatesFile = "common:/certs/ca-bundle.crt"
  item.live = item.continuous = true
  m.video.content = item : m.video.visible = true : m.video.control = "play" : m.guide.visible = false : m.video.SetFocus(true)
  startPlaybackDeadline()
  showChannelBanner(item)
end sub
sub videoStateChanged()
  if m.guide.visible or m.ejectingMovie or m.resolvingLive or m.resolvingMovie or m.retryPending then return
  state = m.video.state
  if state = "playing"
    if m.crtWaiting = true and m.activeMovie <> invalid
      m.crtWaiting = false
      m.crtLit = true
      m.top.findNode("crtLight").opacity = 0.65
      m.tapeTimer.duration = 0.35
      m.tapeTimer.control = "start"
    end if
    clearPlaybackDeadline()
    m.lastProgress = CreateObject("roTimespan")
    m.lastPosition = m.video.position
    m.progressTimer.control = "start"
    m.bufferPanel.visible = false
    if m.liveItem <> invalid and m.liveRetries > 0 then m.liveStableTime = CreateObject("roTimespan")
    if m.liveItem <> invalid then showChannelBanner(m.liveItem)
    if m.upcomingDue then showUpcomingBanner()
  else if state = "buffering"
    m.progressTimer.control = "stop"
    m.liveStableTime = invalid
    startPlaybackDeadline()
    if m.video.content <> invalid then showBuffering("BUFFERING VIDEO", m.video.content.title)
  else if state = "finished" and m.liveItem <> invalid
    clearPlaybackDeadline()
    if m.liveItem.continuous = true
      clearPlaybackDeadline()
      m.programBoundaryTimer.control = "stop"
      showBuffering("BROADCAST FINISHED", "Press Back to return to the guide.")
    else
      scheduleNextProgram()
    end if
  else if state = "finished" and m.activeMovie <> invalid and not m.ejectingMovie
    animateTape(false)
  else if state = "paused"
    clearPlaybackDeadline()
  else if state = "error"
    playbackTimedOut()
  end if
end sub
sub startPlaybackDeadline()
  if m.waitingForVideo then return
  m.waitingForVideo = true
  m.playbackDeadline.control = "start"
end sub
sub clearPlaybackDeadline()
  m.playbackDeadline.control = "stop"
  m.progressTimer.control = "stop"
  m.waitingForVideo = false
end sub
sub checkPlaybackProgress()
  if not m.guide.visible and not m.ejectingMovie and m.video.state = "playing" and m.lastProgress <> invalid
    if m.lastProgress.TotalSeconds() >= 20 then playbackTimedOut()
  end if
end sub
sub playbackTimedOut()
  clearPlaybackDeadline()
  if m.crtWaiting = true then m.top.findNode("tapeMotion").visible = false
  m.crtWaiting = false
  if m.guide.visible or m.ejectingMovie or m.retryPending or m.resolvingLive or m.resolvingMovie then return
  m.liveRetryTimer.control = "stop"
  m.programBoundaryTimer.control = "stop"
  m.upcomingTimer.control = "stop"
  m.video.control = "stop"
  if scheduleMovieRetry() then return
  if m.liveItem <> invalid and m.liveRetries < 2
    m.liveRetries = m.liveRetries + 1
    m.retryPending = true
    showBuffering("RECONNECTING TO THE STUDIO", m.liveItem.title)
    m.liveRetryTimer.duration = m.liveRetries * 2
    m.liveRetryTimer.control = "start"
    return
  end if
  showBuffering("THE STUDIO DID NOT DELIVER VIDEO", "Press OK to retry or Back for the guide.")
  if m.liveItem <> invalid
    m.checkingOffline = true
    refreshOnAirGuide()
  end if
end sub
sub trackPlaybackPosition()
  if m.video.state <> "playing" or m.guide.visible or m.ejectingMovie then return
  if m.video.position = m.lastPosition then return
  m.lastPosition = m.video.position
  if m.lastProgress <> invalid then m.lastProgress.Mark()
  if m.liveItem <> invalid and m.liveRetries > 0 and m.video.state = "playing"
    if m.liveStableTime <> invalid and m.liveStableTime.TotalSeconds() >= 8 then m.liveRetries = 0
  end if
  if m.activeMovie = invalid or m.ejectingMovie or m.video.state <> "playing" then return
  m.moviePosition = m.video.position
  if m.movieRetries > 0 and m.moviePosition >= m.movieRetryPosition + 30 then m.movieRetries = 0
end sub
function scheduleMovieRetry() as boolean
  if m.activeMovie = invalid or m.guide.visible or m.ejectingMovie then return false
  if m.movieRetries >= 2 then return false
  m.movieRetryPosition = m.moviePosition
  m.movieRetries = m.movieRetries + 1
  m.retryPending = true
  showBuffering("RECONNECTING TO THE MOVIE", m.activeMovie.title)
  m.liveRetryTimer.duration = m.movieRetries * 2
  m.liveRetryTimer.control = "start"
  return true
end function
sub resumePlayback()
  if m.guide.visible or m.ejectingMovie then return
  m.retryPending = false
  if m.activeMovie = invalid
    if m.liveItem = invalid then return
    if m.liveRetries > 0 and m.creatorId <> ""
      m.resolvingLive = true
      requestCatalog()
    else
      continueLive()
    end if
    return
  end if
  if m.movieRetries > 0 and m.creatorId <> ""
    m.resolvingMovie = true
    requestCatalog()
  else
    restartMovie()
  end if
end sub
sub restartMovie()
  if m.activeMovie = invalid or m.guide.visible or m.ejectingMovie then return
  item = CreateObject("roSGNode", "ContentNode")
  item.url = m.activeMovie.url
  item.streamFormat = m.activeMovie.streamFormat
  item.title = m.activeMovie.title
  item.playStart = m.moviePosition
  item.HttpCertificatesFile = "common:/certs/ca-bundle.crt"
  m.video.content = item
  m.video.control = "play"
  startPlaybackDeadline()
end sub
sub scheduleNextProgram()
  if m.liveItem = invalid or m.guide.visible then return
  m.upcomingTimer.control = "stop"
  m.programBoundaryTimer.control = "stop"
  if m.liveItem.continuous = true
    updateLiveProgramStop()
    refreshOnAirGuide()
    return
  end if
  showBuffering("LOADING THE NEXT PROGRAM", m.liveItem.title)
  remaining = m.liveProgramStop - CreateObject("roDateTime").AsSeconds()
  m.liveRetryTimer.duration = 1
  if remaining > 0 then m.liveRetryTimer.duration = remaining + 1
  m.liveRetryTimer.control = "stop"
  m.liveRetryTimer.control = "start"
end sub
sub showBuffering(title as string, detail as string)
  m.bufferTitle.text = title
  m.bufferDetails.text = detail
  m.bufferPanel.visible = true
end sub
sub hideChannelBanner()
  m.top.findNode("banner").visible = false
  m.top.findNode("bannerLogo").visible = false
  m.top.findNode("bannerBg").visible = false
end sub
sub showChannelBanner(item as object)
  message = "CH " + item.shortDescriptionLine1 + "   " + item.title
  if item.programs <> invalid and item.programs.Count() > 0
    now = CreateObject("roDateTime").AsSeconds()
    for index = 0 to item.programs.Count() - 1
      program = item.programs[index]
      if program.start_epoch_ms = invalid or program.stop_epoch_ms = invalid
        startsAt = CreateObject("roDateTime") : startsAt.FromISO8601String(program.start)
        endsAt = CreateObject("roDateTime") : endsAt.FromISO8601String(program.stop)
        onNow = startsAt.AsSeconds() <= now and endsAt.AsSeconds() > now
      else
        onNow = program.start_epoch_ms / 1000# <= now and program.stop_epoch_ms / 1000# > now
      end if
      if onNow
        message = message + chr(10) + "NOW  " + program.title
        if index + 1 < item.programs.Count()
          message = message + chr(10) + "COMING UP NEXT  " + item.programs[index + 1].title
        end if
        exit for
      end if
    end for
  end if
  m.top.findNode("banner").text = message
  m.top.findNode("banner").visible = true
  logo = m.top.findNode("bannerLogo")
  logo.uri = item.hdPosterUrl
  logo.visible = item.hdPosterUrl <> ""
  m.top.findNode("bannerBg").visible = true
  m.bannerTimer.control = "stop"
  m.bannerTimer.control = "start"
end sub
sub updateLiveProgramStop()
  previousStop = m.liveProgramStop
  m.liveProgramStop = 0
  m.upcomingTimer.control = "stop"
  m.programBoundaryTimer.control = "stop"
  if m.liveItem = invalid or m.liveItem.programs = invalid then return
  now = CreateObject("roDateTime").AsSeconds()
  for index = 0 to m.liveItem.programs.Count() - 1
    program = m.liveItem.programs[index]
    if program.start_epoch_ms <> invalid and program.stop_epoch_ms <> invalid
      if program.start_epoch_ms / 1000# <= now and program.stop_epoch_ms / 1000# > now
        m.liveProgramStop = program.stop_epoch_ms / 1000#
        if previousStop <> m.liveProgramStop
          m.upcomingDue = false
          m.upcomingShown = false
        end if
        m.programBoundaryTimer.duration = m.liveProgramStop - now + 1.7
        m.programBoundaryTimer.control = "start"
        if index + 1 < m.liveItem.programs.Count()
          lead = (program.stop_epoch_ms - program.start_epoch_ms) / 3000#
          if lead > 45 then lead = 45
          delay = m.liveProgramStop - now - lead
          if delay < 1 then delay = 1
          m.upcomingTimer.duration = delay
          m.upcomingTimer.control = "start"
        end if
        return
      end if
    end if
  end for
end sub

sub showUpcomingBanner()
  m.upcomingDue = true
  if not m.upcomingShown and m.liveItem <> invalid and not m.guide.visible and m.video.state = "playing"
    m.upcomingShown = true
    showChannelBanner(m.liveItem)
  end if
end sub

sub refreshOnAirGuide()
  if m.guideStatusUrl = invalid or m.guideStatusUrl = "" then return
  cancelContentTask(m.onAirGuideTask)
  task = CreateObject("roSGNode", "PlaylistTask")
  task.url = m.guideStatusUrl
  task.onlineCatalog = true
  task.studioId = m.creatorId
  task.observeField("content", "onAirGuideLoaded")
  m.onAirGuideTask = task
  task.control = "run"
end sub

sub onAirGuideLoaded(event as object)
  if not currentTask(event, m.onAirGuideTask) or m.liveItem = invalid or m.guide.visible then return
  guideContent = m.onAirGuideTask.content
  resolving = m.resolvingLive
  m.resolvingLive = false
  for index = 0 to guideContent.GetChildCount() - 1
    channel = guideContent.GetChild(index)
    if (channel.channelId <> "" and channel.channelId = m.liveItem.channelId) or channel.url = m.liveItem.url
      if channel.availability = "offline"
        if m.onAirGuideTask.catalogAvailable then channelOffline()
        return
      end if
      if m.checkingOffline then return
      m.liveItem = channel
      updateLiveProgramStop()
      if resolving then restartLive()
      if m.video.state = "playing" then showChannelBanner(m.liveItem)
      return
    end if
  end for
  if m.onAirGuideTask.catalogAvailable
    channelOffline()
  else if resolving
    restartLive()
  end if
end sub
sub channelOffline()
  clearPlaybackDeadline()
  m.retryPending = false
  m.liveRetryTimer.control = "stop"
  m.programBoundaryTimer.control = "stop"
  m.upcomingTimer.control = "stop"
  m.video.control = "stop"
  showBuffering("THIS CHANNEL IS OFFLINE", "Press OK to check again or Back for the guide.")
end sub

sub continueLive()
  if m.liveItem = invalid or m.guide.visible then return
  updateLiveProgramStop()
  refreshOnAirGuide()
  restartLive()
end sub
sub restartLive()
  if m.liveItem = invalid or m.guide.visible then return
  item = CreateObject("roSGNode", "ContentNode")
  item.url = m.liveItem.url
  item.title = m.liveItem.title
  item.streamFormat = m.liveItem.streamFormat
  item.HttpCertificatesFile = "common:/certs/ca-bundle.crt"
  item.live = m.liveItem.continuous = true
  m.video.control = "stop"
  m.video.content = item
  m.video.control = "play"
  startPlaybackDeadline()
end sub
function onKeyEvent(key as string, press as boolean) as boolean
  if not press then return false
  if m.guide.visible = false
    if key = "OK" and m.bufferPanel.visible and (m.retryPending or m.resolvingLive or m.resolvingMovie) then return true
    if key = "OK" and m.bufferPanel.visible and not m.waitingForVideo and m.activeMovie <> invalid and not m.ejectingMovie and not m.top.findNode("tapeMotion").visible
      m.movieRetries = 1
      resumePlayback()
      return true
    end if
    if key = "OK" and m.bufferPanel.visible and not m.waitingForVideo and m.liveItem <> invalid
      m.checkingOffline = false
      m.liveRetries = 1
      resumePlayback()
      return true
    end if
    if key = "back" or (key = "OK" and m.liveItem <> invalid)
      if m.activeMovie <> invalid
        if not m.ejectingMovie then animateTape(false)
        return true
      end if
      m.liveItem = invalid
      m.retryPending = false
      m.resolvingLive = false
      cancelContentTask(m.onAirGuideTask)
      m.onAirGuideTask = invalid
      clearPlaybackDeadline()
      m.liveRetryTimer.control = "stop"
      m.upcomingTimer.control = "stop"
      m.programBoundaryTimer.control = "stop"
      m.video.control = "stop"
      m.video.visible = false
      m.guide.visible = true
      m.bufferPanel.visible = false
      hideChannelBanner()
      m.channels.SetFocus(true)
      return true
    end if
    if m.liveItem <> invalid
      if key = "up" then playChannel(m.channels.itemFocused + 1) : return true
      if key = "down" then playChannel(m.channels.itemFocused - 1) : return true
    end if
  end if
  if m.guide.visible and m.insertMovie.HasFocus() and key = "left" then m.vault.SetFocus(true) : return true
  if m.guide.visible and m.moreMovies.HasFocus() and key = "up" then m.vault.SetFocus(true) : return true
  if m.guide.visible and m.vault.HasFocus() and key = "down" and m.moreMovies.visible
    if m.vault.itemFocused = m.vault.content.GetChildCount() - 1 then m.moreMovies.SetFocus(true) : return true
  end if
  if key = "right" and m.guide.visible and m.pair.HasFocus() then m.updates.SetFocus(true) : return true
  if key = "left" and m.guide.visible and m.updates.HasFocus() then m.pair.SetFocus(true) : return true
  if key = "left" and m.guide.visible and not m.vault.HasFocus() then m.creators.SetFocus(true) : return true
  if key = "right" and m.guide.visible and not m.vault.HasFocus() then m.channels.SetFocus(true) : return true
  if key = "down" and m.guide.visible and m.channels.HasFocus() and m.channels.content <> invalid and m.channels.itemFocused >= m.channels.content.GetChildCount() - 1 then m.vault.SetFocus(true) : return true
  if key = "up" and m.guide.visible and m.vault.HasFocus() then m.channels.SetFocus(true) : return true
  if key = "up" and m.guide.visible and m.creators.HasFocus() then m.pair.SetFocus(true) : return true
  if key = "down" and m.guide.visible and m.updates.HasFocus() then m.channels.SetFocus(true) : return true
  if key = "down" and m.guide.visible and m.pair.HasFocus() then m.creators.SetFocus(true) : return true
  return false
end function
