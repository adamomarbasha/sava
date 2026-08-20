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

### The two actions

Search the action library for **"Sava"** and you will see exactly two:

| Action | Field | Use in |
|---|---|---|
| **Save Link to Sava** | `Link` | the `Count > 0` branch |
| **Save Screenshot to Sava** | `Screenshot` | the Otherwise branch |

One field each — no optional clutter. They are separate actions because a
single Shortcut action instance carries one fixed parameter configuration; you
cannot swap which parameter is populated per branch.

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
