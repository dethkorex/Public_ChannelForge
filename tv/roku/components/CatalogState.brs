' Only an explicit complete snapshot from the live directory can remove a studio.
function validStudioList(payload as dynamic) as boolean
  if type(payload) <> "roAssociativeArray" then return false
  return type(payload.creators) = "roArray"
end function

function mergeStudioRows(previous as object, fresh as object, complete as boolean) as object
  rows = []
  seen = {}
  groups = [fresh]
  if not complete then groups.Push(previous)
  for each group in groups
    for each row in group
      if type(row) = "roAssociativeArray" and GetInterface(row.playlist_url, "ifString") <> invalid and row.playlist_url <> ""
        id = ""
        if GetInterface(row.id, "ifString") <> invalid then id = row.id
        key = "url:" + row.playlist_url
        if not seen.DoesExist(key) and (id = "" or not seen.DoesExist("id:" + id))
          rows.Push(row)
          seen[key] = true
          if id <> "" then seen["id:" + id] = true
        end if
      end if
    end for
  end for
  return rows
end function
