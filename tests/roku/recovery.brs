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
  print "PASS Roku discovery and recovery"
end sub

function fakeClock(elapsed as integer) as object
  return {elapsed: elapsed, TotalSeconds: function() : return m.elapsed : end function, Mark: sub() : m.elapsed = 0 : end sub}
end function

sub verify(ok as boolean, label as string)
  if not ok
    print "FAILED: "; label
    stop
  end if
end sub
