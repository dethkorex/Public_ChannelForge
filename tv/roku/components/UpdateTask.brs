sub init()
  m.top.functionName = "load"
end sub
sub load()
  release = requestRelease("https://api.github.com/repos/dethkorex/Public_ChannelForge/releases/latest")
  if release = invalid then release = requestRelease("https://github.com/dethkorex/Public_ChannelForge/releases/latest/download/ChannelForge-release.json")
  if release <> invalid
    notes = ""
    for each line in release.body.Tokenize(chr(10))
      text = line.Trim()
      if text <> "" and Instr(1, text, "://") = 0 and Left(text, 2) <> "##"
        if Len(notes) + Len(text) > 1100 then exit for
        if Len(notes) > 0 then notes = notes + chr(10)
        notes = notes + text
      end if
    end for
    if notes <> "" then m.top.releaseTag = release.tag_name
    m.top.notes = notes
  end if
  m.top.result = "On Roku Home, highlight ChannelForge TV, press * and choose Check for updates. Roku installs any available Store update."
end sub

function requestRelease(address as string) as object
  release = requestMetadata(address)
  if type(release) <> "roAssociativeArray" then return invalid
  pattern = CreateObject("roRegex", "^v[0-9]+\.[0-9]+\.[0-9]+$", "")
  if type(release.tag_name) <> "String" and type(release.tag_name) <> "roString" then return invalid
  if not pattern.IsMatch(release.tag_name) then return invalid
  if type(release.body) <> "String" and type(release.body) <> "roString" then release.body = ""
  return release
end function

function requestMetadata(address as string) as object
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
  return ParseJson(response.GetString())
end function
