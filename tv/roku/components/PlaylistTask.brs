sub init()
  m.top.functionName = "load"
end sub
sub load()
  transfer = CreateObject("roUrlTransfer")
  transfer.SetCertificatesFile("common:/certs/ca-bundle.crt")
  transfer.InitClientCertificates()
  transfer.SetUrl(m.top.url)
  lines = transfer.GetToString().Tokenize(chr(10))
  root = CreateObject("roSGNode", "ContentNode")
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
