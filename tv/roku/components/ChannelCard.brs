sub itemChanged()
  m.top.findNode("number").text = m.top.itemContent.shortDescriptionLine1
  m.top.findNode("title").text = m.top.itemContent.title
  state = m.top.itemContent.availability
  label = "Press OK to watch"
  if state = "offline" then label = "Studio reports this channel is offline"
  if state = "reconnecting" then label = "Reconnecting to the studio"
  if state = "overloaded" then label = "Studio is busy"
  rating = m.top.itemContent.channelRating
  if rating <> invalid and rating <> "" then label = rating + "  •  " + label
  m.top.findNode("now").text = label
end sub
sub focusChanged()
  if m.top.focusPercent > 0.5 then m.top.findNode("background").color = "0x50206cff" else m.top.findNode("background").color = "0x0d1724ff"
end sub
