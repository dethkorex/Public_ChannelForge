# ChannelForge Alpha 4.1

Alpha 4.1 addresses a major cause of buffering on channels that used the old publishing route. Update both ChannelForge TV and ChannelForge Studio if you host channels on Windows or Linux.

## For viewers

- **A shared relay for live TV.** Studio uploads one copy of each video segment. The VPS serves that copy to everyone watching, so extra viewers use relay bandwidth.
- **Sort the live guide by rating.** Choose **Rating** to put lower-rated channels first. Unrated channels appear at the end.

## For channel creators

- **Automatic relay setup.** Use **Publish worldwide** as usual. Studio configures its VPS connection and can connect through Tailscale on IPv4-only networks. Studio checks a replacement route before updating the public guide.
- **Set a maximum content rating.** Choose the rating before picking titles. Studio filters the library and checks the lineup again on save.
- **Reserve a public channel number.** Studio checks for conflicts across the public guide. Deleting a channel or stopping sharing frees its number. A temporary outage keeps it reserved.
- **An easier channel builder.** Reorder selections with drag and drop or keyboard controls, and restore the selected playback preset when you want to restart a lineup.
- **Studio fixes.** Find resource usage under **System → Activity**. Release notes wrap to fit the window, and desktop installers use a dedicated app folder and branded taskbar icons.

Available for Windows, Linux, Fire TV, Android phones, Android TV and onn. The Roku Store release is still on hold. For setup or playback help, visit the [ChannelForge Discord](https://discord.gg/Kug9rZQP2D).
