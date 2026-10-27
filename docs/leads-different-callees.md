# different-callees leads

What the calls-constants sweep's `different-callees` rows hold: call sites where ours and the target each call a different function past the shared multiset. All 122 entries of one sweep (551 functions compared) are classified here; the tool artifact class is fixed in calls-constants, and a rerun of the sweep leaves 37. Each entry was checked against the target's disassembly in `orig/1.18-linux-amd64/Harvest`.

## Artifact fixed in calls-constants

85 of the 122 entries were inline-copy noise the pairing check could not see: the target calls a named out-of-line copy (list::_M_erase, __copy_m, CString<wchar_t>::assign, IGUIElement::D2, TArray ctor/dtor, __uninitialized_move_a, list<CHarvestEvent*>::D1, CScenario::addEvent) that no unit's delinked object defines, so `_body_calls` had no body to search. Each copy's calls were verified in the original (every one calls exactly the ours-side leftovers), and `original_refs` now reads each learned function's calls from the original executable, resolving them through the learned table and the PLT's dynamic names. Those entries now report as called-out-of-line/original-called instead of different-callees.

## Source leads

Ranked by how concrete the evidence is.

1. `ox::core::CString<char>::subString(int, int)`: an out-of-line copy emitted in `daisy/scene/CXFileReader.cpp` (ours 482B, target extent at 0x4528e0). The early-return path differs: the target calls `CString<char>::operator=(const CString&)` (0x450320) at 0x4529a5 where ours calls the `CString<char>(const char*)` constructor. The target's sequence `_Znam` (temporary buffer), `operator=`, `_ZdaPv` (temporary destruction), `ret` reads as `o = CString<T>("")` or an equivalent assignment of a CString value, not `return CString<T>("")`. The original's own C1<char> copy at 0x416760 calls only `_Znam`, so the operator= cannot be the inlined constructor: the original's header source assigns where ours constructs. CString.h is shared, so a correction needs the overlay gate over every dependent unit; candidate form is an assignment to a named local (`o = CString<T>(""); return o;` or the same expressed without an early return).

2. `CSceneManager::findMesh`: ours calls `_ZdaPv` where the target calls `_Znam` plus a `delete[]` thunk (sub_4c7b60, `mov (%rdi),%rdi; jmp _ZdaPv`). Weakest lead: the target's emitted copies may simply arrange the temporary vector differently (unit context), but an allocate-versus-delete pairing at an aligned site is also consistent with a small source difference in how the mesh entry list is handled. No concrete line-level form identified; listed for completeness.

## Unit context, not source differences

Every remaining entry was checked against the target's disassembly and is the same source compiled to a different emitted copy or an out-of-line call ours inlines. None has a line-level source fix.

- `IGUIElement::removeChild` in 22 GUI units: the target calls an out-of-line `IUnknown::drop` copy (0x4c6690, 35 bytes: refcount decrement then the virtual `call *0x8(%rax)`); ours inlines drop into `_ZdlPv`. The out-of-line copy is learned (symbols.tsv `manual:out-of-line-drop-copy-called-by-gui-units`) but emitted in no unit's span ours reproduces.
- `CGUIContextMenu::D2`: the target calls `IEventReceiver::D2` (0x5e37a0, a 34-byte thunk to 0x5e36f0) plus `_ZdaPv`/`_ZdlPv`; ours inlines the whole event-receiver destructor chain, leaving a residual `_Unwind_Resume` count.
- `CQ3LevelMesh::loadTextures`: `sub_574680` disassembles to a stride-8 vector reserve (`sar 3`, `lea rsi*8`, `_Znwm`), the same `vector<ITexture*>::reserve` ours calls, an unnamed copy the target emits for this unit.
- `CSceneCollisionManager::getCollisionPoint` and `collideWithWorld`: `sub_578c80` is `vector<CTriangle3d<float>>::reserve` (36-byte element stride). Resolved by `concord learn-calls` naming 0x578c80; the calls then name the same function on both sides.
- `CXFileReader` vector helpers: `sub_596e80` is the `vector<SXMaterial>::_M_insert_aux` element-copy insert; `sub_5946e0`/`sub_595140` are the SXAnimationSet move/copy helpers; `sub_597380` the SXAnimation element insert; `sub_594ae0` the SXSkinWeight element-wise copy (each calls `CString::operator=` at 0x450320 internally). Ours calls the same functions under their mangled names or inlines them into `_Znam`/`_ZdaPv`/`__uninitialized_copy_a`; the target emits its own copies in the unit's span.
- `CSceneManager::registerNodeForRendering` and `addMesh`: `sub_4c7b80` is `vector<DefaultNodeEntry>::_M_insert_aux` (0x10-stride insert); `sub_4c7b60` is a `delete[]` thunk; `sub_4c9940`/`sub_4ca050`/`sub_4ca590` are MeshEntry element copy/move helpers (0x18-stride, CString fields).
- `ox::algo::sort<DefaultNodeEntry>`: `sub_4c6fe0` is `__insertion_sort` (compares the +0x8 field over 0x10-stride elements) and `sub_4c8240` is `__introsort_loop` (0x10f threshold, median-of-3 pivot); ours additionally calls `__unguarded_linear_insert` out of line where the target's copies inline it.
- `addTerrainSceneNode` and `createSoftwareDriver`: ours calls the C1 constructor variant where the target calls the C2 twin (`CTerrainSceneNode` C2 0x4d0440 vs C1 0x4d0c90, `CVideoSoftware` C1 0x4f4320 vs C2 0x4f4e70). The twins' bodies are not byte-identical in the target, so this is copy-variant selection, a unit-context placement difference, not a naming artifact.

## Entries left after the fix

37 different-callees entries remain: 1 firm source lead (`subString`), 1 weak (`findMesh`), 2 resolved by item A's learned addresses, and the rest unit context as itemized above. The remaining sub_ callees are unnamed emitted copies of the same source functions; naming them would change the report's display, not the call-destination difference.
