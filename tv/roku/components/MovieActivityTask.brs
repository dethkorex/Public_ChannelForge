sub init()
  m.top.functionName = "refreshLease"
end sub

sub refreshLease()
  request = CreateObject("roUrlTransfer")
  request.SetCertificatesFile("common:/certs/ca-bundle.crt")
  request.SetUrl(m.top.url)
  port = CreateObject("roMessagePort")
  request.SetMessagePort(port)
  if request.AsyncGetToString()
    response = wait(5000, port)
    if response = invalid then request.AsyncCancel()
  end if
end sub
