sub init()
  m.poster = m.top.findNode("poster")
  m.poster.observeField("loadStatus", "posterChanged")
end sub
sub changed()
  item = m.top.itemContent
  if item = invalid then return
  m.top.findNode("initial").text = Left(item.title, 1)
  m.fallback = item.hdPosterUrl
  art = m.fallback
  if item.landscapeArtwork <> invalid and item.landscapeArtwork <> "" then art = item.landscapeArtwork
  m.poster.uri = art
  m.top.findNode("title").text = item.title
end sub
sub posterChanged()
  if m.poster.loadStatus = "failed" and m.fallback <> invalid and m.poster.uri <> m.fallback then m.poster.uri = m.fallback
end sub

sub focused()
  outline = m.top.findNode("outline")
  if m.top.itemHasFocus then outline.color = "0xf338deff" else outline.color = "0xd7a967ff"
end sub
