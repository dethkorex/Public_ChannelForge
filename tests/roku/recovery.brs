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
  m.progressTimer = {} : m.playbackDeadline = {} : m.liveRetryTimer = {}
  m.programBoundaryTimer = {} : m.upcomingTimer = {}
  m.ejectingMovie = false : m.resolvingMovie = false : m.resolvingLive = false
  m.retryPending = false : m.waitingForVideo = false
  m.activeMovie = {title: "QA"}
  m.movieRetries = 0 : m.moviePosition = 15 : m.movieRetryPosition = 0
  videoStateChanged()
  verify(m.waitingForVideo and m.playbackDeadline.control = "start", "post-start buffering arms a deadline")
  m.playbackDeadline.control = "pending"
  videoStateChanged()
  verify(m.playbackDeadline.control = "pending", "repeated buffering cannot extend deadline")
  playbackTimedOut()
  verify(m.retryPending and m.movieRetries = 1 and m.movieRetryPosition = 15, "retry keeps last movie position")
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
  m.resolvingLive = true
  guide.rows = []
  onAirGuideLoaded(eventB)
  verify(m.bufferTitle.text = "THIS CHANNEL IS OFFLINE", "confirmed unpublish stops reconnect")
  print "PASS Roku discovery and recovery"
end sub

sub verify(ok as boolean, label as string)
  if not ok
    print "FAILED: "; label
    stop
  end if
end sub
