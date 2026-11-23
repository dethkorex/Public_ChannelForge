sub init()
  m.top.functionName = "load"
end sub
sub load()
  payload = loadCatalog(m.top.url)
  if payload = invalid or payload.creators = invalid then payload = loadCatalog(m.top.fallbackUrl)
  root = CreateObject("roSGNode", "ContentNode")
  if payload <> invalid and payload.creators <> invalid
    for each creator in payload.creators
      node = root.CreateChild("ContentNode")
      node.title = creator.name
      node.description = creator.description
      node.url = creator.playlist_url
      node.shortDescriptionLine2 = creator.vault_url
      node.hdPosterUrl = creator.logo_url
    end for
  end if
  m.top.content = root
end sub
function loadCatalog(address as string) as object
  if address = "" then return invalid
  transfer = CreateObject("roUrlTransfer")
  transfer.SetCertificatesFile("common:/certs/ca-bundle.crt")
  transfer.InitClientCertificates()
  transfer.SetUrl(address)
  return ParseJson(transfer.GetToString())
end function
