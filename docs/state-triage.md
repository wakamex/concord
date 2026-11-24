# Which open gruntz-decomp functions need source work, and which only unit state

Question: of the open functions in [gruntz-decomp](https://github.com/sushi-shi/gruntz-decomp), which already have the right source and miss only because of the compiler's unit state, and which need source work? Microsoft Visual C++ 5.0's code for some functions depends on how many declarations precede them in the translation unit, so a function can be right and still score below 100.

Result: unit state decides little of what remains. Of 442 functions below 100 in 145 units, 19 (13.6 KB) become exact in some unit state, 7 (9.4 KB) rise but never reach exact, and 416 (452 KB) stay where they are in every state tried, so they need source work. Five units can be made fully exact by a single state.

`concord state-triage` measured this. For every unit with an open function, it compiled the unit with 1 to 7 inert declarations (`typedef int concord_state_pad_K_I;`, since cl 5.0 counts typedefs and extern variables in the declaration count these functions depend on, and enumerators not) inserted before each `#include` line of the unit's own source and after the last one, and scored every function against an unpadded compile of the same source made the same way. The pads are a diagnostic and are never written to the checkout or offered as a change. It ran on the clean checkout at gruntz-decomp 8c3a113ca.

## Classes

| Class | Meaning | Functions | Bytes |
| --- | --- | ---: | ---: |
| state | exact in at least one padded state, so its source is already right | 19 | 13,581 |
| improves | rises by at least 0.5 in some state but never reaches exact | 7 | 9,379 |
| source | no state tried changes it enough | 416 | 452,382 |

Units where one state makes every open function exact: `aniplayer`, `gamelevelmove`, `imageset3g`, `sbi_image`, `sbi_imagesetani`.

## The state functions

| Unit | Function | Bytes | Now | In its best state |
| --- | --- | ---: | ---: | ---: |
| lightfxrender | `CMinimap::BuildMiniatureMasterzPalette` | 2,383 | 99.85 | 100 |
| lightfxrender | `CMinimap::BuildSpacePalette` | 2,342 | 99.48 | 100 |
| lightfxrender | `CMinimap::BuildHoneyPalette` | 2,240 | 97.87 | 100 |
| lightfxrender | `CMinimap::BuildGruntziclezPalette` | 2,130 | 96.80 | 100 |
| lightfxrender | `CMinimap::BuildHighOnSweetzPalette` | 2,085 | 96.06 | 100 |
| gamelevelmove | `CGameLevel::BroadPhase` | 441 | 99.82 | 100 |
| levelplane | `CLevelPlane::Load` | 320 | 99.98 | 100 |
| levelplane | `CLevelPlane::Save` | 308 | 99.98 | 100 |
| ddsurface | `CDDSurface::Blit168` | 294 | 99.81 | 100 |
| sbi_imagesetani | `CSBI_ImageSetAni::Render` | 225 | 99.73 | 100 |
| statusbartabbuilders | `CSBI_GruntMachine::Render` | 196 | 99.54 | 100 |
| spriteloaders | `CLevelTimer::AddTime` | 163 | 99.82 | 100 |
| levelplane | `CLevelPlane::DeactivateDistantObjects` | 112 | 99.93 | 100 |
| sbi_imageset | `CSBI_ImageSet::Render` | 94 | 87.84 | 100 |
| aniplayer | `CAniPlayer::RenderCel` | 84 | 77.15 | 100 |
| imagepool | `CDib::FillRect` | 75 | 66.44 | 100 |
| sbi_image | `CSBI_Image::Render` | 69 | 74.04 | 100 |
| imageset3g | `CPixelTileImageSet::GetStride` | 11 | 99.50 | 100 |
| fadersine | `CFaderSine::GetFrameCount` | 9 | 99.50 | 100 |

The palette builders were known to depend on the count of declarations before six globals in `PixelShift.h`, with period 8; padding before the unit's includes moves the same count, which is why they appear here without header cut points. Their siblings `BuildRockyRoadzPalette`, `BuildTropiczPalette` and `BuildHighRollerzPalette` rise to 99.38 to 99.92 and are `improves`: they also have a source difference.

## What it changes

Lane work goes to the 416 `source` functions, largest first; none of them is waiting on unit state. The 19 `state` functions already have the right source and need an honest way to reach their state, such as declarations the original sources had, which belongs to the last step, when the rest of each unit is exact; reaching their state now would move again with every later fix.

## Limits

Cut points inside headers were not tried for the whole checkout (`--headers` exists but costs one compile per cut point per offset in every header a unit includes), and offsets were limited to 1 to 7. A function classed `source` could still respond to a pad inside a header or at a larger offset, so the class is a measurement of these states, not a proof that state never matters for it.
