sub itemChanged()
  m.top.findNode("number").text = m.top.itemContent.shortDescriptionLine1
  m.top.findNode("title").text = m.top.itemContent.title
  m.top.findNode("now").text = "LIVE NOW  •  Press OK to watch"
end sub
sub focusChanged()
  if m.top.focusPercent > 0.5 then m.top.findNode("background").color = "0x225b5aff" else m.top.findNode("background").color = "0x0d1724ff"
end sub
