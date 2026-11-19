"""Example Public_ChannelForge extension tool."""

def describe():
    return {"name": "Channel summary", "description": "Counts the channels and scheduled titles in this installation."}

def run(payload, context):
    channels = context["channels"]
    titles = sum(len(channel.get("items", [])) for channel in channels)
    return f"{len(channels)} channel(s), {titles} scheduled title(s)."

