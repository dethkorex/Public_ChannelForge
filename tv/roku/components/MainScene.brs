sub init()
  m.guide = m.top.findNode("guide") : m.video = m.top.findNode("video") : m.creators = m.top.findNode("creators") : m.channels = m.top.findNode("channels") : m.vault = m.top.findNode("vault") : m.pair = m.top.findNode("pair") : m.updates = m.top.findNode("updates") : m.status = m.top.findNode("status") : m.directoryTimer = m.top.findNode("directoryTimer")
  m.creators.observeField("itemSelected", "creatorSelected") : m.channels.observeField("itemSelected", "channelSelected") : m.vault.observeField("itemSelected", "vaultSelected") : m.pair.observeField("buttonSelected", "showPairing") : m.updates.observeField("buttonSelected", "checkUpdates")
  m.directoryTimer.observeField("fire", "refreshCatalog") : m.directoryTimer.control = "start"
  m.liveRetryTimer = m.top.findNode("liveRetryTimer")
  m.liveRetryTimer.observeField("fire", "continueLive")
  m.video.observeField("state", "videoStateChanged")
  m.liveItem = invalid
  m.liveRetries = 0
  m.catalogLoading = false
  m.creatorUrl = ""
  refreshCatalog()
end sub
sub refreshCatalog()
  if m.guide.visible = false or m.catalogLoading = true then return
  m.catalogLoading = true
  m.status.text = "Refreshing public creator studios…"
  task = CreateObject("roSGNode", "CatalogTask") : task.url = "https://channelforge-directory.fresh-anorak.workers.dev/v1/catalog" : task.fallbackUrl = "https://raw.githubusercontent.com/dethkorex/Public_ChannelForge/main/directory/catalog.json" : task.observeField("content", "catalogLoaded")
  m.catalogTask = task
  task.control = "run"
end sub
sub catalogLoaded()
  m.catalogLoading = false
  m.creators.content = m.catalogTask.content
  m.status.text = m.catalogTask.content.GetChildCount().ToStr() + " creator studios online"
  if m.catalogTask.content.GetChildCount() > 0
    selected = 0
    for index = 0 to m.catalogTask.content.GetChildCount() - 1
      if m.catalogTask.content.GetChild(index).url = m.creatorUrl then selected = index
    end for
    loadCreator(selected)
  else
    registry = CreateObject("roRegistrySection", "ChannelForge")
    paired = registry.Read("creator_url")
    if paired <> ""
      connectPaired(paired)
    else
      m.status.text = "No public studios are online. The TV app is standalone; optional direct pairing is available for recovery."
      m.pair.SetFocus(true)
    end if
  end if
end sub
sub showPairing()
  dialog = CreateObject("roSGNode", "KeyboardDialog")
  dialog.title = "Optional direct Creator pairing"
  dialog.message = "Normally public studios appear automatically. Enter a Creator HTTPS URL only when direct recovery is needed."
  dialog.buttons = ["PAIR", "CANCEL"]
  dialog.observeField("buttonSelected", "pairingSelected")
  m.top.dialog = dialog
end sub
sub pairingSelected()
  dialog = m.top.dialog
  if dialog = invalid then return
  if dialog.buttonSelected = 0
    base = dialog.text.Trim()
    while right(base, 1) = "/"
      base = left(base, len(base) - 1)
    end while
    suffix = "/api/public/channels"
    if len(base) > len(suffix) and right(base, len(suffix)) = suffix then base = left(base, len(base) - len(suffix))
    if left(base, 8) <> "https://"
      m.status.text = "Pairing URLs must begin with https://"
    else
      registry = CreateObject("roRegistrySection", "ChannelForge")
      registry.Write("creator_url", base)
      registry.Flush()
      connectPaired(base)
    end if
  end if
  dialog.close = true
end sub
sub connectPaired(base as string)
  root = CreateObject("roSGNode", "ContentNode")
  creator = root.CreateChild("ContentNode")
  creator.title = "Paired Creator"
  creator.description = "Direct secure pairing"
  creator.url = base + "/exports/channels.m3u"
  creator.AddField("statusUrl", "string", false)
  creator.statusUrl = base + "/api/public/channels"
  creator.shortDescriptionLine2 = base + "/api/public/vault"
  m.creators.content = root
  loadCreator(0)
end sub
sub creatorSelected()
  loadCreator(m.creators.itemSelected)
end sub
sub loadCreator(index as integer)
  creator = m.creators.content.GetChild(index)
  if creator = invalid then return
  switched = m.creatorUrl <> creator.url
  m.creatorUrl = creator.url
  m.status.text = "Loading " + creator.title + "…"
  task = CreateObject("roSGNode", "PlaylistTask") : task.url = creator.url
  if creator.statusUrl <> invalid and creator.statusUrl <> ""
    task.url = creator.statusUrl
    task.onlineCatalog = true
  end if
  if m.playlistTask <> invalid then m.playlistTask.unobserveField("content")
  task.observeField("content", "playlistLoaded")
  m.playlistTask = task
  task.control = "run"
  if switched or m.vault.content = invalid
    if m.vaultTask <> invalid then m.vaultTask.unobserveField("content")
    vaultTask = CreateObject("roSGNode", "VaultTask") : vaultTask.url = creator.shortDescriptionLine2 : vaultTask.observeField("content", "vaultLoaded")
    m.vaultTask = vaultTask
    vaultTask.control = "run"
  end if
end sub
sub vaultLoaded()
  m.vault.content = m.vaultTask.content
end sub
sub playlistLoaded()
  m.channels.content = m.playlistTask.content
  m.status.text = m.playlistTask.content.GetChildCount().ToStr() + " channels ready"
  m.channels.SetFocus(true)
end sub
sub vaultSelected()
  item = m.vault.content.GetChild(m.vault.itemSelected)
  if item = invalid then return
  m.liveItem = invalid
  m.liveRetryTimer.control = "stop"
  m.video.content = item : m.video.visible = true : m.video.control = "play" : m.guide.visible = false : m.video.SetFocus(true)
end sub
sub checkUpdates()
  task = CreateObject("roSGNode", "UpdateTask") : task.observeField("result", "updateLoaded") : task.control = "run" : m.updateTask = task
  m.updates.text = "UPDATES  •  CHECKING…"
end sub
sub updateLoaded()
  m.updates.text = m.updateTask.result
end sub
sub channelSelected()
  playChannel(m.channels.itemSelected)
end sub
sub playChannel(index as integer)
  if m.channels.content = invalid or m.channels.content.GetChildCount() = 0 then return
  if index < 0 then index = m.channels.content.GetChildCount() - 1
  if index >= m.channels.content.GetChildCount() then index = 0
  m.channels.jumpToItem = index
  item = m.channels.content.GetChild(index)
  m.liveRetryTimer.control = "stop"
  m.liveItem = item
  m.liveRetries = 0
  m.video.content = item : m.video.visible = true : m.video.control = "play" : m.guide.visible = false : m.video.SetFocus(true)
  m.top.findNode("banner").text = "CH " + item.shortDescriptionLine1 + "   " + item.title : m.top.findNode("banner").visible = true : m.top.findNode("bannerBg").visible = true
end sub
sub videoStateChanged()
  if m.liveItem = invalid or m.guide.visible then return
  if m.video.state = "playing" then m.liveRetries = 0
  if m.video.state = "finished"
    m.liveRetryTimer.duration = 1
    m.liveRetryTimer.control = "start"
  else if m.video.state = "error" and m.liveRetries < 3
    m.liveRetries = m.liveRetries + 1
    m.liveRetryTimer.duration = m.liveRetries * 2
    m.liveRetryTimer.control = "start"
  end if
end sub
sub continueLive()
  if m.liveItem = invalid or m.guide.visible then return
  item = CreateObject("roSGNode", "ContentNode")
  item.url = m.liveItem.url
  item.title = m.liveItem.title
  item.streamFormat = m.liveItem.streamFormat
  m.video.control = "stop"
  m.video.content = item
  m.video.control = "play"
end sub
function onKeyEvent(key as string, press as boolean) as boolean
  if not press then return false
  if m.guide.visible = false
    if key = "OK" or key = "back"
      m.liveItem = invalid
      m.liveRetryTimer.control = "stop"
      m.video.control = "stop"
      m.video.visible = false
      m.guide.visible = true
      m.channels.SetFocus(true)
      return true
    end if
    if key = "up" then playChannel(m.channels.itemFocused + 1) : return true
    if key = "down" then playChannel(m.channels.itemFocused - 1) : return true
  end if
  if key = "left" and m.guide.visible and not m.vault.HasFocus() then m.creators.SetFocus(true) : return true
  if key = "right" and m.guide.visible and not m.vault.HasFocus() then m.channels.SetFocus(true) : return true
  if key = "down" and m.guide.visible and m.channels.HasFocus() and m.channels.itemFocused >= m.channels.content.GetChildCount() - 1 then m.vault.SetFocus(true) : return true
  if key = "up" and m.guide.visible and m.vault.HasFocus() then m.channels.SetFocus(true) : return true
  if key = "up" and m.guide.visible and m.creators.HasFocus() then m.pair.SetFocus(true) : return true
  if key = "right" and m.guide.visible and m.pair.HasFocus() then m.updates.SetFocus(true) : return true
  if key = "left" and m.guide.visible and m.updates.HasFocus() then m.pair.SetFocus(true) : return true
  if key = "down" and m.guide.visible and m.updates.HasFocus() then m.channels.SetFocus(true) : return true
  if key = "down" and m.guide.visible and m.pair.HasFocus() then m.creators.SetFocus(true) : return true
  return false
end function
