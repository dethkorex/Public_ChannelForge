sub init()
  m.top.functionName = "load"
end sub
sub load()
  release = requestRelease("https://api.github.com/repos/dethkorex/Public_ChannelForge/releases/latest")
  if release = invalid then release = requestRelease("https://github.com/dethkorex/Public_ChannelForge/releases/latest/download/ChannelForge-release.json")
  if release = invalid then m.top.result = "RETRY UPDATE CHECK" : return
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
  installed = [3, 6, 0]
  candidate = Mid(release.tag_name, 2).Tokenize(".")
  for index = 0 to 2
    part = Val(candidate[index])
    if part > installed[index]
      m.top.result = "UPDATE " + release.tag_name + " AVAILABLE  ✓"
      return
    end if
    if part < installed[index] then exit for
  end for
  m.top.result = "UP TO DATE  ✓  v3.6.0"
end sub

function requestRelease(address as string) as object
  transfer = CreateObject("roUrlTransfer")
  transfer.SetCertificatesFile("common:/certs/ca-bundle.crt")
  transfer.InitClientCertificates()
  transfer.SetUrl(address)
  transfer.AddHeader("User-Agent", "ChannelForge-TV-Roku")
  messages = CreateObject("roMessagePort")
  transfer.SetMessagePort(messages)
  if not transfer.AsyncGetToString() then return invalid
  response = wait(6000, messages)
  if response = invalid
    transfer.AsyncCancel()
    return invalid
  end if
  if response.GetResponseCode() <> 200 then return invalid
  release = ParseJson(response.GetString())
  pattern = CreateObject("roRegex", "^v[0-9]+\.[0-9]+\.[0-9]+$", "")
  if release = invalid or release.tag_name = invalid or not pattern.IsMatch(release.tag_name) then return invalid
  return release
end function
