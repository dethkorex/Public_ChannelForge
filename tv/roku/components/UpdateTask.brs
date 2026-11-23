sub init()
  m.top.functionName = "load"
end sub
sub load()
  transfer = CreateObject("roUrlTransfer") : transfer.SetCertificatesFile("common:/certs/ca-bundle.crt") : transfer.InitClientCertificates() : transfer.SetUrl("https://api.github.com/repos/dethkorex/Public_ChannelForge/releases/latest") : transfer.AddHeader("User-Agent", "ChannelForge-TV-Roku")
  release = ParseJson(transfer.GetToString())
  if release = invalid or release.tag_name = invalid then m.top.result = "UPDATES  •  RETRY" : return
  m.top.releaseTag = release.tag_name
  if release.body <> invalid
    notes = ""
    for each line in release.body.Tokenize(chr(10))
      text = line.Trim()
      if text <> "" and Instr(1, text, "://") = 0 and Left(text, 2) <> "##"
        if Len(notes) + Len(text) > 1100 then exit for
        if Len(notes) > 0 then notes = notes + chr(10)
        notes = notes + text
      end if
    end for
    m.top.notes = notes
  end if
  if release.tag_name = "v3.5.26" then m.top.result = "UP TO DATE  ✓  v3.5.26" else m.top.result = "UPDATE " + release.tag_name + " AVAILABLE  ✓"
end sub
