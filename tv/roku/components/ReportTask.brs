sub init()
  m.top.functionName = "send"
end sub

sub send()
  appInfo = CreateObject("roAppInfo")
  portal = appInfo.GetValue("channelforge_tester_portal")
  if portal = ""
    m.top.result = "Report sharing is unavailable on this build."
    return
  end if
  transfer = CreateObject("roUrlTransfer")
  transfer.SetCertificatesFile("common:/certs/ca-bundle.crt")
  transfer.InitClientCertificates()
  transfer.SetUrl(portal + "/report")
  transfer.AddHeader("Content-Type", "application/json")
  messages = CreateObject("roMessagePort")
  transfer.SetMessagePort(messages)
  if transfer.AsyncPostFromString(FormatJson(m.top.report))
    response = wait(8000, messages)
    if response <> invalid and response.GetResponseCode() = 201
      receipt = response.GetString()
      pattern = CreateObject("roRegex", "^[0-9a-f]{10}$", "")
      if pattern.IsMatch(receipt)
        m.top.result = "Report sent. ID " + receipt
        return
      end if
    end if
  end if
  transfer.AsyncCancel()
  m.top.result = "Could not send the report. Try again from Developer."
end sub
