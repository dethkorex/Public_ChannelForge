# Create a Public ChannelForge tool

Tools are plain Python files stored in the application data `plugins` directory shown on the Tools screen. A tool must define two functions:

```python
def describe():
    return {"name": "My tool", "description": "What it does."}

def run(payload, context):
    # context contains data_dir and a read-only snapshot of channels.
    return "Finished"
```

Restart Public ChannelForge after adding or changing a file. Tool files run with the user's normal operating-system permissions. Never install a tool you do not trust, and never put tokens or passwords directly in a tool file.

