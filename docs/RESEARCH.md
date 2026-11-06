# What existing CAD programs teach

Read in November 2026. This is what shaped DraftWorx, not a survey. Sources are at the end.

## AutoCAD: the standard to meet on snapping

**What it gets right.** Running object snaps with shaped markers, tooltips and a magnet that pulls the cursor. Object snap tracking: hover over a
snap point without clicking and it's acquired, marked with a small plus. The cursor can then follow alignment lines from it, and clicking where two
lines meet places a point. Polar tracking and direct distance entry: point the cursor in a direction, type a length. These are why experienced people
are fast, and they're the bar.

**Where it costs people.**
- *The person manages the snaps.* Which modes are on is a setting. The advice is "avoid enabling everything: too many snaps increase mis-picks and
  slowdowns", and to keep a short list on and reach for one-shot overrides. So the program's ranking isn't trusted to pick well, and the person
  compensates by hand.
- *Tracking is a mode with hidden state.* It's a separate toggle, acquired points are removed by hovering over the same spot again, and it follows
  only horizontal and vertical unless a further option is set.
- *Annotation is the hard part.* Autodesk's own writer of the beginner's guide called scaling and layouts his "biggest challenge", with four main
  ways to annotate. On Autodesk's forum, people who use annotative objects speak well of them, but others find them "unwieldy" and are surprised by
  per-object "installed scales". Several say most people draw at 1:1 in model space and scale annotation by a factor anyway.
- *Dimension styles and command variety.* Many separate dimension commands and a large style dialog. (Newer versions have a single `DIM` command, which
  supports the idea below.)

## LibreCAD and QCAD: the open-source baseline

LibreCAD's manual shows what's missing as much as what's there. Snap modes are toggles with an optional exclusive mode, and restrictions (horizontal,
vertical, orthogonal) are separate. The snap page describes no object snap tracking, no polar tracking, and no perpendicular, tangent or extension snap;
tangent and perpendicular are separate *line commands*. It says intersection snapping "does not currently work for polylines". On its forum, someone
migrating from AutoCAD asks for exactly these: a perpendicular snap, directing the next segment at set angles, typing a bare length. Another user calls
switching to a separate tool for tangent and perpendicular "a bit of a mental interruption".

So precise, tracking-based drafting is the gap in open-source 2D CAD, and it's the right thing to compete on.

## SketchUp: why inference feels easy, and where it goes wrong

There's no snap menu: you hover over a thing, and inference offers the alignment, with colour and a tooltip saying what it found. The forum thread on
priority is the useful part. An inference "from a point" often beats an actual endpoint that's right there, which silently produces misaligned geometry.
People proposed checkboxes and priority lists. The better answer in that thread: "snapping should work as expected by default", and real points should
simply outrank alignments. DraftWorx takes that as a rule: **a point the geometry really has beats any point worked out from an alignment.**

## Rhino SmartTrack: the cleanest tracking design

Hover captures a "smart point" (for end, mid, centre and vertex), and the oldest drops off past a limit. Infinite tracking lines appear only when the cursor
lines up. Intersections of tracking lines snap. With one point active, a distance tooltip lets you type a distance from it ("Implied From"). Tapping Ctrl
captures any point, and tapping twice clears them; holding Alt hides guides for a moment. It's less a mode than a behaviour, and it's mostly on by default.
DraftWorx takes the hover capture, the typed distance from a tracked point, and a held key to suspend. (One visible cost: smart points skip knots and
intersections, because there'd be too many, which surprises people.)

## BricsCAD Quad: context without clutter

Hover over an object and a small menu near the cursor offers the most recently used commands for that type of object, with Tab to cycle through what's under
the cursor. It shows commands can come to the cursor rather than the cursor going to a ribbon. DraftWorx keeps the idea (right-click on a selection lists what
applies, and the panel shows only what's relevant) without a floating menu that appears on every hover.

## What this means for DraftWorx

| Finding | Decision |
|---|---|
| People are told to switch snaps off because too many mis-pick | All on by default, and the ranking is the product. Per-kind switches exist but you shouldn't need them. |
| Real points must beat alignments | A strict ranking: real features over worked-out points over tracking over "on the line". |
| Tracking is too hidden | No separate mode. Hovering acquires. What's acquired, and why a point was chosen, is always visible. |
| Typing a length is what makes people fast | Type at any point prompt, with no field to click. A length follows the cursor's direction, from the last point or a tracked one. |
| Tangent and perpendicular shouldn't be separate commands | They're candidates like any other, offered when there's a point to measure from. |
| Annotation scale is the great confusion | One scale per drawing. No paper space or viewports, and a file that has them keeps them. See ADR-5. |
| Many dimension commands | One Dimension tool, which reads what you point at. |
| DXF is how drawings move between programs | A first-class working format, and what we don't understand is kept. |

## Sources

- Autodesk, [Object Snap Tracking](https://help.autodesk.com/cloudhelp/2022/ENU/AutoCAD-DidYouKnow/files/GUID-6FB309A8-2696-4383-8277-950E9E2756A8.htm)
- Autodesk, [Drafting tab of the Options dialog](https://help.autodesk.com/cloudhelp/2026/ENU/AutoCAD-LT/files/GUID-33A0F8E8-9981-4111-AD73-128B20D7975C.htm)
- Novedge, [Optimizing AutoCAD object snaps](https://novedge.com/blogs/design-news/autocad-tip-optimizing-autocad-object-snaps)
- Cadnauseam, [The Hitchhiker's Guide to AutoCAD explained](https://www.cadnauseam.com/?p=1968)
- Autodesk forum, [Do you use annotative objects?](https://forums.autodesk.com/t5/autocad-forum/do-you-use-annotative-objects/m-p/12643935)
- LibreCAD manual, [Snaps](https://docs.librecad.org/en/latest/ref/snaps.html)
- LibreCAD forum, [What about basic lack of functionality](https://forum.librecad.org/What-about-basic-lack-of-functionality-td5718854.html)
- SketchUp forum, [Tweak inference priority](https://forums.sketchup.com/t/tweak-inference-priority/57854)
- McNeel, [SmartTrack and Guides](https://docs.mcneel.com/rhino/8/help/en-us/options/modeling_aids_smarttrack.htm)
- McNeel forum, [SmartTrack on knots and intersections](https://discourse.mcneel.com/t/smarttrack-on-knots-and-intersections/123883)
- Bricsys, [The Quad cursor menu](https://help.bricsys.com/en-us/document/bricscad/getting-started-with-bricscad/exploring-the-interface/the-quad-cursor-menu)
- ezdxf, [DXF file structure](https://ezdxf.readthedocs.io/en/stable/dxfinternals/filestructure.html)
