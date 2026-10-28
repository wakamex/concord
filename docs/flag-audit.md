# Harvest compiler and flag audit

The tested options do not explain the comparison flips wholesale or make any of the five selected functions exact. `-O3` moves nine of the tower base constructor's 25 differing string-inlining decisions toward the target, but leaves 16 differing decisions and makes six previously exact functions in that unit inexact. Disabling strict aliasing makes one `CFileSystem` tree helper exact while losing seven existing matches. Neither result establishes a usable flag set for a unit. The comparison compiles five unchanged translation units with the pinned compiler, uses Harvest's matcher for exactness and [objdiff](https://github.com/encounter/objdiff) for function scores, and retains every scored function and exact-function change in the [compressed evidence](../results/flag-audit-e86091f.json.gz).

## Compiler and original build evidence

The target executable's `.comment` contains the single string `GCC: (Ubuntu 4.4.3-4ubuntu5) 4.4.3`. Its recorded executable hash matches the file, and its pinned [GNU Compiler Collection (GCC)](https://gcc.gnu.org/) package and compiler version match that string. The section does not record options, and the executable contains no recorded compiler command-line section. The recorded build information dates the Linux 1.18 link to May 2012.

The pinned compiler's `-O2 -###` output passes `-mtune=generic`, `-fstack-protector` and `-D_FORTIFY_SOURCE=2` to `cc1plus`; including `features.h` reports `__USE_FORTIFY_LEVEL=2`. The target imports `__stack_chk_fail` and several fortified functions including `__printf_chk` and `__memcpy_chk`. These imports establish that the binary contains protected and fortified code, but do not establish the flags used for every unit. `-U_FORTIFY_SOURCE` changes the tested compiler's fortify level to zero. `-fexpensive-optimizations` and strict aliasing are already enabled at `-O2`; the former is also enabled at `-O3`.

The bundled Irrlicht 0.7 Linux makefile sets the include path and `IRRLICHT_EXPORTS`, but specifies no optimization level. The recovered sources used here do not reference `IRRLICHT_EXPORTS`. The makefile therefore does not establish an `-O3` flag for this game.

## Unit scores and exact-function changes

Each cell gives `representative score; exact functions (lost/gained)`. Scores are percentages. The ZIP and tower cells give complete and base constructor scores, C1 and C2 respectively. Exact counts and identities come from Harvest's matcher; losses and gains compare function identities with the same unit's baseline. These are emitted unit copies, so they are not the whole build's count of distinct target functions. The score for Maya at `-mtune=opteron` uses the freshly delinked baseline target because delinking that variant fails on a truncated range; the matcher still supplies its exact count. That fallback can score only functions present in the baseline target.

| Extra flags | ZIP C1/C2 | File system alias resolution | Maya animate | Play state new game | Tower C1/C2 |
| --- | --- | --- | --- | --- | --- |
| Baseline `-O2` | 99.99029/99.99029; 16 (0/0) | 99.98579; 48 (0/0) | 99.82095; 40 (0/0) | 99.77819; 75 (0/0) | 80.46963/87.70007; 46 (0/0) |
| `-O3` | 52.45728/52.45728; 15 (1/0) | 99.23011; 34 (14/0) | 99.82095; 38 (2/0) | 73.59150; 53 (22/0) | 80.46963/92.03954; 40 (6/0) |
| `-O3 -fexpensive-optimizations` | 52.45728/52.45728; 15 (1/0) | 99.23011; 34 (14/0) | 99.82095; 38 (2/0) | 73.59150; 53 (22/0) | 80.46963/92.03954; 40 (6/0) |
| `-g` | 99.99029/99.99029; 16 (0/0) | 99.98579; 48 (0/0) | 99.82095; 40 (0/0) | 99.77819; 75 (0/0) | 80.46963/87.70007; 46 (0/0) |
| `-g0` | 99.99029/99.99029; 16 (0/0) | 99.98579; 48 (0/0) | 99.82095; 40 (0/0) | 99.77819; 75 (0/0) | 80.46963/87.70007; 46 (0/0) |
| `-fno-strict-aliasing` | 96.06408/96.21456; 15 (1/0) | 99.98579; 42 (7/1) | 56.24005; 33 (7/0) | 89.09982; 42 (33/0) | 79.99880/80.66035; 38 (8/0) |
| `-fno-stack-protector` | 99.99029/99.99029; 16 (0/0) | 99.98579; 47 (1/0) | 99.82095; 40 (0/0) | 99.77819; 73 (2/0) | 80.46963/87.70007; 46 (0/0) |
| `-U_FORTIFY_SOURCE` | 99.99029/99.99029; 16 (0/0) | 99.98579; 48 (0/0) | 99.82095; 40 (0/0) | 99.77819; 75 (0/0) | 80.46963/87.70007; 46 (0/0) |
| `-mtune=generic` | 99.99029/99.99029; 16 (0/0) | 99.98579; 48 (0/0) | 99.82095; 40 (0/0) | 99.77819; 75 (0/0) | 80.46963/87.70007; 46 (0/0) |
| `-mtune=core2` | 78.06699/78.06699; 11 (5/0) | 77.56108; 16 (32/0) | 67.70955; 24 (16/0) | 89.19408; 26 (49/0) | 68.77000/65.71975; 21 (25/0) |
| `-mtune=opteron` | 88.82427/88.82427; 12 (4/0) | 94.23296; 23 (25/0) | 79.65782; 26 (14/0) | 97.01479; 40 (35/0) | 79.74053/81.31924; 29 (17/0) |

The compiler rejects `-mtune=atom` with `bad value (atom) for -mtune= switch` in all five units, so it has no score. `-O3` and `-O3 -fexpensive-optimizations` produce identical object hashes for each unit. `-g` preserves all executable-section bytes and normalized executable relocations in these five units, and all their function scores and exact identities remain unchanged. The debug objects contain debug sections and have different whole-object hashes.

The ZIP constructors and file system alias resolver remain inexact in every supported variation, so none can fix the entire comparison-flip class. Other class members were not individually screened. Maya's `animate` remains inexact under all variations. In `initializeNewGame`, the target's `lea` at offset `0x43` remains an `add` in every variation, even where scheduling moves it to another offset.

The tower constructors each contain 147 measured unique string literal sites. For C1, 23 inline decisions differ from the target under both `-O2` and `-O3`. For C2, the baseline has 12 sites inlined only in the candidate and 13 only in the target; `-O3` reduces the former to three and leaves the latter at 13. The six exact functions lost under `-O3` are `getInfoString`, `isForwardingToThis`, `handleRightClickAction`, `handleDoubleClickSelection`, `handleSelectionDraggedToEntity` and `handleSelectionDraggedToNothing`. The `-fno-strict-aliasing` gain is the emitted `std::_Rb_tree` string-to-string `_M_insert_` copy at target address `0x531760`; it does not correct `intern_resolveAliases`, and the same unit loses seven exact functions. The target's retained template copy need not originate from that unit.

## Inputs and reproduction

The checkout is Harvest `e86091f046dd0e70de13c5856305408e1b579a78`. The container image is `b94ef6033199879fb811edc1d642923532b9213f245eb081646157559e3d45dd`, the package manifest hash is `e379f2252a8cdda9e94b71c872e83c863e7e7961180fa62ea95e8f763fafaf5e`, and the target executable hash is `ac381fa044f6009e09a29e1e221a51e72e65f0772edd164083857dda863a426a`. All hashes are SHA-256. The [objdiff](https://github.com/encounter/objdiff) executable is version 3.8.1, hash `c8290281e82114bcc1a06ff73061110d3902a177822e750337de2537188e358f`.

The flags file hash is `9ebb8c16c904108ddb5cbaa50345a733d400ee2bd3994c72e5545e676fb03ad8`; the unit and symbol configuration hashes are `fb93f20b48037862b2815dba879eed0da5c6f0f6f0237fe0c8e1affc7e9c9c38` and `eee6d7bb4ffa45d3ab3c9078b3fe841606b867ae1153c8d0402044fffd1aa5d8`. The selected source hashes, in table order, are `a0edbdcfce7f76690241be71a98f9d59bd0d74239d9a6369a9489e0d72245e9f`, `2affce18c6090698f6ee1ddad582e3c8a92f0815529873419cad1490d1f51ad6`, `a76cf74f3b79c9c7dbb385993f90cd6436cd74d306bc21fa531266f8a9400533`, `3c46780ce5968f1710177713e197451d82c2d1113a00737d67214c0a3ae7c1de` and `d046e018dc33b514876c8007760b8b5a59d6b829886829e5406ed48b4d6aa150`. The evidence records the batch duration, each object hash, and whether its executable sections and relocations agree with the baseline.

With [uv](https://docs.astral.sh/uv/), the audited options can be reproduced after `hv match` with:

```sh
env -u XDG_DATA_HOME CONCORD_JOBS=2 uv --no-config run --locked concord flag-audit \
  --harvest HARVEST_CHECKOUT \
  --unit daisy/io/CZipFileList.cpp \
  --unit daisy/io/CFileSystem.cpp \
  --unit daisy/scene/CCameraMayaSceneNode.cpp \
  --unit HarvestFull/harvest/states/CPlayState.cpp \
  --unit HarvestFull/harvest/entity/CDefenseTowerEntity.cpp \
  --variant 'o3=-O3' \
  --variant 'o3-expensive=-O3 -fexpensive-optimizations' \
  --variant 'debug=-g' --variant 'no-debug=-g0' \
  --variant 'no-strict-aliasing=-fno-strict-aliasing' \
  --variant 'no-stack-protector=-fno-stack-protector' \
  --variant 'no-fortify=-U_FORTIFY_SOURCE' \
  --variant 'tune-generic=-mtune=generic' \
  --variant 'tune-core2=-mtune=core2' \
  --variant 'tune-opteron=-mtune=opteron' \
  --save /tmp/flag-audit.json
```

The command measures options per selected unit without editing the build configuration. There is no whole-build flag verdict, and `flags.json` retains the baseline flags. Concord validation uses `uv --no-config run --locked python -m unittest`, `uv --no-config run --locked ruff check`, and the Harvest-backed flag tests with `CONCORD_HARVEST` set to the checkout.
