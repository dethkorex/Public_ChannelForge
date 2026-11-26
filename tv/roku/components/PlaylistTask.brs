sub init()
  m.top.functionName = "load"
end sub
sub load()
  transfer = CreateObject("roUrlTransfer")
  transfer.SetCertificatesFile("common:/certs/ca-bundle.crt")
  transfer.InitClientCertificates()
  transfer.EnableEncodings(true)
  transfer.SetMinimumTransferRate(128, 6)
  transfer.SetUrl(m.top.url)
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
      cached = ReadAsciiFile(cache)
      if cached <> "" then catalog = ParseJson(cached)
      if type(catalog) <> "roAssociativeArray" or catalog.source <> m.top.url then catalog = invalid
      m.top.stale = true
    end if
    if type(catalog) = "roAssociativeArray" and type(catalog.channels) = "roArray"
      for each channel in catalog.channels
        if type(channel) = "roAssociativeArray" and channel.stream_url <> invalid and channel.stream_url <> ""
          node = root.CreateChild("ContentNode")
          node.AddFields({channelId: "", availability: "standby", programs: [], continuous: false})
          node.AddField("airUntil", "double", false)
          if channel.air_until_epoch_ms <> invalid then node.airUntil = channel.air_until_epoch_ms / 1000#
          if channel.id <> invalid then node.channelId = channel.id
          if channel.state <> invalid then node.availability = channel.state
          if channel.name <> invalid then node.title = channel.name
          if m.top.stale
            node.title = node.title + " (reconnecting)"
            node.availability = "reconnecting"
          else if node.availability = "offline" or (channel.online = false and channel.state = invalid)
            node.title = node.title + " (offline)"
            node.availability = "offline"
          end if
          if channel.number <> invalid then node.shortDescriptionLine1 = channel.number.ToStr()
          if channel.logo_url <> invalid then node.hdPosterUrl = channel.logo_url
          if type(channel.programs) = "roArray" then node.programs = channel.programs
          node.url = channel.stream_url
          if channel.continuous_url <> invalid and channel.continuous_url <> ""
            node.url = channel.continuous_url
            node.continuous = true
          end if
          node.streamFormat = "hls"
        end if
      end for
    end if
    m.top.content = root
    return
  end if
  lines = payload.Tokenize(chr(10))
  m.top.catalogAvailable = Left(payload.Trim(), 7) = "#EXTM3U"
  m.top.stale = not m.top.catalogAvailable
  pending = ""
  number = 1
  for each raw in lines
    line = raw.Trim()
    if left(line, 8) = "#EXTINF:"
      comma = Instr(1, line, ",")
      if comma > 0 then pending = Mid(line, comma + 1).Trim()
    else if line <> "" and left(line, 1) <> "#" and pending <> ""
      node = root.CreateChild("ContentNode")
      node.title = pending
      node.shortDescriptionLine1 = number.ToStr()
      node.AddField("programs", "array", false)
      node.url = line
      node.streamFormat = "hls"
      pending = ""
      number++
    end if
  end for
  m.top.content = root
end sub
