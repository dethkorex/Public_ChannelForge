sub init()
  m.top.functionName = "load"
end sub
sub load()
  root = CreateObject("roSGNode", "ContentNode")
  m.top.nextOffset = -1
  if m.top.url = "" then m.top.loaded = true : m.top.content = root : return
  joiner = "?"
  if Instr(1, m.top.url, "?") > 0 then joiner = "&"
  transfer = CreateObject("roUrlTransfer") : transfer.SetCertificatesFile("common:/certs/ca-bundle.crt") : transfer.InitClientCertificates() : transfer.SetUrl(m.top.url + joiner + "limit=24&offset=" + m.top.offset.ToStr())
  transfer.EnableEncodings(true)
  transfer.SetMinimumTransferRate(128, 10)
  payload = ParseJson(transfer.GetToString())
  if payload <> invalid and payload.items <> invalid
    m.top.loaded = true
    if payload.next_offset <> invalid and payload.next_offset > m.top.offset then m.top.nextOffset = payload.next_offset
    seen = {}
    match = CreateObject("roRegex", "[^[:alnum:]]+", "")
    for each item in payload.items
      year = ""
      if item.year <> invalid then year = item.year.ToStr()
      key = match.ReplaceAll(LCase(item.title), " ").Trim() + "|" + year
      if item.type = "movie" and not seen.DoesExist(key)
        seen[key] = true
        node = root.CreateChild("ContentNode") : node.title = item.title : node.description = item.summary : node.hdPosterUrl = item.poster_url : node.url = item.stream_url : node.streamFormat = "hls"
        if item.year <> invalid then node.shortDescriptionLine1 = item.year.ToStr()
      end if
    end for
  end if
  m.top.content = root
end sub
