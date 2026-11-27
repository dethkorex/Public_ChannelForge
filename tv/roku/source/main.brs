sub Main(args as dynamic)
  screen = CreateObject("roSGScreen")
  port = CreateObject("roMessagePort")
  screen.SetMessagePort(port)
  input = CreateObject("roInput")
  input.SetMessagePort(port)
  scene = screen.CreateScene("MainScene")
  screen.Show()
  if type(args) <> "roAssociativeArray" then args = {}
  scene.deepLinkRequest = args
  while true
    message = wait(0, port)
    if type(message) = "roSGScreenEvent"
      if message.IsScreenClosed() then return
    else if type(message) = "roInputEvent"
      if message.IsInput()
        info = message.GetInfo()
        if type(info) = "roAssociativeArray" and (info.contentId <> invalid or info.mediaType <> invalid)
          scene.deepLinkRequest = info
        end if
      end if
    end if
  end while
end sub
