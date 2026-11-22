sub init()
  m.guide = m.top.findNode("guide") : m.video = m.top.findNode("video") : m.creators = m.top.findNode("creators") : m.channels = m.top.findNode("channels") : m.status = m.top.findNode("status")
  m.creators.observeField("itemSelected", "creatorSelected") : m.channels.observeField("itemSelected", "channelSelected")
  task = CreateObject("roSGNode", "CatalogTask") : task.url = "https://directory.channelforge.tv/v1/catalog" : task.observeField("content", "catalogLoaded") : task.control = "run"
  m.catalogTask = task
end sub
sub catalogLoaded()
  m.creators.content = m.catalogTask.content
  m.status.text = m.catalogTask.content.GetChildCount().ToStr() + " creator studios online"
  m.creators.SetFocus(true)
  if m.catalogTask.content.GetChildCount() > 0 then loadCreator(0)
end sub
sub creatorSelected()
  loadCreator(m.creators.itemSelected)
end sub
sub loadCreator(index as integer)
  creator = m.creators.content.GetChild(index)
  m.status.text = "Loading " + creator.title + "…"
  task = CreateObject("roSGNode", "PlaylistTask") : task.url = creator.url : task.observeField("content", "playlistLoaded") : task.control = "run"
  m.playlistTask = task
end sub
sub playlistLoaded()
  m.channels.content = m.playlistTask.content
  m.status.text = m.playlistTask.content.GetChildCount().ToStr() + " channels ready"
  m.channels.SetFocus(true)
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
  m.video.content = item : m.video.visible = true : m.video.control = "play" : m.guide.visible = false : m.video.SetFocus(true)
  m.top.findNode("banner").text = "CH " + item.shortDescriptionLine1 + "   " + item.title : m.top.findNode("banner").visible = true : m.top.findNode("bannerBg").visible = true
end sub
function onKeyEvent(key as string, press as boolean) as boolean
  if not press then return false
  if m.guide.visible = false
    if key = "OK" or key = "back" then m.guide.visible = true : m.channels.SetFocus(true) : return true
    if key = "up" then playChannel(m.channels.itemFocused + 1) : return true
    if key = "down" then playChannel(m.channels.itemFocused - 1) : return true
  end if
  if key = "left" and m.guide.visible then m.creators.SetFocus(true) : return true
  if key = "right" and m.guide.visible then m.channels.SetFocus(true) : return true
  return false
end function
