sub itemChanged()
  channel = m.top.itemContent
  if channel = invalid then return
  label = "CH " + channel.shortDescriptionLine1
  if channel.channelRating <> invalid and channel.channelRating <> "" then label += "  •  " + channel.channelRating
  m.top.findNode("number").text = label
  m.top.findNode("title").text = channel.title
  art = channel.hdPosterUrl
  now = CreateObject("roDateTime").AsSeconds()
  current = "Schedule unavailable"
  upcoming = []
  if channel.programs <> invalid
    for each program in channel.programs
      if program.title <> invalid and program.start_epoch_ms <> invalid and program.stop_epoch_ms <> invalid
        if program.start_epoch_ms / 1000# > now
          upcoming.Push(program.title)
          if upcoming.Count() = 2 then exit for
        else if program.stop_epoch_ms / 1000# > now
          current = program.title
          for each address in [program.backdrop_url, program.series_poster_url, program.poster_url]
            if address <> invalid and address <> "" then art = address : exit for
          end for
        end if
      end if
    end for
  end if
  m.top.findNode("now").text = current
  m.top.findNode("logo").uri = art
  nextTitle = "—" : laterTitle = "—"
  if upcoming.Count() > 0 then nextTitle = upcoming[0]
  if upcoming.Count() > 1 then laterTitle = upcoming[1]
  m.top.findNode("next").text = nextTitle
  m.top.findNode("later").text = laterTitle
end sub
sub focusChanged()
  if m.top.focusPercent > 0.5 then m.top.findNode("background").color = "0x16435aff" else m.top.findNode("background").color = "0x0d1724ff"
end sub
