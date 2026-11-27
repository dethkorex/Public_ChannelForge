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
  progress = 0
  upcoming = []
  if channel.programs <> invalid
    for each program in channel.programs
      if program.title <> invalid and program.start_epoch_ms <> invalid and program.stop_epoch_ms <> invalid
        if program.start_epoch_ms / 1000# > now
          upcoming.Push(program)
          if upcoming.Count() = 3 then exit for
        else if program.stop_epoch_ms / 1000# > now
          current = program.title
          progress = (now - program.start_epoch_ms / 1000#) / ((program.stop_epoch_ms - program.start_epoch_ms) / 1000#)
          for each address in [program.backdrop_url, program.series_poster_url, program.poster_url]
            if address <> invalid and address <> "" then art = address : exit for
          end for
        end if
      end if
    end for
  end if
  m.top.findNode("now").text = current
  m.top.findNode("logo").uri = art
  m.top.findNode("progress").width = 153 * progress
  fields = ["next", "later", "laterTwo"]
  for index = 0 to 2
    caption = "—" : time = ""
    if index < upcoming.Count()
      program = upcoming[index]
      caption = program.title
      split = Instr(1, caption, " · S")
      if split > 1
        time = Mid(caption, split + 3)
        caption = Left(caption, split - 1)
      else
        clock = CreateObject("roDateTime")
        clock.FromSeconds(Int(program.start_epoch_ms / 1000#))
        clock.ToLocalTime()
        time = clock.GetHours().ToStr() + ":" + Right("0" + clock.GetMinutes().ToStr(), 2)
      end if
    end if
    m.top.findNode(fields[index]).text = caption
    m.top.findNode(fields[index] + "Time").text = time
  end for
end sub
sub focusChanged()
  if m.top.focusPercent > 0.5 then m.top.findNode("background").color = "0x16435aff" else m.top.findNode("background").color = "0x0d1724ff"
end sub
