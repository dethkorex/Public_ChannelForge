' Off-device regression tests for the catalog and player callbacks.
sub main()
  a = {id: "alpha", playlist_url: "https://old.example/channels"}
  b = {id: "beta", playlist_url: "https://second.example/channels"}
  changed = {id: "alpha", playlist_url: "https://new.example/channels"}
  c = {id: "charlie", playlist_url: "https://third.example/channels"}
  cached = [a, b]
  fresh = [c, changed]
  merged = mergeStudioRows(cached, fresh, false)
  verify(merged.Count() = 3, "partial result keeps the missing studio and includes new studios")
  verify(merged[1].playlist_url = changed.playlist_url, "stable id accepts new public endpoint")
  verify(mergeStudioRows(cached, [], false).Count() = 2, "empty partial preserves cache")
  verify(mergeStudioRows(cached, [], true).Count() = 0, "complete empty removes registered studios")
  verify(mergeStudioRows(cached, fresh, true).Count() = 2, "complete snapshot removes individual unpublished studio")
  legacy = {playlist_url: changed.playlist_url}
  verify(mergeStudioRows([legacy], [changed], false).Count() = 1, "legacy URL matches upgraded registration")
  verify(mergeStudioRows(cached, [invalid, {}, {id: "bad"}], false).Count() = 2, "malformed rows preserve cached guide")

  movie = parseDeepLink({ContentID: "alpha:0123456789abcdef01234567", MediaType: "Movie"})
  verify(movie.kind = "movie" and movie.studioId = "alpha" and movie.itemId = "0123456789abcdef01234567", "movie link uses case-insensitive Roku input")
  live = parseDeepLink({contentId: "alpha:0123456789abcdef", mediaType: "livefeed"})
  verify(live.kind = "livefeed" and live.itemId = "0123456789abcdef", "live link resolves stable channel ID")
  verify(parseDeepLink({contentId: "https://example.test/live", mediaType: "livefeed"}) = invalid, "deep links cannot supply stream URLs")
  verify(parseDeepLink({contentId: "alpha:0123456789abcdef", mediaType: "movie"}) = invalid, "movie link rejects channel ID")
  verify(parseDeepLink({contentId: "alpha:0123456789abcdef", mediaType: "unknown"}) = invalid, "unsupported deep link returns home")
  verify(parseDeepLink({}) = invalid, "regular launch takes the home route")

  bookmarks = {
    entries: {},
    Read: function(key as string) as string
      if m.entries.DoesExist(key) then return m.entries[key]
      return ""
    end function,
    Write: function(key as string, value as string) as boolean
      if not m.entries.DoesExist(key) and m.entries.Count() >= 16 then return false
      m.entries[key] = value
      return true
    end function,
    Delete: function(key as string) as boolean
      return m.entries.Delete(key)
    end function,
    GetKeyList: function() as object
      return m.entries.Keys()
    end function,
    Flush: function() as boolean
      return true
    end function
  }
  bookmarkKey = movieBookmarkKey("alpha", "film|2000")
  verify(movieResumePosition(bookmarks, bookmarkKey, [{key: bookmarkKey, position: 35.5}]) = 35.5, "old shelf bookmarks still resume")
  writeMovieBookmark(bookmarks, bookmarkKey, 81.5, false, 1000)
  for index = 1 to 15
    writeMovieBookmark(bookmarks, movieBookmarkKey("alpha", "next" + index.ToStr()), 12.0, false, 2000)
  end for
  verify(movieResumePosition(bookmarks, bookmarkKey, []) = 81.5, "shelf eviction does not erase the bookmark")
  verify(movieResumePosition(bookmarks, movieBookmarkKey("beta", "film|2000"), []) = 0, "matching title at a different studio has its own bookmark")
  writeMovieBookmark(bookmarks, "too soon", 13.0, false, 1000 + 30 * 24 * 60 * 60)
  verify(movieResumePosition(bookmarks, bookmarkKey, []) = 81.5, "a full registry preserves 30 days of bookmarks")
  writeMovieBookmark(bookmarks, "new film", 16.0, false, 1001 + 30 * 24 * 60 * 60)
  verify(movieResumePosition(bookmarks, "new film", []) = 16, "a full registry can reclaim an expired bookmark")
  writeMovieBookmark(bookmarks, "new film", 16.0, true, 1002 + 30 * 24 * 60 * 60)
  verify(movieResumePosition(bookmarks, "new film", []) = 0, "a completed movie starts over")

  m.guide = {visible: false}
  m.video = {state: "buffering", content: {title: "QA"}, position: 15}
  m.bufferTitle = {} : m.bufferDetails = {} : m.bufferPanel = {}
  m.progressTimer = {} : m.liveRetryTimer = {}
  m.programBoundaryTimer = {} : m.upcomingTimer = {}
  m.ejectingMovie = false : m.resolvingMovie = false : m.resolvingLive = false
  m.retryPending = false : m.waitingForVideo = false
  m.activeMovie = {title: "QA"}
  m.movieRetries = 0 : m.moviePosition = 15
  videoStateChanged()
  verify(m.waitingForVideo and m.progressTimer.control = "start", "post-start buffering arms a deadline")
  m.lastProgress = fakeClock(21)
  videoStateChanged()
  m.video.state = "playing"
  videoStateChanged()
  verify(m.lastProgress.TotalSeconds() = 21 and m.waitingForVideo, "alternating native state alone cannot extend deadline")
  checkPlaybackProgress()
  verify(m.retryPending and m.movieRetries = 1 and m.moviePosition = 15, "retry keeps last movie position")
  videoStateChanged()
  verify(m.movieRetries = 1, "late state event does not spend another retry")
  m.retryPending = false
  playbackTimedOut()
  verify(m.movieRetries = 2 and m.liveRetryTimer.duration = 4, "two retries with backoff")
  m.retryPending = false
  playbackTimedOut()
  verify(m.liveRetryTimer.control = "stop" and not m.retryPending, "exhausted retries stop")
  m.video.state = "paused"
  videoStateChanged()
  verify(not m.waitingForVideo and m.progressTimer.control = "stop", "pause cannot trigger stall recovery")
  m.video.state = "playing" : m.video.position = 12 : m.moviePosition = 12
  videoStateChanged()
  m.lastProgress = fakeClock(18)
  m.video.position = 11.8 : trackPlaybackPosition()
  m.video.position = 12 : trackPlaybackPosition()
  verify(m.waitingForVideo and m.lastProgress.TotalSeconds() = 18, "backward jitter does not renew the watchdog")
  m.video.position = 45 : trackPlaybackPosition()
  verify(m.waitingForVideo and m.movieRetries = 2 and m.moviePosition = 12, "seek jump alone cannot clear retries")
  m.video.position = 45.5 : trackPlaybackPosition()
  verify(not m.waitingForVideo and m.moviePosition = 45.5 and m.lastProgress.TotalSeconds() = 0, "forward progress confirms recovery")
  for tick = 1 to 60
    m.video.position += 0.5
    trackPlaybackPosition()
  end for
  verify(m.movieRetries = 0, "30 seconds of sustained playback restores automatic retry budget")
  m.video.state = "buffering" : videoStateChanged()
  m.video.state = "playing" : videoStateChanged()
  m.video.position += 0.5 : trackPlaybackPosition()
  m.video.state = "buffering" : videoStateChanged()
  m.windowStarted = fakeClock(20) : m.windowMedia = 1 : m.lastProgress = fakeClock(2)
  checkPlaybackProgress()
  verify(m.movieRetries = 1 and m.retryPending, "frequent stalls with brief advances lower startup quality on retry")
  m.retryPending = false
  clearPlaybackDeadline()
  quality = {url: "https://example.test/vault/a.m3u8"}
  configureHls(quality, false, false)
  verify(quality.AdaptiveMaxStartBitrate = 800 and quality.MaxBandwidth = invalid, "movie permits upward adaptation after startup")
  configureHls(quality, true, true)
  verify(quality.AdaptiveMaxStartBitrate = 320 and quality.live, "live recovery uses a conservative startup hint")
  m.video.state = "playing"
  elapsed = {TotalSeconds: function() : return 21 : end function}
  m.lastProgress = elapsed
  m.liveItem = {title: "Live QA"}
  m.liveRetries = 0 : m.activeMovie = invalid
  checkPlaybackProgress()
  verify(m.liveRetries = 1 and m.retryPending, "silent stall while playing triggers a bounded live retry")
  m.retryPending = false : m.guide.visible = true
  checkPlaybackProgress()
  verify(m.liveRetries = 1 and not m.retryPending, "closed player ignores watchdog")
  m.guide.visible = false : m.video.state = "finished"
  m.liveItem.continuous = true : m.liveItem.airUntil = 1
  videoStateChanged()
  verify(m.bufferTitle.text = "BROADCAST FINISHED" and not m.retryPending, "scheduled sign-off ends live playback")
  m.liveItem.airUntil = 0
  videoStateChanged()
  verify(m.retryPending and m.liveRetries = 2, "unexpected end of continuous HLS reconnects")
  m.retryPending = false
  studioA = {key: "a", IsSameNode: function(other) : return m.key = other.key : end function}
  studioB = {key: "b", IsSameNode: studioA.IsSameNode}
  eventA = {node: studioA, GetRoSGNode: function() : return m.node : end function}
  eventB = {node: studioB, GetRoSGNode: eventA.GetRoSGNode}
  m.vaultTask = studioB : m.playlistTask = studioB : m.onAirGuideTask = studioB
  m.vault = {content: "B movies"} : m.channels = {content: "B channels"}
  vaultLoaded(eventA)
  playlistLoaded(eventA)
  verify(m.vault.content = "B movies" and m.channels.content = "B channels", "late responses cannot overwrite selected studio")
  m.guide.visible = false : m.resolvingLive = true : m.checkingOffline = false
  m.liveItem = {channelId: "ch2", title: "After School", url: "https://old.example/live/ch2"}
  onAirGuideLoaded(eventA)
  verify(m.resolvingLive, "late recovery response is ignored")
  channel = {channelId: "ch2", title: "After School", url: "https://new.example/live/ch2", streamFormat: "hls", continuous: true}
  guide = {rows: [channel], GetChildCount: function() : return m.rows.Count() : end function, GetChild: function(index) : return m.rows[index] : end function}
  studioB.content = guide : studioB.catalogAvailable = true
  m.video.state = "stopped"
  onAirGuideLoaded(eventB)
  verify(m.video.control = "play" and m.video.content.url = channel.url, "reconnects using channel ID on rotated route")
  verify(m.video.content.AdaptiveMaxStartBitrate = 320, "retuned channel keeps conservative startup on its refreshed route")
  m.resolvingLive = true
  guide.rows = []
  onAirGuideLoaded(eventB)
  verify(m.bufferTitle.text = "THIS CHANNEL IS OFFLINE", "confirmed unpublish stops reconnect")
  pilot = "registered.example.org=relay.example.org"
  stream = "https://registered.example.org/broadcast/alpha/master.m3u8?viewer=abc"
  verify(preferredRelayUrl(stream, pilot) = "https://relay.example.org/broadcast/alpha/master.m3u8?viewer=abc", "relay preserves media path and viewer lease")
  verify(preferredRelayUrl("https://registered.example.org/exports/guide.xml", pilot) = "https://registered.example.org/exports/guide.xml", "relay leaves guide on registered route")
  verify(preferredRelayUrl(stream, "other.example.org=relay.example.org") = stream, "relay stays scoped to the configured studio")
  m.home = {visible: false}
  m.guide.visible = true
  m.continuePage = {visible: false}
  m.vault = focusTarget(true)
  m.insertMovie = focusTarget(false)
  m.moreMovies = focusTarget(false)
  m.homeMenu = focusTarget(false)
  m.guideSchedule = focusTarget(false)
  m.guideSort = focusTarget(false)
  m.channels = focusTarget(false)
  m.channels.itemFocused = 0
  m.creators = focusTarget(false)
  m.updates = focusTarget(false)
  m.selectedMovie = {title: "QA"}
  verify(onKeyEvent("right", true) and m.insertMovie.focused, "Right from a selected tape reaches Insert Movie")
  m.vault.focused = false
  verify(onKeyEvent("left", true) and m.vault.focused, "Left from Insert Movie returns to the shelf")
  m.insertMovie.focused = false
  m.vault.focused = false
  m.channels.focused = true
  verify(onKeyEvent("up", true) and m.guideSort.focused, "Up from the first channel reaches guide sorting")
  m.channels.focused = false
  verify(onKeyEvent("right", true) and m.updates.focused, "Right from guide sorting reaches System")
  m.guideSort.focused = true
  verify(onKeyEvent("left", true) and m.guideSchedule.focused, "Left from guide sorting reaches channel schedule")
  m.home.visible = true
  m.updates.focused = false
  focusSystemButton()
  verify(m.updates.focused, "Closing System on Home restores focus to the shared header control")
  print "PASS Roku discovery and recovery"
end sub

function focusTarget(focused as boolean) as object
  return {focused: focused, HasFocus: function() : return m.focused : end function, SetFocus: sub(value as boolean) : m.focused = value : end sub}
end function

function fakeClock(elapsed as integer) as object
  return {elapsed: elapsed, TotalSeconds: function() : return m.elapsed : end function, Mark: sub() : m.elapsed = 0 : end sub}
end function

sub verify(ok as boolean, label as string)
  if not ok
    print "FAILED: "; label
    stop
  end if
end sub
