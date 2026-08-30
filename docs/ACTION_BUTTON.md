# Action Button capture

## The product requirement

```
press Action Button once
  -> Sava captures what's on screen
  -> Save created
  -> keep scrolling
```

No share sheet. No Copy Link. No clipboard step. No opening Sava.

## How the pieces divide up

A **Shortcut** can see the foreground app. A bare **App Intent** cannot. So the
Shortcut gathers the evidence and hands it to the intent:

```
Action Button
  -> Shortcut: Get What's On Screen -> Get URLs -> Count
       Count > 0  -> Save to Sava (URLs)          <- no screenshot taken
       otherwise  -> Take Screenshot -> Save to Sava (Screenshot)
  -> intent picks the real content URL, or resolves the screenshot
  -> save + haptic
```

The intent never takes a screenshot itself, and never uploads one when a URL
was supplied.

## Build the Shortcut (exact actions, in order)

Shortcuts app → **+** → New Shortcut. Add these seven actions.

```
1.  Get What's On Screen
       (no fields)

2.  Get URLs from  [What's On Screen]
       field "from"      <- variable: What's On Screen   (auto-filled)

3.  Count  [URLs]
       field "Type"      <- Items          (NOT Characters)
       field "of"        <- variable: URLs

4.  If  [Count]  is  greater than  0
       field left        <- variable: Count
       condition         <- greater than
       field right       <- 0

5.      Save Link to Sava
           field "Link"  <- variable: URLs

6.  Otherwise

7.      Take Screenshot

8.      Save Screenshot to Sava
           field "Screenshot"  <- variable: Screenshot

9.  End If
```

Then: Settings → Action Button → Shortcut → select this Shortcut.

### The actions

Search the action library for **"Sava"** and you will see three:

| Action | Field | Use for |
|---|---|---|
| **Save to Sava** | `Link or text` (optional) | the general-purpose action — see below |
| **Save Link to Sava** | `Link` | the `Count > 0` branch of the capture Shortcut |
| **Save Screenshot to Sava** | `Screenshot` | the Otherwise branch |

The two branch actions have one field each and no optional clutter. They are
separate actions because a single Shortcut action instance carries one fixed
parameter configuration; you cannot swap which parameter is populated per
branch.

**Save to Sava** is the one to reach for otherwise. Its parameter is a `String`,
which means it accepts a URL, text containing a URL, or nothing at all:

| Given | What happens |
|---|---|
| `https://tiktok.com/@u/video/123` | saved |
| `Check this out 😂 https://vm.tiktok.com/ZMhq/` | the link is extracted and saved |
| a profile link *and* a video link | ranked; the video wins |
| nothing | reads the clipboard |
| text with no link | `No link found` |

A `[URL]` parameter would have looked tidier and refused half of real input —
Share Sheet hands over text on TikTok, "Get Text from Input" produces a string,
and a user who pasted a caption would get a type-mismatch error instead of a
save. URLs are found with `NSDataDetector`, the same detector Messages and Mail
use, so "what counts as a link" matches what the user sees highlighted
everywhere else on their phone.

Verified by `CaptureDiagnostics.runIntentInputSelfCheck()` — 10/10 passing, run
on every Debug launch.

### Siri and the Action Button picker

`SavaShortcuts` registers **Save to Sava** as an App Shortcut with the phrases
*"Save to Sava"*, *"Add to Sava"* and *"Save this to Sava"*, plus *"Save this
screen to Sava"* for the screenshot action.

That registration is what makes Sava findable in **Settings → Action Button →
Shortcut** without the user first building a Shortcut by hand, and what puts it
in Spotlight and Siri. (The actions appear in the Shortcuts *action library*
regardless; the provider is about discovery everywhere else.)

Confirmed in the built app's `Metadata.appintents/root.ssu.yaml`.

Both declare `openAppWhenRun = false`, show no snippet view, and never open
Sava. The only UI is a result dialog you can silence with **Show When Run →
Off** on each action.

### Why Count, and why "Items"

Testing "URLs has any value" is unreliable — Shortcuts treats an empty list as
present, so the If branch fires with nothing in it and the save silently fails.
Counting **Items** and comparing `> 0` is the check that works. Leave the Count
type as *Items*; *Characters* will count the text length instead.

### Pass the whole URLs list, not the first item

Step 5's `Link` field takes the **URLs** variable directly — the entire list.
There is deliberately no "Get Item from List → First Item" step.

"Get What's On Screen" routinely returns several URLs, and the first is often
not the video:

```
https://www.tiktok.com/@mystery_jj                             profile   rejected
https://www.tiktok.com/music/original-sound-7234567890         sound     rejected
https://www.tiktok.com/@mystery_jj/video/7234567890123456789   video     SELECTED
https://p16-sign.tiktokcdn-us.com/obj/.../abc.jpg              CDN       rejected
```

Sava ranks the list and picks the real content URL. Verified by
`CaptureDiagnostics.runSelectorSelfCheck()` — 8/8 passing, run on every Debug
launch. Passing only the first item would frequently save a profile link.

The `Link` field also accepts a single URL, so a one-URL screen works the same
way.

## Per platform

| Platform | Expected path | Screenshot taken? |
|---|---|---|
| TikTok | `direct_url` from Get What's On Screen | No |
| YouTube | `direct_url` if a URL is on screen, else `screenshot_resolution` | Only if no URL |
| Instagram | `direct_url` if a URL is on screen, else `screenshot_resolution` | Only if no URL |

Screenshot resolution is verified working for YouTube: a player screenshot
resolved to the exact video (`aircAruvnKk`) at confidence 1.00 by reading the
title and channel and matching against YouTube search.

For TikTok and Instagram the screenshot branch is a genuine fallback only — the
server can read the `@handle` and caption but has no public search to turn that
into an exact video id, so it reports honestly instead of guessing. In the
intended flow that branch should not be reached, because Get What's On Screen
supplies the URL.

## The simple setup (no Shortcut building)

For most people the full capture Shortcut is more than they need. The shortest
path that works:

```
Settings -> Action Button -> swipe to Shortcut -> Choose "Save to Sava"
```

Copy a link, press the button, done. The action reads the clipboard when it is
given nothing, which is exactly what the Action Button gives it.

The seven-action Shortcut above is worth building when you want to capture
*without* copying — it reads the foreground app instead. Both end at the same
intent and the same backend save path.

## Signed out, expired session, or offline

None of these lose the save.

When there is a usable link but no way to deliver it — the token is missing or
expired, the phone is offline, or the request timed out — the link is written to
the **App Group pending-save queue**, the same one the Share Extension writes to,
and the user is told:

```
Open Sava to finish saving
```

`AppShell` drains that queue on the next foreground. The queue is a file in the
shared container, written atomically, retried up to five times.

Deliberately the *existing* mechanism rather than a new one: it is already
crash-safe, already drained, and already tested. A second handoff path for
intents would be a parallel system with its own bugs.

With no link at all there is nothing worth keeping, so the honest answer is
`No link found` (or `Open Sava and sign in to start saving.`).

## Publishing a shared Shortcut (optional)

Sava can show a **Get Sava Shortcut** button that opens a prebuilt Shortcut you
publish. It is off until configured, because an iCloud share id only exists once
somebody presses Share on a real Shortcut — it cannot be predicted or generated,
and shipping a plausible-looking placeholder would give users a button that 404s.

### Build it

Shortcuts app → **+** → New Shortcut. Four actions, no third-party actions, no
API keys, no auth tokens:

```
1.  If  [Shortcut Input]  has any value
2.      Save to Sava
            field "Link or text"  <- variable: Shortcut Input
3.  Otherwise
4.      Get Clipboard
5.      Save to Sava
            field "Link or text"  <- variable: Clipboard
6.  End If
```

Name it exactly **Save to Sava**. Set its icon to something recognisable
(bookmark glyph, dark background).

The intent itself already falls back to the clipboard when given nothing, so
strictly the whole If/Otherwise is optional — a single **Save to Sava** action
with an empty field behaves identically. The branch is there so the Shortcut
also works when run *from* a share sheet, where Shortcut Input is populated.

The result dialog is the intent's own — `Saved to Sava`, `Already in your Sava
library`, `Open Sava to finish saving`, or `No link found`. Turn it off per
action with **Show When Run → Off** if you prefer it silent.

### Publish it

1. Long-press the Shortcut → **Share** → **Copy iCloud Link**.
2. The link looks like `https://www.icloud.com/shortcuts/<32-hex-id>`.
3. Put it in `ios/Info.plist` and `ios/Info-Release.plist` as
   `SAVA_SHARED_SHORTCUT_URL`, or export it in the scheme for a Debug build.

`AppConfig.sharedShortcutURL` validates it: https only, and the host must be
`icloud.com` or a subdomain. Only Apple can host a Shortcut, so accepting any
https URL would turn a plist typo into a tap that opens an arbitrary website
from a trusted-looking button.

When it is set, **Profile → Action Button** shows **Get Sava Shortcut** and the
steps start with installing it. When it is not, the same screen shows the native
App Shortcut instructions, which need no hosted asset and work on every install.

## Clipboard

Emergency fallback only. Read **exclusively** when there is no URL and no
screenshot, because touching the pasteboard shows a system paste banner. Not
part of the normal journey, and no user-facing message asks anyone to copy a
link.

## DEBUG diagnostics

Debug builds record every press. **Profile → Capture debug**, and the device
console under `[Sava capture]`:

| Field | Meaning |
|---|---|
| `types` | onscreen item types received (`URLs[4]`, `Screenshot`, `Text→URL[1]`) |
| `urlCount` | how many URLs arrived |
| `urls` | the actual URLs (first 4) |
| `selected` | which URL the ranker chose |
| `screenshotTaken` | yes/no |
| `platform` | detected platform |
| `path` | `direct_url` / `screenshot_resolution` / `clipboard_fallback` / `failed` |
| `reason`, `confidence` | resolver result when the screenshot branch ran |
| `outcome` | `saved` / `failed` |

Compiled out of Release entirely.

## Screenshot handling

Bytes are held in memory, uploaded for identification, and never written to the
Photos library or to disk on the server. Do **not** add a "Save to Photo Album"
action to the Shortcut.
