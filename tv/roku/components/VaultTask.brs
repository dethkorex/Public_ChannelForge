sub init()
  m.top.functionName = "load"
end sub
sub load()
  root = CreateObject("roSGNode", "ContentNode")
  m.top.nextOffset = -1
  if m.top.url = "" then m.top.loaded = true : m.top.content = root : return
  joiner = "?"
  if Instr(1, m.top.url, "?") > 0 then joiner = "&"
  query = "limit=24&offset=" + m.top.offset.ToStr()
  if m.top.movieId <> "" then query = "limit=1&id=" + m.top.movieId
  transfer = CreateObject("roUrlTransfer") : transfer.SetCertificatesFile("common:/certs/ca-bundle.crt") : transfer.InitClientCertificates() : transfer.SetUrl(m.top.url + joiner + query)
  transfer.EnableEncodings(true)
  transfer.SetMinimumTransferRate(128, 10)
  port = CreateObject("roMessagePort")
  transfer.SetMessagePort(port)
  payload = invalid
  if transfer.AsyncGetToString()
    response = wait(8000, port)
    if response <> invalid and response.GetResponseCode() = 200
      payload = ParseJson(response.GetString())
    else
      transfer.AsyncCancel()
    end if
  end if
  if type(payload) = "roAssociativeArray" and type(payload.items) = "roArray"
    m.top.loaded = true
    if payload.next_offset <> invalid and payload.next_offset > m.top.offset then m.top.nextOffset = payload.next_offset
    seen = {}
    match = CreateObject("roRegex", "[^[:alnum:]]+", "")
    for each item in payload.items
      if type(item) = "roAssociativeArray" and (m.top.movieId = "" or item.id = m.top.movieId)
        appendMovie(root, seen, match, item)
      end if
    end for
  end if
  m.top.content = root
end sub
sub appendMovie(root as object, seen as object, match as object, item as dynamic)
  if type(item) <> "roAssociativeArray" or GetInterface(item.title, "ifString") = invalid then return
  if item.type <> "movie" or item.stream_url = invalid or item.stream_url = "" then return
  year = ""
  if item.year <> invalid then year = item.year.ToStr()
  key = match.ReplaceAll(LCase(item.title), " ").Trim() + "|" + year
  if seen.DoesExist(key) then return
  seen[key] = true
  node = root.CreateChild("ContentNode")
  node.AddFields({movieKey: key})
  node.title = item.title
  node.url = item.stream_url
  node.streamFormat = "hls"
  if item.summary <> invalid then node.description = item.summary
  if item.poster_url <> invalid then node.hdPosterUrl = item.poster_url
  node.shortDescriptionLine1 = year
end sub
