;; Function traced (_ZN2ox4core16CStringFunctions18millisecondsToWideEfb)

  # BLOCK 153 freq:4277
  # PRED: 153 [91.0%]  (true,exec) 152 [91.0%]  (true,exec)
  # lenD.27349_63 = PHI <lenD.27349_676(153), 0(152)>
  # cD.27346_62 = PHI <cD.27346_678(153), &"."[0](152)>
  [src/ox/core/CString.h : 148] lenD.27349_676 = lenD.27349_63 + 1;
  [src/ox/core/CString.h : 149] cD.27346_678 = cD.27346_62 + 4;
  [src/ox/core/CString.h : 146] D.27355_679 = *cD.27346_678;
  [src/ox/core/CString.h : 146] if (D.27355_679 != 0)
    goto <bb 153>;
  else
    goto <bb 154>;
  # SUCC: 153 [91.0%]  (true,exec) 154 [9.0%]  (false,exec)

  # BLOCK 154 freq:423
  # PRED: 153 [9.0%]  (false,exec) 152 [9.0%]  (false,exec)
  # lenD.27349_929 = PHI <lenD.27349_676(153), 0(152)>
  [src/ox/core/CString.h : 156] D.27356_681 = lenD.27349_929 + 1;
  [src/ox/core/CString.h : 156] D.26237.usedD.8813 = D.27356_681;
  [src/ox/core/CString.h : 156] D.26237.allocatedD.8812 = D.27356_681;
  [src/ox/core/CString.h : 157] D.27358_684 = (long unsigned intD.7) D.27356_681;
  [src/ox/core/CString.h : 157] D.27359_685 = D.27358_684 * 4;
  [src/ox/core/CString.h : 157] D.27360_686 = operator new []D.2015 (D.27359_685);
  # SUCC: 155 [100.0%]  (fallthru,exec) 223 (ab,eh,exec)

  # BLOCK 156 freq:4277
  # PRED: 156 [90.1%]  (true,exec) 232 [100.0%]  (fallthru)
  # lD.27351_123 = PHI <lD.27351_697(156), lD.27351_756(232)>
  [src/ox/core/CString.h : 160] D.27362_690 = (long unsigned intD.7) lD.27351_123;
  [src/ox/core/CString.h : 160] D.27363_691 = D.27362_690 * 4;
  [src/ox/core/CString.h : 160] D.27364_692 = D.27361_687 + D.27363_691;
  [src/ox/core/CString.h : 160] D.27365_695 = &"."[0] + D.27363_691;
  [src/ox/core/CString.h : 160] D.27366_696 = *D.27365_695;
  [src/ox/core/CString.h : 160] *D.27364_692 = D.27366_696;
  [src/ox/core/CString.h : 159] lD.27351_697 = lD.27351_123 + 1;
  [src/ox/core/CString.h : 159] if (lD.27351_697 <= lenD.27349_929)
    goto <bb 156>;
  else
    goto <bb 157>;
  # SUCC: 156 [90.1%]  (true,exec) 157 [9.9%]  (false,exec)

  # BLOCK 209 freq:137
  # PRED: 208 [29.0%]  (true,exec)
  [src/ox/core/CString.h : 321] amountD.27533_870 = D.27497_866 + 1;
  [src/ox/core/CString.h : 533] old_arrayD.27532_909 = resultD.26218.arrayD.8811;
  [src/ox/core/CString.h : 535] D.27546_910 = (long unsigned intD.7) amountD.27533_870;
  [src/ox/core/CString.h : 535] D.27545_911 = D.27546_910 * 4;
  [src/ox/core/CString.h : 535] D.27544_912 = operator new []D.2015 (D.27545_911);
  # SUCC: 210 [100.0%]  (fallthru,exec) 223 (ab,eh,exec)

  # BLOCK 210 freq:137, starting at line 0
  # PRED: 209 [100.0%]  (fallthru,exec)
  [src/ox/core/CString.h : 535] D.27543_913 = (wchar_tD.43 *) D.27544_912;
  [src/ox/core/CString.h : 535] resultD.26218.arrayD.8811 = D.27543_913;
  [src/ox/core/CString.h : 536] resultD.26218.allocatedD.8812 = amountD.27533_870;
  [src/ox/core/CString.h : 538] D.27542_914 = resultD.26218.usedD.8813;
  amountD.27533_986 = MIN_EXPR <amountD.27533_870, D.27542_914>;
  [src/ox/core/CString.h : 539] if (amountD.27533_986 > 0)
    goto <bb 211>;
  else
    goto <bb 212>;
  # SUCC: 211 [91.0%]  (true,exec) 212 [9.0%]  (false,exec)

  # BLOCK 212 freq:137
  # PRED: 211 [9.0%]  (false,exec) 210 [9.0%]  (false,exec)
  [src/ox/core/CString.h : 542] D.27542_928 = resultD.26218.usedD.8813;
  [src/ox/core/CString.h : 542] if (amountD.27533_870 < D.27542_928)
    goto <bb 213>;
  else
    goto <bb 214>;
  # SUCC: 213 [50.0%]  (true,exec) 214 [50.0%]  (false,exec)

  # BLOCK 213 freq:68
  # PRED: 212 [50.0%]  (true,exec)
  [src/ox/core/CString.h : 543] resultD.26218.usedD.8813 = amountD.27533_870;
  # SUCC: 214 [100.0%]  (fallthru,exec)

  # BLOCK 232 freq:423
  # PRED: 155 [100.0%]  (fallthru,exec)
  # lD.27351_756 = PHI <0(155)>
  goto <bb 156>;
  # SUCC: 156 [100.0%]  (fallthru)
