function parseDeepLink(args as dynamic) as dynamic
  if type(args) <> "roAssociativeArray" then return invalid
  kind = invalid : content = invalid
  for each key in args
    if LCase(key) = "mediatype" then kind = args[key]
    if LCase(key) = "contentid" then content = args[key]
  end for
  if GetInterface(kind, "ifString") = invalid or GetInterface(content, "ifString") = invalid then return invalid
  kind = LCase(kind)
  if kind = "livefeed"
    pattern = "^([a-zA-Z0-9_-]{1,80}):([a-f0-9]{16})$"
  else if kind = "movie"
    pattern = "^([a-zA-Z0-9_-]{1,80}):([a-f0-9]{24})$"
  else
    return invalid
  end if
  match = CreateObject("roRegex", pattern, "").Match(content)
  if match.Count() <> 3 then return invalid
  return {kind: kind, studioId: match[1], itemId: match[2], phase: "directory"}
end function

sub completeAppLaunch()
  if m.launchComplete <> false then return
  m.launchComplete = true
  m.top.signalBeacon("AppLaunchComplete")
end sub

sub cancelDeepLink()
  if m.deepLinkTimer <> invalid then m.deepLinkTimer.control = "stop"
  cancelContentTask(m.deepLinkMovieTask)
  m.deepLinkMovieTask = invalid
  m.deepLink = invalid
end sub

sub deepLinkRequested(event as object)
  target = parseDeepLink(event.GetData())
  if m.activeMovie <> invalid then saveMoviePosition(m.movieCompleted = true)
  if not m.guide.visible then closeRemovedStudio()
  cancelDeepLink()
  if target = invalid
    if not m.home.visible then showHome()
    completeAppLaunch()
    return
  end if
  m.deepLink = target
  m.deepLinkTimer.control = "start"
  if not openLinkedStudio(m.creators.content) then requestCatalog()
end sub

function linkedEntryIndex(content as dynamic, field as string, id as string) as integer
  if content = invalid then return -1
  for index = 0 to content.GetChildCount() - 1
    if content.GetChild(index).GetField(field) = id then return index
  end for
  return -1
end function

function openLinkedStudio(studios as dynamic) as boolean
  target = m.deepLink
  index = linkedEntryIndex(studios, "studioId", target.studioId)
  if index < 0 then return false
  target.phase = "content"
  m.home.visible = false
  m.continuePage.visible = false
  m.creators.jumpToItem = index
  loadCreator(index)
  if target.kind = "movie"
    task = CreateObject("roSGNode", "VaultTask")
    task.url = studios.GetChild(index).shortDescriptionLine2
    task.movieId = target.itemId
    task.observeField("content", "linkedMovieLoaded")
    m.deepLinkMovieTask = task
    task.control = "run"
  end if
  return true
end function

sub playLinkedChannel()
  target = m.deepLink
  if target = invalid or target.kind <> "livefeed" or target.studioId <> m.creatorId then return
  index = linkedEntryIndex(m.channels.content, "channelId", target.itemId)
  if index < 0 or m.channels.content.GetChild(index).availability = "offline"
    showHome()
    return
  end if
  cancelDeepLink()
  playChannel(index)
end sub

sub linkedMovieLoaded(event as object)
  if not currentTask(event, m.deepLinkMovieTask) or m.deepLink = invalid then return
  content = event.GetData()
  movie = content.GetChild(0)
  if movie = invalid
    showHome()
    return
  end if
  studioId = m.deepLink.studioId
  position = resumePositionFor(studioId, movie.movieKey)
  cancelDeepLink()
  startCinemaMovie(movie, position, studioId, true)
end sub

sub deepLinkTimedOut()
  if m.deepLink <> invalid then showHome()
end sub
