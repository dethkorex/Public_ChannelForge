sub itemChanged()
  m.top.findNode("title").text = m.top.itemContent.title
  m.top.findNode("description").text = m.top.itemContent.description
end sub
sub focusChanged()
  if m.top.focusPercent > 0.5 then m.top.findNode("background").color = "0x50206cff" else m.top.findNode("background").color = "0x101b2aff"
end sub
