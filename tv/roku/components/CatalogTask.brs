sub init()
  m.top.functionName = "load"
end sub
sub load()
  payload = loadCatalog(m.top.url)
  cache = "cachefs:/channelforge-catalog.json"
  previous = m.top.previousRows
  known = m.top.hasPrevious
  if not m.top.hasPrevious
    previous = []
    cached = ReadAsciiFile(cache)
    if cached <> ""
      snapshot = ParseJson(cached)
      if validStudioList(snapshot)
        previous = snapshot.creators
        known = true
      end if
    end if
  end if
  complete = false
  fresh = []
  if validStudioList(payload)
    complete = payload.registrations_complete <> false
    fresh = payload.creators
  else if not known
    seed = loadCatalog(m.top.fallbackUrl)
    if validStudioList(seed) then fresh = seed.creators
  end if
  rows = mergeStudioRows(previous, fresh, complete)
  if complete or fresh.Count() > 0
    json = FormatJson({creators: rows})
    if Len(json) <= 262144
      WriteAsciiFile(cache, json)
    else
      DeleteFile(cache)
    end if
  end if
  m.top.rows = rows
  m.top.stale = not complete
  root = CreateObject("roSGNode", "ContentNode")
  for each creator in rows
    if creator.state <> "offline" and not (creator.state = invalid and creator.online = false)
      node = root.CreateChild("ContentNode")
      node.AddField("studioId", "string", false)
      if creator.id <> invalid then node.studioId = creator.id
      if creator.name <> invalid then node.title = creator.name
      if creator.description <> invalid then node.description = creator.description
      node.url = creator.playlist_url
      node.AddField("statusUrl", "string", false)
      if creator.status_url <> invalid then node.statusUrl = creator.status_url
      if creator.cinema_supported = true and creator.vault_url <> invalid then node.shortDescriptionLine2 = creator.vault_url
      if creator.logo_url <> invalid then node.hdPosterUrl = creator.logo_url
    end if
  end for
  m.top.content = root
end sub

function loadCatalog(address as string) as object
  if address = "" then return invalid
  transfer = CreateObject("roUrlTransfer")
  transfer.SetCertificatesFile("common:/certs/ca-bundle.crt")
  transfer.InitClientCertificates()
  transfer.EnableEncodings(true)
  transfer.SetUrl(address)
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
