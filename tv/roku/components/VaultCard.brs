sub changed()
  m.top.findNode("poster").uri = m.top.itemContent.hdPosterUrl
  m.top.findNode("title").text = m.top.itemContent.title + "  " + m.top.itemContent.shortDescriptionLine1
end sub
sub focused()
  amount = m.top.focusPercent
  m.top.findNode("face").translation = [0, -2 * amount]
  if amount > 0.5 then m.top.findNode("case").color = "0xff69d0ff" else m.top.findNode("case").color = "0xc8a77bff"
end sub
