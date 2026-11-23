sub init()
  m.top.functionName = "load"
end sub
sub load()
  root = CreateObject("roSGNode", "ContentNode")
  if m.top.url = "" then m.top.content = root : return
  transfer = CreateObject("roUrlTransfer") : transfer.SetCertificatesFile("common:/certs/ca-bundle.crt") : transfer.InitClientCertificates() : transfer.SetUrl(m.top.url)
  transfer.EnableEncodings(true)
  transfer.SetMinimumTransferRate(128, 10)
  payload = ParseJson(transfer.GetToString())
  if payload <> invalid and payload.items <> invalid
    for each item in payload.items
      if item.type = "movie"
        node = root.CreateChild("ContentNode") : node.title = item.title : node.description = item.summary : node.hdPosterUrl = item.poster_url : node.url = item.stream_url : node.streamFormat = "hls"
        if item.year <> invalid then node.shortDescriptionLine1 = item.year.ToStr()
      end if
    end for
  end if
  m.top.content = root
end sub
