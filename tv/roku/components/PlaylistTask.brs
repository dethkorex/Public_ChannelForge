sub init()
  m.top.functionName = "load"
end sub
function readPlaylist(address as string) as string
  transfer = CreateObject("roUrlTransfer")
  transfer.SetCertificatesFile("common:/certs/ca-bundle.crt")
  transfer.InitClientCertificates()
  transfer.EnableEncodings(true)
  transfer.SetMinimumTransferRate(128, 6)
  transfer.SetUrl(address)
  port = CreateObject("roMessagePort")
  transfer.SetMessagePort(port)
  payload = ""
  if transfer.AsyncGetToString()
    event = wait(7000, port)
    if event <> invalid and event.GetResponseCode() = 200
      payload = event.GetString()
    else
      transfer.AsyncCancel()
    end if
  end if
  return payload
end function
sub load()
  payload = readPlaylist(m.top.url)
  root = CreateObject("roSGNode", "ContentNode")
  if m.top.onlineCatalog
    catalog = invalid
    if payload <> "" then catalog = ParseJson(payload)
    identity = m.top.studioId
    if identity = "" then identity = m.top.url
    digest = CreateObject("roEVPDigest")
    digest.Setup("sha256")
    bytes = CreateObject("roByteArray")
    bytes.FromAsciiString(identity)
    cache = "cachefs:/channelforge-guide-" + digest.Process(bytes) + ".json"
    if type(catalog) = "roAssociativeArray" and type(catalog.channels) = "roArray"
      m.top.catalogAvailable = true
      if Len(payload) < 524288
        WriteAsciiFile(cache, FormatJson({source: m.top.url, channels: catalog.channels}))
      else
        DeleteFile(cache)
      end if
    else
      if m.top.fallbackUrl <> ""
        playlist = readPlaylist(m.top.fallbackUrl)
        if Left(playlist.Trim(), 7) = "#EXTM3U"
          appendPlaylist(root, playlist)
          if root.GetChildCount() > 0
            m.top.content = root
            return
          end if
        end if
      end if
      cached = ReadAsciiFile(cache)
      if cached <> "" then catalog = ParseJson(cached)
      if type(catalog) <> "roAssociativeArray" or (m.top.studioId = "" and catalog.source <> m.top.url) then catalog = invalid
      m.top.stale = true
    end if
    if type(catalog) = "roAssociativeArray" and type(catalog.channels) = "roArray"
      seen = {}
      for each channel in catalog.channels
        if type(channel) = "roAssociativeArray" and channel.stream_url <> invalid and channel.stream_url <> "" and channel.state <> "offline" and (channel.online <> false or channel.state <> invalid)
          identity = "url:" + channel.stream_url
          if type(channel.id) = "roString" or type(channel.id) = "String"
            if channel.id <> "" then identity = "id:" + channel.id
          end if
          if not seen.DoesExist(identity)
            seen[identity] = true
            node = root.CreateChild("ContentNode")
            node.AddFields({channelId: "", availability: "standby", programs: [], continuous: false, channelRating: "", ratingRank: 10})
            node.AddField("airUntil", "double", false)
            if channel.air_until_epoch_ms <> invalid then node.airUntil = channel.air_until_epoch_ms / 1000#
            if channel.id <> invalid then node.channelId = channel.id
            if channel.state <> invalid then node.availability = channel.state
            if channel.name <> invalid then node.title = channel.name
            if m.top.stale
              node.title = node.title + " (reconnecting)"
              node.availability = "reconnecting"
            end if
            if channel.number <> invalid then node.shortDescriptionLine1 = channel.number.ToStr()
            if channel.channel_rating <> invalid then node.channelRating = channel.channel_rating
            if channel.channel_rating_rank <> invalid and channel.channel_rating_rank >= 1 and channel.channel_rating_rank <= 9 then node.ratingRank = channel.channel_rating_rank
            if channel.logo_url <> invalid then node.hdPosterUrl = channel.logo_url
            if type(channel.programs) = "roArray" then node.programs = channel.programs
            node.url = channel.stream_url
            if channel.continuous_url <> invalid and channel.continuous_url <> ""
              node.url = channel.continuous_url
              node.continuous = true
            end if
            node.streamFormat = "hls"
          end if
        end if
      end for
    end if
    m.top.content = root
    return
  end if
  m.top.catalogAvailable = Left(payload.Trim(), 7) = "#EXTM3U"
  m.top.stale = not m.top.catalogAvailable
  if m.top.catalogAvailable then appendPlaylist(root, payload)
  m.top.content = root
end sub
sub appendPlaylist(root as object, payload as string)
  lines = payload.Tokenize(chr(10))
  pending = ""
  number = 1
  seen = {}
  identity = CreateObject("roRegex", "tvg-id=" + Chr(34) + "public-channelforge\.([^" + Chr(34) + "]+)", "")
  channelNumber = CreateObject("roRegex", "tvg-chno=" + Chr(34) + "([^" + Chr(34) + "]+)", "")
  channelRating = CreateObject("roRegex", "tvg-rating=" + Chr(34) + "([A-Z0-9-]+)" + Chr(34), "")
  ratingRank = CreateObject("roRegex", "tvg-rating-rank=" + Chr(34) + "([1-9])" + Chr(34), "")
  for each raw in lines
    line = raw.Trim()
    if left(line, 8) = "#EXTINF:"
      comma = Instr(1, line, ",")
      if comma > 0 then pending = Mid(line, comma + 1).Trim()
      matchedId = identity.Match(line)
      matchedNumber = channelNumber.Match(line)
      matchedRating = channelRating.Match(line)
      matchedRank = ratingRank.Match(line)
    else if line <> "" and left(line, 1) <> "#" and pending <> ""
      key = "url:" + line
      if matchedId.Count() > 1 then key = "id:" + matchedId[1]
      if not seen.DoesExist(key)
        seen[key] = true
        node = root.CreateChild("ContentNode")
        node.title = pending
        node.AddFields({channelId: "", availability: "standby", programs: [], continuous: false, channelRating: "", ratingRank: 10})
        node.shortDescriptionLine1 = number.ToStr()
        if matchedId.Count() > 1 then node.channelId = matchedId[1]
        if matchedNumber.Count() > 1 then node.shortDescriptionLine1 = matchedNumber[1]
        if matchedRating.Count() > 1 then node.channelRating = matchedRating[1]
        if matchedRank.Count() > 1 then node.ratingRank = Int(Val(matchedRank[1]))
        node.url = line
        node.streamFormat = "hls"
        number++
      end if
      pending = ""
    end if
  end for
end sub
