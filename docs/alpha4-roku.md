# Roku 4.0.0 alpha

The developer sideload ZIP is `build/alpha-v4-roku-store/ChannelForge-TV-Roku-4.0.0.zip`. The manifest uses version 4.0.0, build 3, and the title **ChannelForge TV**. This build uses the public directory configured in MainScene. Testing partial directory responses, route rotation or deliberate outages requires a separate QA directory and studio.

## Verified on the development host

Run these from the repository root with Node, npm and Python installed. On Windows PowerShell, use `npx.cmd` and `py` if needed.

```sh
npx --yes brighterscript@0.70.3 --root-dir tv/roku --create-package=false --copy-to-staging=false
npx --yes brs@0.45.0 tv/roku/components/CatalogState.brs tv/roku/components/MovieBookmarks.brs tv/roku/components/MainScene.brs tv/roku/components/DeepLink.brs tests/roku/recovery.brs
python scripts/package_roku.py build/alpha-v4-roku-store/ChannelForge-TV-Roku-4.0.0.zip
```

The first command validated the SceneGraph project. The second printed `PASS Roku discovery and recovery`, including checks for migration from the old Continue Watching shelf and retention of recent movie bookmarks when the registry fills. The final command built a 31-file archive, checked CRCs, decoded BrightScript as UTF-8 and resolved every component script and manifest artwork reference. The tested archive was 2,161,836 bytes and its SHA-256 was `a7ef2e68beaff49c7efacbce705da955ccd8fb7fcd2fe919b14dfb992c00fb66`.

## Playback and discovery checks on Roku hardware

The host checks exercised application logic. Roku OS playback, its native decoder, physical remote input and Store signing still require separate verification. This build has no measured Roku startup or frame-drop figures.

1. Use a developer-enabled Roku with internet access. Sign in to its Development Application Installer in a browser as `rokudev`. Install the ZIP, launch ChannelForge TV Alpha, and open the guide. Use two published QA studios and a viewer network separate from both Creator machines. Verify both studios, their channels and movies appear without entering a URL. [Roku developer setup](https://developer.roku.com/dev/docs/developer-setup)
2. Start a channel with two short programs. Observe the upcoming program banner and playback across the boundary. Record player model, Roku OS version, public route, time to first picture and duration of any stall. Repeat with a movie. Pause, resume, eject and confirm the movie selection returns. Verify that opening it again from Rewind Cinema, from Continue Watching and from a movie deep link resumes from the saved position.
3. During playback, interrupt delivery of media segments on the QA studio. Keep the test directory reachable. Try both a short interruption and a failure exceeding 20 seconds. The buffering deadline or position watchdog should trigger recovery. Recover the route and check the selected channel resumes. For a movie, check it resumes near the last played position. A sustained outage should exhaust the two automatic retries and present a manual action. Back during retry should keep the player closed.
4. During recovery, rotate the published studio URL while retaining its studio and channel IDs. Verify the next attempt uses the new URL. Unpublish that channel while keeping the studio registered. Its next successful guide refresh should remove it from the guide or report it offline in the player. Remove the studio and verify a complete directory response clears its guide and movies. A manually saved direct pairing remains a separate viewer choice.
5. With two registered studios already cached, temporarily return a partial directory containing one renamed studio and one new studio. The guide should show those updates and keep the unmentioned studio. Return a complete empty registration snapshot to verify removal. Simulate a failed guide request for a previously viewed studio and confirm cached data is labeled reconnecting.
6. Delay studio A's movie and guide responses, select studio B, and let A's responses arrive. The visible channels, films and selection should belong to B. Repeat while requesting a second movie page from A.

## Store signing

For this classic SceneGraph project, a trusted teammate with an internet-connected, developer-enabled Roku linked to the appropriate developer account can sideload the ZIP. For an existing app, restore its original signing identity using **Utilities → Rekey**, its previous signed package and its password. For a new identity, use the device's developer console on port 8080 and run `genkey`. Keep the generated Dev ID and password with the owner. [Roku app packaging and rekeying](https://developer.roku.com/dev/docs/packaging-channels)

On the device's **Packager** page, verify the Dev ID, enter the app name/version and signing password, and download the generated `.pkg`. The authorized publisher can then upload it to the app's Developer Dashboard listing for beta testing or certification. An update signed with the same identity preserves access to the app's existing registry. [Roku packaging](https://developer.roku.com/dev/docs/packaging-channels) · [Roku app publishing](https://developer.roku.com/dev/docs/channel-publishing-guide)

Roku also documents host signing in its RSG 2.0 SDK. That workflow takes an SDK-built squashfs archive, an existing Roku key package and its password. This project's classic sideload ZIP is a different input format. No Store package was produced in this verification. [Roku SDK host packaging](https://developer.roku.com/dev/docs/rsg-sdk-standing-docs-advanced-rk-package)

Roku's Cloud Emulator can help test the app, but its devices lack signing keys. First-time signing requires generating a key on a physical development device. [Roku Cloud Emulator](https://developer.roku.com/dev/docs/rsg-sdk-standing-docs-advanced-rk-rce)

## Store certification still pending

The movie streams still need thumbnail tracks for trick play on titles longer than 15 minutes. Run the physical-device checks, supply known playable examples for live and movie deep links, and collect screenshots from the Roku app before submitting the Store listing. [Roku certification criteria](https://developer.roku.com/dev/docs/certification) · [Roku trick mode](https://developer.roku.com/dev/docs/trick-mode)
