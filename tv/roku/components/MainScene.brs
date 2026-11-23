sub init()
  m.guide = m.top.findNode("guide") : m.video = m.top.findNode("video") : m.creators = m.top.findNode("creators") : m.channels = m.top.findNode("channels") : m.vault = m.top.findNode("vault") : m.pair = m.top.findNode("pair") : m.updates = m.top.findNode("updates") : m.status = m.top.findNode("status") : m.directoryTimer = m.top.findNode("directoryTimer")
  m.creators.observeField("itemSelected", "creatorSelected") : m.channels.observeField("itemSelected", "channelSelected") : m.vault.observeField("itemSelected", "vaultSelected") : m.pair.observeField("buttonSelected", "showPairing") : m.updates.observeField("buttonSelected", "checkUpdates")
  m.directoryTimer.observeField("fire", "refreshCatalog") : m.directoryTimer.control = "start"
  m.liveRetryTimer = m.top.findNode("liveRetryTimer")
  m.liveRetryTimer.observeField("fire", "continueLive")
  m.upcomingTimer = m.top.findNode("upcomingTimer")
  m.upcomingTimer.observeField("fire", "showUpcomingBanner")
  m.programBoundaryTimer = m.top.findNode("programBoundaryTimer")
  m.programBoundaryTimer.observeField("fire", "scheduleNextProgram")
  m.video.observeField("state", "videoStateChanged")
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
  m.catalogLoading = false
  m.creatorUrl = ""
  refreshCatalog()
end sub
sub refreshCatalog()
  if m.guide.visible = false or m.catalogLoading = true then return
  m.catalogLoading = true
  m.status.text = "Refreshing public creator studios…"
  task = CreateObject("roSGNode", "CatalogTask") : task.url = "https://channelforge-directory.fresh-anorak.workers.dev/v1/catalog" : task.fallbackUrl = "https://raw.githubusercontent.com/dethkorex/Public_ChannelForge/main/directory/catalog.json" : task.observeField("content", "catalogLoaded")
  m.catalogTask = task
  task.control = "run"
end sub
sub catalogLoaded()
  m.catalogLoading = false
  m.creators.content = m.catalogTask.content
  m.status.text = m.catalogTask.content.GetChildCount().ToStr() + " creator studios online"
  if m.catalogTask.content.GetChildCount() > 0
    selected = 0
    for index = 0 to m.catalogTask.content.GetChildCount() - 1
      if m.catalogTask.content.GetChild(index).url = m.creatorUrl then selected = index
    end for
    loadCreator(selected)
  else
    registry = CreateObject("roRegistrySection", "ChannelForge")
    paired = registry.Read("creator_url")
    if paired <> ""
      connectPaired(paired)
    else
      m.status.text = "No public studios are online. The TV app is standalone; optional direct pairing is available for recovery."
      m.pair.SetFocus(true)
    end if
  end if
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
  root = CreateObject("roSGNode", "ContentNode")
  creator = root.CreateChild("ContentNode")
  creator.title = "Paired Creator"
  creator.description = "Direct secure pairing"
  creator.url = base + "/exports/channels.m3u"
  creator.AddField("statusUrl", "string", false)
  creator.statusUrl = base + "/api/public/channels"
  creator.shortDescriptionLine2 = base + "/api/public/vault"
  m.creators.content = root
  loadCreator(0)
end sub
sub creatorSelected()
  loadCreator(m.creators.itemSelected)
end sub
sub loadCreator(index as integer)
  creator = m.creators.content.GetChild(index)
  if creator = invalid then return
  switched = m.creatorUrl <> creator.url
  m.creatorUrl = creator.url
  m.status.text = "Loading " + creator.title + "…"
  task = CreateObject("roSGNode", "PlaylistTask") : task.url = creator.url
  if creator.statusUrl <> invalid and creator.statusUrl <> ""
    task.url = creator.statusUrl + "?guide=brief"
    task.onlineCatalog = true
  end if
  m.guideStatusUrl = task.url
  if not task.onlineCatalog then m.guideStatusUrl = ""
  if m.playlistTask <> invalid then m.playlistTask.unobserveField("content")
  task.observeField("content", "playlistLoaded")
  m.playlistTask = task
  task.control = "run"
  if switched or m.vault.content = invalid
    m.selectedMovie = invalid
    m.top.findNode("movieDetails").visible = false
    m.moreMovies.visible = false
    if m.vaultTask <> invalid then m.vaultTask.unobserveField("content")
    vaultTask = CreateObject("roSGNode", "VaultTask") : vaultTask.url = creator.shortDescriptionLine2 : vaultTask.observeField("content", "vaultLoaded")
    m.vaultTask = vaultTask
    vaultTask.control = "run"
  end if
end sub
sub vaultLoaded()
  if not m.vaultTask.loaded
    m.moreMovies.text = "RETRY MOVIES"
    m.moreMovies.visible = true
    return
  end if
  if m.vaultTask.offset = 0 or m.vault.content = invalid
    m.vault.content = m.vaultTask.content
  else
    current = m.vault.content
    page = m.vaultTask.content
    for index = 0 to page.GetChildCount() - 1
      movie = page.GetChild(index)
      entry = current.CreateChild("ContentNode")
      entry.title = movie.title
      entry.hdPosterUrl = movie.hdPosterUrl
      entry.description = movie.description
      entry.url = movie.url
      entry.streamFormat = movie.streamFormat
      entry.shortDescriptionLine1 = movie.shortDescriptionLine1
    end for
    m.vault.SetFocus(true)
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
  task.offset = offset
  task.observeField("content", "vaultLoaded")
  m.vaultTask = task
  task.control = "run"
end sub
sub playlistLoaded()
  firstGuide = m.channels.content = invalid
  m.channels.content = m.playlistTask.content
  m.status.text = m.playlistTask.content.GetChildCount().ToStr() + " channels ready"
  if firstGuide and m.guide.visible and not m.insertMovie.HasFocus() and not m.moreMovies.HasFocus() then m.channels.SetFocus(true)
end sub
sub vaultSelected()
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
  m.liveItem = invalid
  m.liveRetryTimer.control = "stop"
  m.upcomingTimer.control = "stop"
  m.programBoundaryTimer.control = "stop"
  hideChannelBanner()
  showBuffering("CONNECTING TO THE STUDIO", item.title)
  m.video.content = item : m.video.visible = true : m.guide.visible = false : m.video.SetFocus(true)
  animateTape(true)
end sub
sub animateTape(inserting as boolean)
  if m.activeMovie = invalid then return
  m.ejectingMovie = not inserting
  m.top.findNode("tapeTitle").text = m.activeMovie.title
  m.top.findNode("tapeArtwork").uri = m.activeMovie.hdPosterUrl
  m.top.findNode("tapeMotion").visible = true
  m.top.findNode("insertTapeMotion").control = "stop"
  m.top.findNode("ejectTapeMotion").control = "stop"
  if inserting
    m.top.findNode("insertTapeMotion").control = "start"
    m.tapeTimer.duration = 1.45
  else
    m.video.control = "pause"
    m.top.findNode("ejectTapeMotion").control = "start"
    m.tapeTimer.duration = 0.89
  end if
  m.tapeTimer.control = "stop"
  m.tapeTimer.control = "start"
end sub
sub tapeMotionDone()
  m.top.findNode("tapeMotion").visible = false
  if m.ejectingMovie
    m.ejectingMovie = false
    m.activeMovie = invalid
    m.video.visible = false
    m.guide.visible = true
    m.bufferPanel.visible = false
    m.video.control = "stop"
    m.insertMovie.SetFocus(true)
  else if not m.guide.visible and m.activeMovie <> invalid
    m.video.control = "play"
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
  m.liveRetryTimer.control = "stop"
  m.liveItem = item
  m.checkingOffline = false
  m.liveRetries = 0
  updateLiveProgramStop()
  showBuffering("CONNECTING TO THE STUDIO", item.title)
  m.video.content = item : m.video.visible = true : m.video.control = "play" : m.guide.visible = false : m.video.SetFocus(true)
  showChannelBanner(item)
end sub
sub videoStateChanged()
  if m.guide.visible then return
  state = m.video.state
  if state = "playing"
    m.bufferPanel.visible = false
    m.liveRetries = 0
    if m.liveItem <> invalid then showChannelBanner(m.liveItem)
  else if state = "buffering"
    if m.video.content <> invalid then showBuffering("BUFFERING VIDEO", m.video.content.title)
  else if state = "finished" and m.liveItem <> invalid
    scheduleNextProgram()
  else if state = "finished" and m.activeMovie <> invalid and not m.ejectingMovie
    animateTape(false)
  else if state = "error"
    if m.liveItem <> invalid and m.liveRetries < 3
      showBuffering("RECONNECTING TO THE STUDIO", m.liveItem.title)
      m.liveRetries = m.liveRetries + 1
      m.liveRetryTimer.duration = m.liveRetries * 2
      m.liveRetryTimer.control = "start"
    else
      showBuffering("STREAM UNAVAILABLE", "Press Back to return to the guide.")
      if m.liveItem <> invalid
        m.checkingOffline = true
        m.programBoundaryTimer.control = "stop"
        refreshOnAirGuide()
      end if
    end if
  end if
end sub
sub scheduleNextProgram()
  if m.liveItem = invalid or m.guide.visible then return
  m.upcomingTimer.control = "stop"
  m.programBoundaryTimer.control = "stop"
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
  if m.liveItem <> invalid and not m.guide.visible and m.video.state = "playing" then showChannelBanner(m.liveItem)
end sub

sub refreshOnAirGuide()
  if m.guideStatusUrl = invalid or m.guideStatusUrl = "" then return
  if m.onAirGuideTask <> invalid then m.onAirGuideTask.unobserveField("content")
  task = CreateObject("roSGNode", "PlaylistTask")
  task.url = m.guideStatusUrl
  task.onlineCatalog = true
  task.observeField("content", "onAirGuideLoaded")
  m.onAirGuideTask = task
  task.control = "run"
end sub

sub onAirGuideLoaded()
  if m.liveItem = invalid or m.guide.visible then return
  guideContent = m.onAirGuideTask.content
  for index = 0 to guideContent.GetChildCount() - 1
    channel = guideContent.GetChild(index)
    if channel.url = m.liveItem.url
      if m.checkingOffline then return
      m.liveItem.programs = channel.programs
      updateLiveProgramStop()
      if m.video.state = "playing" then showChannelBanner(m.liveItem)
      return
    end if
  end for
  if m.checkingOffline and m.onAirGuideTask.catalogAvailable
    showBuffering("THIS CHANNEL IS OFFLINE", "Press Back to return to the guide.")
  end if
end sub

sub continueLive()
  if m.liveItem = invalid or m.guide.visible then return
  updateLiveProgramStop()
  refreshOnAirGuide()
  item = CreateObject("roSGNode", "ContentNode")
  item.url = m.liveItem.url
  item.title = m.liveItem.title
  item.streamFormat = m.liveItem.streamFormat
  m.video.control = "stop"
  m.video.content = item
  m.video.control = "play"
end sub
function onKeyEvent(key as string, press as boolean) as boolean
  if not press then return false
  if m.guide.visible = false
    if key = "back" or (key = "OK" and m.liveItem <> invalid)
      if m.activeMovie <> invalid
        if not m.ejectingMovie then animateTape(false)
        return true
      end if
      m.liveItem = invalid
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
  if key = "down" and m.guide.visible and m.channels.HasFocus() and m.channels.itemFocused >= m.channels.content.GetChildCount() - 1 then m.vault.SetFocus(true) : return true
  if key = "up" and m.guide.visible and m.vault.HasFocus() then m.channels.SetFocus(true) : return true
  if key = "up" and m.guide.visible and m.creators.HasFocus() then m.pair.SetFocus(true) : return true
  if key = "down" and m.guide.visible and m.updates.HasFocus() then m.channels.SetFocus(true) : return true
  if key = "down" and m.guide.visible and m.pair.HasFocus() then m.creators.SetFocus(true) : return true
  return false
end function
