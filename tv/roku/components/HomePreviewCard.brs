sub paint()
  item = m.top.itemContent
  if item = invalid or m.top.width < 165 then return
  width = m.top.width
  captionWidth = width - 164
  outline = m.top.findNode("outline")
  outline.width = width
  if m.top.itemHasFocus then outline.color = "0x36e9fcff" else outline.color = "0x455b78ff"
  m.top.findNode("card").width = width - 4
  title = m.top.findNode("title")
  title.width = captionWidth
  title.text = item.title
  caption = m.top.findNode("caption")
  caption.width = captionWidth
  caption.text = item.shortDescriptionLine1
  m.top.findNode("art").uri = item.hdPosterUrl
  m.top.findNode("track").width = captionWidth
  m.top.findNode("track").visible = item.progress > 0
  m.top.findNode("progress").width = captionWidth * item.progress
end sub
