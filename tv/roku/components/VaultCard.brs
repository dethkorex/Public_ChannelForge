sub changed()
  m.top.findNode("poster").uri = m.top.itemContent.hdPosterUrl
  m.top.findNode("title").text = m.top.itemContent.title + "  " + m.top.itemContent.shortDescriptionLine1
end sub
sub focused()
  if m.top.focusPercent > 0.5 then m.top.findNode("case").color = "0xe8ff18ff" : m.top.scale = [1.08,1.08] else m.top.findNode("case").color = "0x00dcf5ff" : m.top.scale = [1,1]
end sub
