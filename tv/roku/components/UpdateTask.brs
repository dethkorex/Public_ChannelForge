sub init()
  m.top.functionName = "load"
end sub
sub load()
  appInfo = CreateObject("roAppInfo")
  testerPortal = appInfo.GetValue("channelforge_tester_portal")
  if testerPortal <> ""
    loadTesterUpdate(testerPortal, Val(appInfo.GetValue("channelforge_tester_revision")), appInfo)
    return
  end if
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

sub loadTesterUpdate(portal as string, installed as integer, appInfo as object)
  m.top.releaseTag = alphaVersionLabel(appInfo.GetValue("channelforge_version"))
  localNotes = appInfo.GetValue("channelforge_tester_notes")
  if localNotes <> "" then m.top.notes = testerNotes(ParseJson(localNotes))
  manifest = requestMetadata(portal + "/manifest.json?manual=1")
  if type(manifest) <> "roAssociativeArray" then m.top.result = "Could not check for an update. Try again." : return
  package = manifest["ChannelForgeTV-Roku-4.1-private.zip"]
  if type(package) <> "roAssociativeArray" then m.top.result = "TESTER ZIP UNAVAILABLE" : return
  if type(package.revision) <> "Integer" and type(package.revision) <> "roInt" and type(package.revision) <> "roInteger"
    m.top.result = "RETRY TESTER UPDATE CHECK"
    return
  end if
  if package.revision <= installed
    m.top.result = "You’re on the latest Tester build."
    return
  end if
  m.top.releaseTag = "Tester build " + package.revision.ToStr()
  version = package.version
  if type(version) = "String" or type(version) = "roString"
    pattern = CreateObject("roRegex", "^[0-9]+\.[0-9]+\.[0-9]+$", "")
    if pattern.IsMatch(version) then m.top.releaseTag = alphaVersionLabel(version)
  end if
  m.top.notes = testerNotes(package.release_notes)
  m.top.result = "Download the new Roku ZIP from the private Tester page and install it in the Roku Development Application Installer."
end sub

function testerNotes(lines as dynamic) as string
  notes = ""
  if type(lines) <> "roArray" then return notes
  for each line in lines
    if type(line) = "String" or type(line) = "roString"
      if Len(notes) + Len(line) > 720 then exit for
      if notes <> "" then notes = notes + Chr(10)
      notes = notes + "• " + line
    end if
  end for
  return notes
end function

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
