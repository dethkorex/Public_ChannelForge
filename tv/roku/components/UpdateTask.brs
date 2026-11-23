sub init()
  m.top.functionName = "load"
end sub
sub load()
  transfer = CreateObject("roUrlTransfer") : transfer.SetCertificatesFile("common:/certs/ca-bundle.crt") : transfer.InitClientCertificates() : transfer.SetUrl("https://api.github.com/repos/dethkorex/Public_ChannelForge/releases/latest") : transfer.AddHeader("User-Agent", "ChannelForge-TV-Roku")
  release = ParseJson(transfer.GetToString())
  if release = invalid or release.tag_name = invalid then m.top.result = "UPDATES  •  RETRY" : return
  if release.tag_name = "v3.5.18" then m.top.result = "UP TO DATE  ✓  v3.5.18" else m.top.result = "UPDATE " + release.tag_name + " AVAILABLE  ✓"
end sub
