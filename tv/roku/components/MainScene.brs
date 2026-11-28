sub init()
  m.guide = m.top.findNode("guide") : m.video = m.top.findNode("video") : m.creators = m.top.findNode("creators") : m.channels = m.top.findNode("channels") : m.vault = m.top.findNode("vault") : m.updates = m.top.findNode("updates") : m.status = m.top.findNode("status") : m.directoryTimer = m.top.findNode("directoryTimer")
  m.creators.observeField("itemSelected", "creatorSelected") : m.channels.observeField("itemSelected", "channelSelected") : m.vault.observeField("itemSelected", "vaultSelected") : m.updates.observeField("buttonSelected", "showSystem")
  m.home = m.top.findNode("home")
  m.livePresentation = m.top.findNode("livePresentation")
  m.libraryPresentation = m.top.findNode("libraryPresentation")
  m.homeLive = m.top.findNode("homeLive")
  m.homeCinema = m.top.findNode("homeCinema")
  m.homeContinue = m.top.findNode("homeContinue")
  m.continuePage = m.top.findNode("continuePage")
  m.continueMovies = m.top.findNode("continueMovies")
  m.homeContinue.observeField("buttonSelected", "showContinueWatching")
  m.continueMovies.observeField("itemSelected", "resumeSavedMovie")
  m.homeMenu = m.top.findNode("homeMenu")
  m.homeWatch = m.top.findNode("homeWatch")
  m.homeChannels = m.top.findNode("homeChannels")
  m.homeMovies = m.top.findNode("homeMovies")
  m.homeResumes = m.top.findNode("homeResumes")
  m.drawer = m.top.findNode("navigationDrawer")
  m.drawerItems = m.top.findNode("navigationItems")
  choices = CreateObject("roSGNode", "ContentNode")
  for each label in ["Home", "Live TV Guide", "Rewind Cinema", "Continue Watching", "System"]
    choices.CreateChild("ContentNode").title = label
  end for
  m.drawerItems.content = choices
  m.drawerItems.observeField("itemSelected", "drawerSelected")
  m.homeWatch.observeField("buttonSelected", "watchHomeChannel")
  m.homeChannels.observeField("itemSelected", "watchHomeSelection")
  m.homeMovies.observeField("itemSelected", "selectHomeMovie")
  m.homeResumes.observeField("itemSelected", "resumeFromHome")
  m.guideSort = m.top.findNode("guideSort")
  m.guideSchedule = m.top.findNode("guideSchedule")
  m.guideSchedule.observeField("buttonSelected", "openGuideSchedule")
  m.guideSort.observeField("buttonSelected", "toggleGuideSort")
  selectCatalogPresentation(false, false)
  m.sortByRating = false
  m.navigationTick = m.top.findNode("navigationTick")
  m.homeLive.observeField("buttonSelected", "openLiveGuide")
  m.homeCinema.observeField("buttonSelected", "openCinema")
  m.homeMenu.observeField("buttonSelected", "toggleDrawer")
  m.programArt = m.top.findNode("programArt")
  m.channels.observeField("itemFocused", "updateGuideArtwork")
  m.homeLive.SetFocus(true)
  m.directoryTimer.observeField("fire", "refreshCatalog") : m.directoryTimer.control = "start"
  m.liveRetryTimer = m.top.findNode("liveRetryTimer")
  m.liveRetryTimer.observeField("fire", "resumePlayback")
  appInfo = CreateObject("roAppInfo")
  m.relayRoute = appInfo.GetValue("channelforge_relay_route")
  installedVersion = appInfo.GetValue("channelforge_version")
  if installedVersion = "" then installedVersion = appInfo.GetVersion()
  if Right(installedVersion, 2) = ".0" then installedVersion = Left(installedVersion, Len(installedVersion) - 2)
  m.updateButtonLabel = "Alpha Version " + installedVersion
  m.updates.text = m.updateButtonLabel
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
  m.launchComplete = false
  m.deepLinkTimer = m.top.findNode("deepLinkTimer")
  m.deepLinkTimer.observeField("fire", "deepLinkTimedOut")
  m.top.observeField("deepLinkRequest", "deepLinkRequested")
  m.creatorUrl = ""
  m.creatorId = ""
  m.movieKeys = {}
  m.pairBase = CreateObject("roRegistrySection", "ChannelForge").Read("creator_url")
  m.qualityKbps = Int(Val(CreateObject("roRegistrySection", "ChannelForge").Read("quality_kbps")))
  refreshCatalog()
end sub
sub refreshCatalog()
  if m.guide.visible and not m.home.visible then updateGuideArtwork()
  if m.guide.visible then m.status.text = "Refreshing public creator studios…"
  requestCatalog()
end sub
sub showHome()
  cancelDeepLink()
  playMenuSound()
  m.continuePage.visible = false
  m.home.visible = true
  selectCatalogPresentation(false, false)
  if m.homeWatch <> invalid then updateHomeFeature()
  m.homeLive.SetFocus(true)
  completeAppLaunch()
end sub
sub toggleDrawer()
  playMenuSound()
  m.drawer.visible = not m.drawer.visible
  if m.drawer.visible then m.drawerItems.SetFocus(true) else m.homeMenu.SetFocus(true)
end sub
sub drawerSelected()
  choice = m.drawerItems.itemSelected
  m.drawer.visible = false
  if choice = 0 then showHome()
  if choice = 1 then openLiveGuide()
  if choice = 2 then openCinema()
  if choice = 3 then showContinueWatching()
  if choice = 4 then showSystem()
end sub
sub watchHomeChannel()
  cancelDeepLink()
  m.home.visible = false
  playChannel(0)
end sub
sub watchHomeSelection()
  cancelDeepLink()
  m.home.visible = false
  playChannel(m.homeChannels.itemSelected)
end sub
sub openGuideSchedule()
  if m.channels.content = invalid then return
  index = m.channels.itemFocused
  if index < 0 then index = 0
  m.scheduleChannel = m.channels.content.GetChild(index)
  if m.scheduleChannel = invalid then return
  m.scheduleHour = 0
  showGuideSchedule()
end sub
sub showGuideSchedule()
  channel = m.scheduleChannel
  now = CreateObject("roDateTime").AsSeconds()
  start = now + m.scheduleHour * 3600
  finish = start + 3600
  text = ""
  for each program in channel.programs
    if program.type <> "interstitial" and program.start_epoch_ms <> invalid and program.stop_epoch_ms <> invalid and program.title <> invalid
      if program.start_epoch_ms / 1000# < finish and program.stop_epoch_ms / 1000# > start
        clock = CreateObject("roDateTime")
        clock.FromSeconds(Int(program.start_epoch_ms / 1000#))
        clock.ToLocalTime()
        text += clock.GetHours().ToStr() + ":" + Right("0" + clock.GetMinutes().ToStr(), 2) + "  " + Left(program.title, 68) + Chr(10)
        if Len(text) > 190 then exit for
      end if
    end if
  end for
  if text = "" then text = "No programs listed for this time."
  panel = CreateObject("roSGNode", "Dialog")
  panel.title = channel.title + "  •  " + m.scheduleHour.ToStr() + "h ahead"
  panel.message = text
  panel.buttons = ["Previous hour", "Next hour", "Now", "Close"]
  panel.observeField("buttonSelected", "guideScheduleSelected")
  m.top.dialog = panel
end sub
sub guideScheduleSelected()
  if m.top.dialog = invalid then return
  choice = m.top.dialog.buttonSelected
  m.top.dialog = invalid
  if choice = 0 and m.scheduleHour > 0 then m.scheduleHour--
  if choice = 1 and m.scheduleHour < 12 then m.scheduleHour++
  if choice = 2 then m.scheduleHour = 0
  if choice = 3 then m.guideSchedule.SetFocus(true) else showGuideSchedule()
end sub
sub selectHomeMovie()
  if m.homeMovies.content = invalid then return
  item = m.homeMovies.content.GetChild(m.homeMovies.itemSelected)
  if item = invalid then return
  openCinema()
  revealMovie(item)
end sub
sub openLiveGuide()
  cancelDeepLink()
  completeAppLaunch()
  m.top.signalBeacon("EPGLaunchInitiate")
  playMenuSound()
  m.continuePage.visible = false
  m.home.visible = false
  selectCatalogPresentation(false, true)
  m.channels.SetFocus(true)
  updateGuideArtwork()
  m.top.signalBeacon("EPGLaunchComplete")
end sub
sub openCinema()
  cancelDeepLink()
  completeAppLaunch()
  playMenuSound()
  m.continuePage.visible = false
  m.home.visible = false
  selectCatalogPresentation(true, true)
  m.vault.SetFocus(true)
end sub
sub selectCatalogPresentation(cinema as boolean, showControls as boolean)
  if m.livePresentation <> invalid then m.livePresentation.visible = not cinema
  if m.libraryPresentation <> invalid then m.libraryPresentation.visible = cinema
  if m.guideSchedule <> invalid then m.guideSchedule.visible = showControls and not cinema
  if m.guideSort <> invalid then m.guideSort.visible = showControls and not cinema
end sub
sub playMenuSound()
  if m.navigationTick <> invalid and m.navigationTick.state <> "playing" then m.navigationTick.control = "play"
end sub
sub showSystem()
  playMenuSound()
  panel = CreateObject("roSGNode", "Dialog")
  panel.title = "System"
  diagnostics = "Playback diagnostics"
  panel.buttons = ["Check for Update", diagnostics, "Pair a Studio", "Close", "Playback quality"]
  panel.observeField("buttonSelected", "systemSelected")
  m.top.dialog = panel
end sub
sub systemSelected()
  if m.top.dialog = invalid then return
  choice = m.top.dialog.buttonSelected
  m.top.dialog = invalid
  if choice = 0 then checkUpdates()
  if choice = 1 then showDiagnostics()
  if choice = 2 then showPairing()
  if choice = 3 then focusSystemButton()
  if choice = 4 then showPlaybackQuality()
end sub
sub showPlaybackQuality()
  panel = CreateObject("roSGNode", "Dialog")
  panel.title = "Playback quality"
  panel.message = "Lower the quality when a channel or movie buffers."
  panel.buttons = ["HQ · up to 720p", "Balanced · 480p / 1 Mbps", "Data Saver · 270p / 0.4 Mbps", "Close"]
  panel.observeField("buttonSelected", "playbackQualitySelected")
  m.top.dialog = panel
end sub
sub showLivePlaybackControls()
  panel = CreateObject("roSGNode", "Dialog")
  panel.title = m.liveItem.title
  panel.message = "Live TV controls"
  panel.buttons = ["Quality", "Channel up", "Channel down", "Live TV Guide"]
  panel.observeField("buttonSelected", "livePlaybackControlSelected")
  m.top.dialog = panel
end sub
sub livePlaybackControlSelected()
  if m.top.dialog = invalid then return
  choice = m.top.dialog.buttonSelected
  m.top.dialog = invalid
  if m.liveItem = invalid then return
  if choice = 0 then showPlaybackQuality()
  if choice = 1 then playChannel(m.channels.itemFocused + 1)
  if choice = 2 then playChannel(m.channels.itemFocused - 1)
  if choice = 3 then returnFromLivePlayback()
end sub
sub returnFromLivePlayback()
  releaseLiveViewer()
  finishDiagnosticStall()
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
end sub
sub playbackQualitySelected()
  if m.top.dialog = invalid then return
  choice = m.top.dialog.buttonSelected
  m.top.dialog = invalid
  if choice < 0 or choice > 2 then return
  picks = [0, 1000, 400]
  if m.qualityKbps = picks[choice] then return
  m.qualityKbps = picks[choice]
  registry = CreateObject("roRegistrySection", "ChannelForge")
  registry.Write("quality_kbps", m.qualityKbps.ToStr())
  registry.Flush()
  if m.guide.visible
    focusSystemButton()
  else if m.activeMovie <> invalid and not m.ejectingMovie
    if m.video.position > 0 then m.moviePosition = m.video.position
    restartMovie()
  else if m.liveItem <> invalid
    restartLive()
  end if
end sub
sub focusSystemButton()
  if not m.guide.visible or m.continuePage.visible then return
  m.updates.SetFocus(true)
end sub
sub showDiagnostics()
  playMenuSound()
  panel = CreateObject("roSGNode", "Dialog")
  panel.title = "Playback diagnostics"
  panel.message = "Player state: " + m.video.state + Chr(10) + "Live retries: " + m.liveRetries.ToStr()
  if m.movieRetries <> invalid then panel.message = panel.message + Chr(10) + "Movie retries: " + m.movieRetries.ToStr()
  if m.diagnosticClock <> invalid
    panel.message += Chr(10) + "Playback ID " + m.viewerLease + Chr(10) + "Route " + m.diagnosticPath
    panel.message += Chr(10) + "Buffer events " + m.diagnosticStalls.ToStr() + " for " + diagnosticStallTime().ToStr() + " ms"
    if m.diagnosticFirst >= 0 then panel.message += Chr(10) + "First playback progress " + m.diagnosticFirst.ToStr() + " ms"
  end if
  panel.buttons = ["Close"]
  panel.observeField("buttonSelected", "dismissDiagnostics")
  m.top.dialog = panel
end sub
sub resetPlaybackDiagnostics(kind as string)
  m.viewerLease = ""
  for digit = 1 to 32
    m.viewerLease += Mid("0123456789abcdef", Rnd(16), 1)
  end for
  m.diagnosticClock = CreateObject("roTimespan")
  m.diagnosticKind = kind
  m.diagnosticPath = "unknown"
  m.diagnosticFirst = -1
  m.diagnosticStalls = 0
  m.diagnosticStallMs = 0
  m.diagnosticBuffer = invalid
  m.diagnosticPosition = 0
end sub
function diagnosticStallTime() as integer
  elapsed = m.diagnosticStallMs
  if m.diagnosticBuffer <> invalid then elapsed += m.diagnosticBuffer.TotalMilliseconds()
  return elapsed
end function
sub finishDiagnosticStall()
  if m.diagnosticBuffer = invalid then return
  m.diagnosticStallMs = diagnosticStallTime()
  m.diagnosticBuffer = invalid
end sub
sub dismissDiagnostics()
  m.top.dialog = invalid
  focusSystemButton()
end sub
function savedMovies() as object
  registry = CreateObject("roRegistrySection", "ChannelForge")
  raw = registry.Read("movie_history")
  if raw <> ""
    rows = ParseJson(raw)
    if type(rows) = "roArray" then return rows
  end if
  return []
end function
sub saveMoviePosition(completed as boolean)
  if m.activeMovie = invalid then return
  if not completed and m.moviePosition <= 0 then return
  movie = m.activeMovie
  key = movieBookmarkKey(m.movieStudioId, movie.movieKey)
  time = CreateObject("roDateTime")
  writeMovieBookmark(CreateObject("roRegistrySection", "ChannelForgePlayheads"), key, m.moviePosition, completed, time.AsSeconds())
  rows = []
  if not completed
    rows.Push({key: key, movieKey: movie.movieKey, studioId: m.movieStudioId,
        title: movie.title, url: movie.url, poster: movie.hdPosterUrl, position: m.moviePosition})
  end if
  for each previous in savedMovies()
    if previous.key <> key and rows.Count() < 12 then rows.Push(previous)
  end for
  registry = CreateObject("roRegistrySection", "ChannelForge")
  registry.Write("movie_history", FormatJson(rows))
  registry.Flush()
end sub
function resumePositionFor(studioId as string, movieKey as string) as dynamic
  key = movieBookmarkKey(studioId, movieKey)
  return movieResumePosition(CreateObject("roRegistrySection", "ChannelForgePlayheads"), key, savedMovies())
end function
sub showContinueWatching()
  cancelDeepLink()
  completeAppLaunch()
  m.home.visible = false
  m.continuePage.visible = true
  selectCatalogPresentation(false, false)
  root = CreateObject("roSGNode", "ContentNode")
  for each row in savedMovies()
    if row.position > 0 and row.url <> ""
      item = root.CreateChild("ContentNode")
      item.AddFields({movieKey: row.movieKey, savedStudioId: row.studioId, resumePosition: row.position, progress: 0.0})
      item.title = row.title
      item.url = row.url
      item.streamFormat = "hls"
      item.hdPosterUrl = row.poster
      item.shortDescriptionLine1 = "• Resume at " + movieTimeLabel(row.position)
    end if
  end for
  m.continueMovies.content = root
  label = m.top.findNode("continueStatus")
  if root.GetChildCount() > 0
    label.text = "Select a movie to resume"
    m.continueMovies.SetFocus(true)
  else
    label.text = "No unfinished movies yet. Press Back to return home."
    m.continuePage.SetFocus(true)
  end if
end sub
sub resumeFromHome()
  if m.homeResumes.content = invalid then return
  item = m.homeResumes.content.GetChild(m.homeResumes.itemSelected)
  if item = invalid then return
  m.home.visible = false
  startCinemaMovie(item, item.resumePosition, item.savedStudioId, false)
end sub
function movieTimeLabel(position as float) as string
  seconds = Int(position)
  minutes = Int(seconds / 60)
  remainder = (seconds MOD 60).ToStr()
  if Len(remainder) < 2 then remainder = "0" + remainder
  return minutes.ToStr() + ":" + remainder
end function
sub resumeSavedMovie()
  item = m.continueMovies.content.GetChild(m.continueMovies.itemSelected)
  if item = invalid then return
  startCinemaMovie(item, item.resumePosition, item.savedStudioId, false)
end sub
sub updateGuideArtwork()
  if m.libraryPresentation <> invalid and m.libraryPresentation.visible then return
  if m.home.visible or not m.guide.visible or m.channels.content = invalid then return
  index = m.channels.itemFocused
  if index < 0 then index = 0
  if index >= m.channels.content.GetChildCount() then return
  channel = m.channels.content.GetChild(index)
  feature = guideFeature(channel)
  m.top.findNode("programTitle").text = feature.title
  m.top.findNode("programChannel").text = "CH " + channel.shortDescriptionLine1 + "  •  " + channel.title
  m.top.findNode("programProgress").width = feature.fraction * 510
  if m.programArt.uri <> feature.artwork then m.programArt.uri = feature.artwork
end sub
function guideFeature(channel as object) as object
  result = {title: channel.title, artwork: channel.hdPosterUrl, fraction: 0.0}
  now = CreateObject("roDateTime").AsSeconds()
  for each program in channel.programs
    if program.type <> "interstitial" and program.start_epoch_ms <> invalid and program.stop_epoch_ms <> invalid
      start = program.start_epoch_ms / 1000# : finish = program.stop_epoch_ms / 1000#
      if start <= now and now < finish
        if program.title <> invalid then result.title = program.title
        for each art in [program.backdrop_url, program.series_poster_url, program.poster_url]
          if art <> invalid and art <> "" then result.artwork = art : exit for
        end for
        result.fraction = (now - start) / (finish - start)
        exit for
      end if
    end if
  end for
  return result
end function
sub updateHomeFeature()
  resumes = CreateObject("roSGNode", "ContentNode")
  titles = {}
  for each row in savedMovies()
    if row.position > 0 and row.url <> "" and row.title <> "" and not titles.DoesExist(row.movieKey)
      titles[row.movieKey] = true
      item = resumes.CreateChild("ContentNode")
      item.AddFields({movieKey: row.movieKey, resumePosition: row.position, savedStudioId: row.studioId, progress: 0.0})
      item.url = row.url
      item.title = row.title
      item.hdPosterUrl = row.poster
      item.streamFormat = "hls"
      item.shortDescriptionLine1 = "Resume at " + movieTimeLabel(row.position)
      if resumes.GetChildCount() >= 4 then exit for
    end if
  end for
  m.homeResumes.content = resumes
  m.homeResumes.visible = resumes.GetChildCount() > 0
  m.top.findNode("homeResumeHeading").visible = m.homeResumes.visible
  root = m.channels.content
  hasChannel = root <> invalid and root.GetChildCount() > 0
  m.homeWatch.visible = hasChannel
  previews = CreateObject("roSGNode", "ContentNode")
  if hasChannel
    last = root.GetChildCount() - 1
    if last > 4 then last = 4
    for index = 0 to last
      channel = root.GetChild(index)
      display = guideFeature(channel)
      item = previews.CreateChild("ContentNode")
      item.title = channel.title
      item.hdPosterUrl = display.artwork
      item.shortDescriptionLine1 = display.title
      item.AddFields({progress: display.fraction})
    end for
  end if
  m.homeChannels.content = previews
  if hasChannel
    channel = root.GetChild(0)
    feature = guideFeature(channel)
    m.top.findNode("homeEyebrow").text = "● LIVE NOW"
    m.top.findNode("homeTitle").text = feature.title
    m.top.findNode("homeCaption").text = "CH " + channel.shortDescriptionLine1 + "  •  " + channel.title
    if feature.artwork = "" then feature.artwork = "pkg:/images/channel-forge-tv.png"
    m.top.findNode("homeArt").uri = feature.artwork
  else
    m.top.findNode("homeEyebrow").text = "WELCOME TO CHANNELFORGE TV"
    m.top.findNode("homeTitle").text = "Live channels. One community dial."
    m.top.findNode("homeCaption").text = "Watch community live channels and explore shared movies."
    m.top.findNode("homeArt").uri = "pkg:/images/channel-forge-tv.png"
  end if
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
  if m.deepLink <> invalid
    if m.deepLink.phase <> "directory" then return
    if openLinkedStudio(root) then return
    showHome()
  end if
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
      refreshMovieAddress(m.activeMovie, m.movieStudioId)
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
  if not m.home.visible and not m.continuePage.visible then m.updates.SetFocus(true)
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
  cancelContentTask(m.homeVaultTask)
  cancelContentTask(m.onAirGuideTask)
  m.playlistTask = invalid
  m.vaultTask = invalid
  m.homeVaultTask = invalid
  m.onAirGuideTask = invalid
  m.channels.content = invalid
  m.vault.content = invalid
  if m.homeMovies <> invalid then m.homeMovies.content = invalid
  m.selectedMovie = invalid
  m.movieKeys = {}
  m.creatorUrl = ""
  m.creatorId = ""
  m.guideStatusUrl = ""
  m.top.findNode("movieDetails").visible = false
  m.moreMovies.visible = false
end sub
sub closeRemovedStudio()
  releaseLiveViewer()
  finishDiagnosticStall()
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
      dialog.message = "Pairing URLs must begin with https://. Check the address and retry."
      return
    else
      registry = CreateObject("roRegistrySection", "ChannelForge")
      registry.Write("creator_url", base)
      registry.Flush()
      dialog.close = true
      connectPaired(base)
      return
    end if
  end if
  dialog.close = true
  focusSystemButton()
end sub
sub connectPaired(base as string)
  m.pairBase = base
  root = m.creators.content
  if root = invalid then root = CreateObject("roSGNode", "ContentNode")
  index = addPaired(root, base)
  m.creators.content = root
  m.creators.jumpToItem = index
  m.home.visible = false
  m.creators.SetFocus(true)
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
  cancelDeepLink()
  loadCreator(m.creators.itemSelected)
end sub
sub loadCreator(index as integer)
  if m.creators.content = invalid then return
  creator = m.creators.content.GetChild(index)
  if creator = invalid then return
  id = ""
  if creator.studioId <> invalid then id = creator.studioId
  switched = m.creatorUrl <> creator.url or m.creatorId <> id
  if switched and (id = "" or m.creatorId <> id) then clearStudio()
  m.creatorId = id
  m.creatorUrl = creator.url
  m.status.text = "Loading " + creator.title + "…"
  task = CreateObject("roSGNode", "PlaylistTask") : task.url = creator.url
  m.guideStatusUrl = ""
  if creator.statusUrl <> invalid and creator.statusUrl <> ""
    task.fallbackUrl = creator.url
    task.url = creator.statusUrl + "?guide=extended"
    task.onlineCatalog = true
    m.guideStatusUrl = creator.statusUrl + "?guide=brief"
  end if
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
  if m.homeMovies <> invalid and creator.shortDescriptionLine2 <> ""
    if m.homeVaultTask = invalid or m.homeVaultTask.url <> creator.shortDescriptionLine2
      cancelContentTask(m.homeVaultTask)
      m.homeMovies.content = invalid
      m.top.findNode("homeRewindHeading").text = "FROM REWIND CINEMA"
      recent = CreateObject("roSGNode", "VaultTask")
      recent.url = creator.shortDescriptionLine2
      recent.recent = true
      recent.observeField("content", "homeVaultLoaded")
      m.homeVaultTask = recent
      recent.control = "run"
    end if
  end if
end sub
sub homeVaultLoaded(event as object)
  if not currentTask(event, m.homeVaultTask) or not m.homeVaultTask.loaded then return
  content = m.homeVaultTask.content
  hasDates = content.GetChildCount() > 0
  for each item in content.GetChildren(-1, 0)
    hasDates = hasDates and item.hasAddedDate
  end for
  if hasDates then m.top.findNode("homeRewindHeading").text = "RECENTLY ADDED TO REWIND"
  m.homeMovies.content = content
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
        entry.AddFields({movieKey: movie.movieKey})
        if movie.landscapeArtwork <> invalid then entry.AddFields({landscapeArtwork: movie.landscapeArtwork})
        entry.title = movie.title
        entry.hdPosterUrl = movie.hdPosterUrl
        entry.description = movie.description
        entry.url = movie.url
        entry.streamFormat = movie.streamFormat
        entry.shortDescriptionLine1 = movie.shortDescriptionLine1
      end if
    end for
    if m.guide.visible and not m.home.visible and (m.libraryPresentation = invalid or m.libraryPresentation.visible) then m.vault.SetFocus(true)
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
  m.lastGuideUrl = m.creatorUrl
  firstGuide = m.channels.content = invalid
  m.channels.content = m.playlistTask.content
  m.guideRows = m.channels.content.GetChildren(-1, 0)
  if m.sortByRating then applyGuideSort()
  if m.homeWatch <> invalid then updateHomeFeature()
  m.status.text = m.playlistTask.content.GetChildCount().ToStr() + " channels ready"
  if m.catalogTask <> invalid and m.catalogTask.stale then m.status.text += " • Directory incomplete"
  if m.playlistTask.stale then m.status.text = "Studio unavailable. No channels ready."
  if firstGuide and m.playlistTask.content.GetChildCount() > 0 and m.guide.visible and not m.home.visible and not m.continuePage.visible and not m.vault.HasFocus() and not m.insertMovie.HasFocus() and not m.moreMovies.HasFocus() then m.channels.SetFocus(true)
  updateGuideArtwork()
  playLinkedChannel()
end sub
sub toggleGuideSort()
  m.sortByRating = not m.sortByRating
  m.guideSort.text = "SORT: CHANNEL"
  if m.sortByRating then m.guideSort.text = "SORT: RATING"
  applyGuideSort()
end sub
sub applyGuideSort()
  root = m.channels.content
  if root = invalid or m.guideRows = invalid or m.guideRows.Count() < 2 then return
  ordered = []
  if m.sortByRating
    for rank = 1 to 10
      for each channel in m.guideRows
        if channel.ratingRank = rank then ordered.Push(channel)
      end for
    end for
  else
    ordered = m.guideRows
  end if
  root.RemoveChildrenIndex(root.GetChildCount(), 0)
  root.AppendChildren(ordered)
  m.channels.jumpToItem = 0
  updateGuideArtwork()
end sub
sub vaultSelected()
  if m.vault.content = invalid then return
  item = m.vault.content.GetChild(m.vault.itemSelected)
  if item = invalid then return
  revealMovie(item)
end sub
sub revealMovie(item as object)
  cancelDeepLink()
  playMenuSound()
  m.selectedMovie = item
  m.top.findNode("movieTitle").text = item.title
  m.top.findNode("moviePoster").uri = item.hdPosterUrl
  m.top.findNode("movieDetails").visible = true
  m.insertMovie.SetFocus(true)
end sub
sub insertSelectedMovie()
  item = m.selectedMovie
  if item = invalid then return
  playMenuSound()
  startCinemaMovie(item, resumePositionFor(m.creatorId, item.movieKey), m.creatorId, false)
end sub
sub refreshMovieAddress(item as object, studioId as string)
  if studioId = "" or m.creators.content = invalid then return
  root = m.creators.content
  suffix = "/api/public/vault"
  path = Instr(9, item.url, "/vault/")
  if path <= 0 or Instr(path, item.url, "?") > 0 then return
  for index = 0 to root.GetChildCount() - 1
    creator = root.GetChild(index)
    endpoint = creator.shortDescriptionLine2
    if creator.studioId = studioId and Right(endpoint, Len(suffix)) = suffix
      item.url = Left(endpoint, Len(endpoint) - Len(suffix)) + Mid(item.url, path)
      return
    end if
  end for
end sub
sub startCinemaMovie(item as object, position as float, studioId as string, direct as boolean)
  releaseLiveViewer()
  resetPlaybackDiagnostics("movie")
  m.activeMovie = item
  m.moviePosition = position
  m.bookmarkCheckpoint = CreateObject("roTimespan")
  m.bookmarkCheckpoint.Mark()
  m.movieActivityClock = invalid
  m.movieCompleted = false
  m.movieStudioId = studioId
  refreshMovieAddress(item, studioId)
  item.playStart = position
  m.movieRetries = 0
  m.retryPending = false
  m.resolvingLive = false
  m.resolvingMovie = false
  m.skipPreferredRelay = false
  clearPlaybackDeadline()
  m.liveItem = invalid
  m.liveRetryTimer.control = "stop"
  m.upcomingTimer.control = "stop"
  m.programBoundaryTimer.control = "stop"
  hideChannelBanner()
  showBuffering("CONNECTING TO THE STUDIO", item.title)
  m.video.content = playbackContent(item, false, false) : m.video.visible = true : m.guide.visible = false : m.video.SetFocus(true)
  if direct
    m.video.control = "play"
    startPlaybackDeadline()
  else
    animateTape(true)
  end if
end sub
sub animateTape(inserting as boolean)
  if m.activeMovie = invalid then return
  if not inserting then finishDiagnosticStall()
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
    saveMoviePosition(m.movieCompleted)
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
    if m.continuePage.visible
      showContinueWatching()
    else if m.top.findNode("movieDetails").visible
      selectCatalogPresentation(true, true)
      m.insertMovie.SetFocus(true)
    else
      selectCatalogPresentation(true, true)
      m.vault.SetFocus(true)
    end if
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
  if m.updateTask <> invalid and m.updateTask.result = "" then return
  task = CreateObject("roSGNode", "UpdateTask") : task.observeField("result", "updateLoaded") : m.updateTask = task
  m.updates.text = "UPDATES  •  CHECKING…"
  task.control = "run"
end sub
sub updateLoaded()
  m.updates.text = m.updateButtonLabel
  showUpdateDialog(m.updateTask.releaseTag, m.updateTask.notes, m.updateTask.result)
end sub
sub showUpdateDialog(releaseTag as string, notes as string, status as string)
  dialog = CreateObject("roSGNode", "Dialog")
  dialog.title = "What's new in " + releaseTag
  if releaseTag = ""
    dialog.title = "Roku updates"
  end if
  if status <> "" and notes <> "" then status = status + Chr(10) + Chr(10)
  dialog.message = status + notes
  dialog.buttons = ["DONE"]
  dialog.observeField("buttonSelected", "dismissUpdateNotes")
  m.top.dialog = dialog
end sub
sub dismissUpdateNotes()
  if m.top.dialog <> invalid then m.top.dialog.close = true
  focusSystemButton()
end sub
sub channelSelected()
  cancelDeepLink()
  playChannel(m.channels.itemSelected)
end sub
sub playChannel(index as integer)
  if m.channels.content = invalid or m.channels.content.GetChildCount() = 0 then return
  selectCatalogPresentation(false, true)
  if index < 0 then index = m.channels.content.GetChildCount() - 1
  if index >= m.channels.content.GetChildCount() then index = 0
  m.channels.jumpToItem = index
  item = m.channels.content.GetChild(index)
  clearPlaybackDeadline()
  m.resolvingLive = false
  m.retryPending = false
  cancelContentTask(m.onAirGuideTask)
  m.onAirGuideTask = invalid
  if item.availability = "offline"
    closeRemovedStudio()
    m.status.text = item.title + " is currently offline."
    m.channels.SetFocus(true)
    return
  end if
  m.liveRetryTimer.control = "stop"
  releaseLiveViewer()
  resetPlaybackDiagnostics("live")
  m.liveItem = item
  m.checkingOffline = false
  m.liveRetries = 0
  m.skipPreferredRelay = false
  m.liveProgramStop = 0
  updateLiveProgramStop()
  showBuffering("CONNECTING TO THE STUDIO", item.title)
  m.video.content = playbackContent(item, false, item.continuous = true) : m.video.visible = true : m.video.control = "play" : m.guide.visible = false : m.video.SetFocus(true)
  startPlaybackDeadline()
  showChannelBanner(item)
end sub
sub videoStateChanged()
  state = m.video.state
  m.video.disableScreenSaver = m.video.visible and not m.guide.visible and not m.ejectingMovie and (state = "playing" or state = "buffering")
  if m.guide.visible or m.ejectingMovie or m.resolvingLive or m.resolvingMovie or m.retryPending then return
  if m.diagnosticClock <> invalid
    if state = "buffering" and m.diagnosticFirst >= 0
      if m.diagnosticBuffer = invalid
        m.diagnosticBuffer = CreateObject("roTimespan")
        m.diagnosticStalls++
      end if
    else
      finishDiagnosticStall()
    end if
  end if
  if state = "playing"
    startPlaybackDeadline()
    m.inBuffer = false
  else if state = "buffering"
    startPlaybackDeadline()
    if not m.inBuffer then m.rebufferCount++
    m.inBuffer = true
    m.stableSeconds = 0
    m.waitingForVideo = true
    if m.video.content <> invalid then showBuffering("BUFFERING VIDEO", m.video.content.title)
  else if state = "finished" and m.liveItem <> invalid
    clearPlaybackDeadline()
    if m.liveItem.continuous = true
      endAt = m.liveItem.airUntil
      if endAt <> invalid and endAt > 0 and endAt <= CreateObject("roDateTime").AsSeconds()
        m.programBoundaryTimer.control = "stop"
        showBuffering("BROADCAST FINISHED", "Press Back to return to the guide.")
      else
        playbackTimedOut()
      end if
    else
      scheduleNextProgram()
    end if
  else if state = "finished" and m.activeMovie <> invalid and not m.ejectingMovie
    m.movieCompleted = true
    animateTape(false)
  else if state = "paused"
    clearPlaybackDeadline()
  else if state = "error"
    playbackTimedOut()
  end if
end sub
sub startPlaybackDeadline()
  if m.lastProgress <> invalid then return
  m.lastProgress = CreateObject("roTimespan")
  m.windowStarted = CreateObject("roTimespan")
  m.windowMedia = 0
  m.stableSeconds = 0
  m.rebufferCount = 0
  m.inBuffer = false
  m.lastPosition = m.video.position
  m.waitingForVideo = true
  m.progressTimer.control = "start"
end sub
sub clearPlaybackDeadline()
  m.progressTimer.control = "stop"
  m.lastProgress = invalid
  m.waitingForVideo = false
end sub
sub checkPlaybackProgress()
  if m.guide.visible or m.ejectingMovie or m.lastProgress = invalid then return
  if m.diagnosticClock <> invalid
    m.top.alphaHealth = {kind:m.diagnosticKind, delivery_path:m.diagnosticPath, stalls:m.diagnosticStalls,
      stall_ms:diagnosticStallTime(), first_progress_ms:m.diagnosticFirst}
  end if
  if m.lastProgress.TotalSeconds() >= 20
    playbackTimedOut()
    return
  end if
  elapsed = m.windowStarted.TotalSeconds()
  if elapsed >= 20
    if m.rebufferCount >= 2 and m.windowMedia < elapsed / 2
      playbackTimedOut()
      return
    end if
    m.windowStarted.Mark()
    m.windowMedia = 0
    m.rebufferCount = 0
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
  if m.usingPreferredRelay = true
    m.skipPreferredRelay = true
    if m.activeMovie <> invalid then restartMovie()
    if m.liveItem <> invalid then restartLive()
    return
  end if
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
  if m.video.state <> "playing" or m.guide.visible or m.ejectingMovie or m.lastProgress = invalid then return
  position = m.video.position
  delta = position - m.lastPosition
  if delta < -1 or delta > m.lastProgress.TotalSeconds() + 3
    ' Seeking or an HLS timestamp discontinuity establishes a new baseline.
    m.lastPosition = position
    m.stableSeconds = 0
    return
  end if
  if delta < 0.25 then return
  if m.diagnosticClock <> invalid
    if m.diagnosticFirst < 0 then m.diagnosticFirst = m.diagnosticClock.TotalMilliseconds()
    m.diagnosticPosition = position
    finishDiagnosticStall()
  end if
  completeAppLaunch()
  m.lastPosition = position
  m.lastProgress.Mark()
  m.windowMedia += delta
  m.stableSeconds += delta
  if m.activeMovie <> invalid
    m.moviePosition = position
    if m.movieActivityClock = invalid or m.movieActivityClock.TotalSeconds() >= 15 then refreshMovieActivity()
    if m.bookmarkCheckpoint <> invalid and m.bookmarkCheckpoint.TotalSeconds() >= 60
      saveMoviePosition(false)
      m.bookmarkCheckpoint.Mark()
    end if
  end if
  if m.stableSeconds >= 30
    m.liveRetries = 0
    m.movieRetries = 0
  end if
  if m.waitingForVideo
    m.waitingForVideo = false
    m.bufferPanel.visible = false
    if m.liveItem <> invalid then showChannelBanner(m.liveItem)
    if m.upcomingDue then showUpcomingBanner()
    if m.crtWaiting = true and m.activeMovie <> invalid
      m.crtWaiting = false
      m.crtLit = true
      m.top.findNode("crtLight").opacity = 0.65
      m.tapeTimer.duration = 0.35
      m.tapeTimer.control = "start"
    end if
  end if
end sub
sub refreshMovieActivity()
  content = m.video.content
  if content <> invalid and content.url <> invalid then sendMovieActivity(content.url)
end sub
sub releaseLiveViewer()
  address = m.top.liveDepartureUrl
  if address = "" then return
  m.top.liveDepartureUrl = ""
  m.departureTask = CreateObject("roSGNode", "MovieActivityTask")
  m.departureTask.url = address
  m.departureTask.control = "RUN"
end sub
sub sendMovieActivity(address as string)
  path = Instr(9, address, "/vault/")
  if path <= 0 then return
  id = Mid(address, path + 7, 24)
  if Len(id) <> 24 or Mid(address, path + 31, 6) <> ".m3u8?" then return
  if m.movieActivityClock = invalid then m.movieActivityClock = CreateObject("roTimespan")
  m.movieActivityClock.Mark()
  m.movieActivityTask = CreateObject("roSGNode", "MovieActivityTask")
  m.movieActivityTask.url = Left(address, path - 1) + "/api/public/origin?viewing=" + id + "&viewer=" + m.viewerLease
  m.movieActivityTask.control = "RUN"
end sub
function scheduleMovieRetry() as boolean
  if m.activeMovie = invalid or m.guide.visible or m.ejectingMovie then return false
  if m.movieRetries >= 2 then return false
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
  clearPlaybackDeadline()
  item = CreateObject("roSGNode", "ContentNode")
  item.url = m.activeMovie.url
  item.streamFormat = m.activeMovie.streamFormat
  item.title = m.activeMovie.title
  item.playStart = m.moviePosition
  m.video.content = playbackContent(item, true, false)
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
  task.fallbackUrl = m.creatorUrl
  task.onlineCatalog = true
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
  clearPlaybackDeadline()
  item = CreateObject("roSGNode", "ContentNode")
  item.url = m.liveItem.url
  item.title = m.liveItem.title
  item.streamFormat = m.liveItem.streamFormat
  item = playbackContent(item, m.liveRetries > 0, m.liveItem.continuous = true)
  m.video.control = "stop"
  m.video.content = item
  m.video.control = "play"
  startPlaybackDeadline()
end sub
function playbackContent(item as object, recovering as boolean, live as boolean) as object
  address = item.url
  m.usingPreferredRelay = false
  if m.skipPreferredRelay <> true and m.relayRoute <> invalid
    alternate = preferredRelayUrl(item.url, m.relayRoute)
    if alternate <> item.url
      address = alternate
      m.usingPreferredRelay = true
    end if
  end if
  selected = CreateObject("roSGNode", "ContentNode")
  selected.title = item.title
  selected.streamFormat = item.streamFormat
  if item.playStart <> invalid then selected.playStart = item.playStart
  selected.url = address
  if m.diagnosticClock <> invalid
    m.diagnosticPath = "direct"
    if Instr(1, address, ".lhr.life/") > 0 then m.diagnosticPath = "legacy-tunnel"
    if Left(address, Len("https://channelforge-vps.tailb58ce4.ts.net/")) = "https://channelforge-vps.tailb58ce4.ts.net/" then m.diagnosticPath = "managed-vps"
  end if
  configureHls(selected, recovering, live)
  return selected
end function
function preferredRelayUrl(url as string, pilot as string) as string
  separator = Instr(1, pilot, "=")
  if separator < 2 or separator = Len(pilot) then return url
  registered = "https://" + Left(pilot, separator - 1) + "/"
  if Left(url, Len(registered)) <> registered then return url
  path = Mid(url, Len(registered))
  if Left(path, 11) <> "/broadcast/" and Left(path, 6) <> "/live/" and Left(path, 7) <> "/vault/" then return url
  return "https://" + Mid(pilot, separator + 1) + path
end function
sub configureHls(item as object, recovering as boolean, live as boolean)
  item.HttpCertificatesFile = "common:/certs/ca-bundle.crt"
  item.live = live
  if m.diagnosticKind = "live" and m.viewerLease <> invalid
    route = CreateObject("roRegex", "^(https?://[^/?]+(?:/studio/[a-f0-9]{16})?)/(?:live/([a-zA-Z0-9_-]+)\.m3u8|broadcast/([a-zA-Z0-9_-]+)/(?:master|firetv)\.m3u8)(?:\?|$)", "")
    match = route.Match(item.url)
    if match.Count() = 4
      id = match[2]
      if id = "" then id = match[3]
      m.top.liveDepartureUrl = match[1] + "/api/public/origin?leaving=" + id + "&viewer=" + m.viewerLease
    end if
  end if
  if m.viewerLease <> invalid and (Instr(1, item.url, "/broadcast/") > 0 or Instr(1, item.url, "/live/") > 0 or Instr(1, item.url, "/vault/") > 0)
    if Instr(1, item.url, "viewer=") = 0
      separator = "?"
      if Instr(1, item.url, "?") > 0 then separator = "&"
      item.url = item.url + separator + "viewer=" + m.viewerLease
    end if
  end if
  if live or Instr(1, item.url, "/vault/") > 0
    ' Roku measures bandwidth after startup and may use every advertised variant.
    initialKibps = 800
    if recovering then initialKibps = 320
    if m.qualityKbps <> invalid and (m.qualityKbps = 400 or m.qualityKbps = 1000)
      separator = "?"
      if Instr(1, item.url, "?") > 0 then separator = "&"
      item.url += separator + "max_kbps=" + m.qualityKbps.ToStr()
      if m.qualityKbps = 400 then initialKibps = 390
    end if
    item.AdaptiveMaxStartBitrate = initialKibps
  end if
end sub
function onKeyEvent(key as string, press as boolean) as boolean
  if not press then return false
  if m.drawer <> invalid and m.drawer.visible
    if key = "back" or key = "left" then toggleDrawer() : return true
    return false
  end if
  if key = "back" and m.deepLink <> invalid then showHome() : return true
  if m.home <> invalid and m.home.visible
    hasChannels = m.homeChannels.content <> invalid and m.homeChannels.content.GetChildCount() > 0
    hasMovies = m.homeMovies.content <> invalid and m.homeMovies.content.GetChildCount() > 0
    if key = "right" and m.homeMenu.HasFocus() then m.updates.SetFocus(true) : return true
    if key = "left" and m.updates.HasFocus() then m.homeMenu.SetFocus(true) : return true
    if key = "left" and m.homeLive.HasFocus() and m.homeWatch.visible then m.homeWatch.SetFocus(true) : return true
    if key = "right" and m.homeWatch.HasFocus() then m.homeLive.SetFocus(true) : return true
    if key = "up" and (m.homeWatch.HasFocus() or m.homeLive.HasFocus()) then m.homeMenu.SetFocus(true) : return true
    if key = "down" and (m.homeMenu.HasFocus() or m.updates.HasFocus()) then m.homeLive.SetFocus(true) : return true
    if key = "up" and m.homeChannels.HasFocus() then m.homeLive.SetFocus(true) : return true
    if key = "down" and (m.homeWatch.HasFocus() or m.homeLive.HasFocus() or m.homeChannels.HasFocus())
      if hasChannels and not m.homeChannels.HasFocus()
        m.homeChannels.SetFocus(true)
      else if hasMovies
        m.homeMovies.SetFocus(true)
      else
        m.homeCinema.SetFocus(true)
      end if
      return true
    end if
    if key = "left" and m.homeCinema.HasFocus() and hasMovies then m.homeMovies.SetFocus(true) : return true
    if key = "right" and m.homeMovies.HasFocus() and hasMovies and m.homeMovies.itemFocused = m.homeMovies.content.GetChildCount() - 1 then m.homeCinema.SetFocus(true) : return true
    if key = "up" and (m.homeMovies.HasFocus() or m.homeCinema.HasFocus())
      if hasChannels then m.homeChannels.SetFocus(true) else m.homeLive.SetFocus(true)
      return true
    end if
    if key = "down" and (m.homeMovies.HasFocus() or m.homeCinema.HasFocus())
      if m.homeResumes.visible then m.homeResumes.SetFocus(true) else m.homeContinue.SetFocus(true)
      return true
    end if
    if key = "up" and (m.homeResumes.HasFocus() or m.homeContinue.HasFocus())
      if hasMovies then m.homeMovies.SetFocus(true) else m.homeCinema.SetFocus(true)
      return true
    end if
    if key = "right" and m.homeResumes.HasFocus() and m.homeResumes.itemFocused = m.homeResumes.content.GetChildCount() - 1 then m.homeContinue.SetFocus(true) : return true
    if key = "left" and m.homeContinue.HasFocus() and m.homeResumes.visible then m.homeResumes.SetFocus(true) : return true
    return false
  end if
  if m.guide.visible and m.continuePage.visible
    if key = "back" then showHome() : return true
    if key = "up" and m.homeMenu.HasFocus() = false and m.continueMovies.itemFocused <= 0 then m.homeMenu.SetFocus(true) : return true
    if key = "right" and m.homeMenu.HasFocus() then m.updates.SetFocus(true) : return true
    if key = "left" and m.updates.HasFocus() then m.homeMenu.SetFocus(true) : return true
    if key = "down" and m.homeMenu.HasFocus() then m.continueMovies.SetFocus(true) : return true
    return false
  end if
  if m.guide.visible = false
    if key = "options" and not m.ejectingMovie then showPlaybackQuality() : return true
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
    if key = "OK" and m.liveItem <> invalid
      showLivePlaybackControls()
      return true
    end if
    if key = "back"
      if m.activeMovie <> invalid
        finishDiagnosticStall()
        if not m.ejectingMovie then animateTape(false)
        return true
      end if
      returnFromLivePlayback()
      return true
    end if
    if m.liveItem <> invalid
      if key = "up" then playChannel(m.channels.itemFocused + 1) : return true
      if key = "down" then playChannel(m.channels.itemFocused - 1) : return true
    end if
  end if
  if key = "back" and m.guide.visible and m.home <> invalid then showHome() : return true
  if m.guide.visible and m.libraryPresentation <> invalid and m.libraryPresentation.visible
    if key = "right" and m.homeMenu.HasFocus() then m.updates.SetFocus(true) : return true
    if key = "left" and m.updates.HasFocus() then m.homeMenu.SetFocus(true) : return true
    if key = "down" and m.updates.HasFocus() then m.vault.SetFocus(true) : return true
    if key = "up" and m.vault.HasFocus() and m.vault.itemFocused <= 0 then m.updates.SetFocus(true) : return true
    if key = "down" and m.homeMenu.HasFocus() then m.creators.SetFocus(true) : return true
    if key = "up" and m.creators.HasFocus() and m.creators.itemFocused <= 0 then m.homeMenu.SetFocus(true) : return true
    if key = "right" and m.creators.HasFocus() then m.vault.SetFocus(true) : return true
    if key = "left" and m.vault.HasFocus() then m.creators.SetFocus(true) : return true
  end if
  if key = "right" and m.guide.visible and m.homeMenu.HasFocus() then m.guideSchedule.SetFocus(true) : return true
  if key = "left" and m.guide.visible and m.guideSchedule.HasFocus() then m.homeMenu.SetFocus(true) : return true
  if key = "right" and m.guide.visible and m.guideSchedule.HasFocus() then m.guideSort.SetFocus(true) : return true
  if key = "left" and m.guide.visible and m.guideSort.HasFocus() then m.guideSchedule.SetFocus(true) : return true
  if key = "right" and m.guide.visible and m.guideSort.HasFocus() then m.updates.SetFocus(true) : return true
  if key = "left" and m.guide.visible and m.updates.HasFocus() then m.guideSort.SetFocus(true) : return true
  if m.guide.visible and m.insertMovie.HasFocus() and key = "left" then m.vault.SetFocus(true) : return true
  if key = "right" and m.guide.visible and m.vault.HasFocus() and m.selectedMovie <> invalid then m.insertMovie.SetFocus(true) : return true
  if m.guide.visible and m.moreMovies.HasFocus() and key = "up" then m.vault.SetFocus(true) : return true
  if m.guide.visible and m.vault.HasFocus() and key = "down" and m.moreMovies.visible
    if m.vault.itemFocused = m.vault.content.GetChildCount() - 1 then m.moreMovies.SetFocus(true) : return true
  end if
  if key = "left" and m.guide.visible and not m.vault.HasFocus() then m.creators.SetFocus(true) : return true
  if key = "right" and m.guide.visible and not m.vault.HasFocus() then m.channels.SetFocus(true) : return true
  if key = "up" and m.guide.visible and m.creators.HasFocus() and m.creators.itemFocused <= 0 then m.homeMenu.SetFocus(true) : return true
  if key = "up" and m.guide.visible and m.channels.HasFocus() and m.channels.itemFocused <= 0 then m.guideSort.SetFocus(true) : return true
  if key = "down" and m.guide.visible and (m.updates.HasFocus() or m.guideSort.HasFocus() or m.guideSchedule.HasFocus()) then m.channels.SetFocus(true) : return true
  if key = "down" and m.guide.visible and m.homeMenu.HasFocus() then m.creators.SetFocus(true) : return true
  return false
end function
