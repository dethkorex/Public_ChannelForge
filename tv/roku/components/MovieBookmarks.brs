function movieBookmarkKey(studioId as string, movieKey as string) as string
  return studioId + "|" + movieKey
end function

function movieResumePosition(registry as object, key as string, history as object) as dynamic
  stored = registry.Read(key)
  separator = Instr(1, stored, "|")
  if separator > 1 then return Val(Left(stored, separator - 1))
  ' Older builds kept positions only in the Continue Watching shelf.
  for each row in history
    if row.key = key and row.position > 0 then return row.position
  end for
  return 0.0
end function

sub writeMovieBookmark(registry as object, key as string, position as dynamic, completed as boolean, now as integer)
  if completed
    registry.Delete(key)
  else if position > 0
    ' The shelf can evict old cards while their compact playback bookmarks are retained.
    ok = registry.Write(key, position.ToStr() + "|" + now.ToStr())
    if not ok
      for each savedKey in registry.GetKeyList()
        entry = registry.Read(savedKey)
        separator = Instr(1, entry, "|")
        if separator > 1 and now - Val(Mid(entry, separator + 1)) > 30 * 24 * 60 * 60 then registry.Delete(savedKey)
      end for
      registry.Write(key, position.ToStr() + "|" + now.ToStr())
    end if
  end if
  registry.Flush()
end sub
