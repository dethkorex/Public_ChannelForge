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
  payload = transfer.GetToString()
  root = CreateObject("roSGNode", "ContentNode")
  if m.top.onlineCatalog
    catalog = ParseJson(payload)
    if catalog <> invalid and catalog.channels <> invalid
      for each channel in catalog.channels
        if channel.online = true
          node = root.CreateChild("ContentNode")
          node.title = channel.name
          node.shortDescriptionLine1 = channel.number
          node.url = channel.stream_url
          node.streamFormat = "hls"
        end if
      end for
    end if
    m.top.content = root
    return
  end if
  lines = payload.Tokenize(chr(10))
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
      node.url = line
      node.streamFormat = "hls"
      pending = ""
      number++
    end if
  end for
  m.top.content = root
end sub
