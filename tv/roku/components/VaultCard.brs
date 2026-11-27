sub init()
  m.poster = m.top.findNode("poster")
  m.poster.observeField("loadStatus", "posterChanged")
end sub
sub changed()
  item = m.top.itemContent
  if item = invalid then return
  m.fallback = item.hdPosterUrl
  art = m.fallback
  if item.landscapeArtwork <> invalid and item.landscapeArtwork <> "" then art = item.landscapeArtwork
  m.poster.uri = art
  m.top.findNode("title").text = item.title + "  " + item.shortDescriptionLine1
end sub
sub posterChanged()
  if m.poster.loadStatus = "failed" and m.fallback <> invalid and m.poster.uri <> m.fallback then m.poster.uri = m.fallback
end sub
sub focused()
  amount = m.top.focusPercent
  m.top.findNode("face").translation = [0, -2 * amount]
  if amount > 0.5 then m.top.findNode("case").color = "0xff69d0ff" else m.top.findNode("case").color = "0xc8a77bff"
end sub
