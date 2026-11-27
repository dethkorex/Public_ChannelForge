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
      if message.IsScreenClosed()
        sendLiveDeparture(scene.liveDepartureUrl)
        sendAlphaShutdown(scene.alphaHealth)
        return
      end if
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

sub sendLiveDeparture(address as dynamic)
  if address = invalid or address = "" then return
  transfer = CreateObject("roUrlTransfer")
  transfer.SetCertificatesFile("common:/certs/ca-bundle.crt")
  transfer.SetUrl(address)
  port = CreateObject("roMessagePort")
  transfer.SetMessagePort(port)
  if transfer.AsyncGetToString()
    if wait(800, port) = invalid then transfer.AsyncCancel()
  end if
end sub

sub sendAlphaShutdown(snapshot as dynamic)
  app = CreateObject("roAppInfo")
  report = {platform:"roku", component:"tv", app:app.GetValue("channelforge_version"), report_type:"exit"}
  if type(snapshot) = "roAssociativeArray"
    for each field in ["kind", "delivery_path", "stalls", "stall_ms", "first_progress_ms"]
      if snapshot[field] <> invalid then report[field] = snapshot[field]
    end for
  end if
  transfer = CreateObject("roUrlTransfer")
  transfer.SetCertificatesFile("common:/certs/ca-bundle.crt")
  transfer.SetUrl("https://channelforge-directory.fresh-anorak.workers.dev/v1/diagnostics/exit")
  transfer.AddHeader("Content-Type", "application/json")
  port = CreateObject("roMessagePort")
  transfer.SetMessagePort(port)
  if transfer.AsyncPostFromString(FormatJson(report))
    result = wait(1600, port)
    if result = invalid then transfer.AsyncCancel()
  end if
end sub
