
;; Function int f(const char*, int) (_Z1fPKci)

;; 3 loops found
;;
;; Loop 0
;;  header 0, latch 1
;;  depth 0, outer -1
;;  nodes: 0 1 2 3 4 5 6 8 7
;;
;; Loop 2
;;  header 6, latch 8
;;  depth 1, outer 0
;;  nodes: 6 8
;;
;; Loop 1
;;  header 4, latch 3
;;  depth 1, outer 0
;;  nodes: 4 3
;; 2 succs { 4 }
;; 3 succs { 4 }
;; 4 succs { 3 5 }
;; 5 succs { 6 }
;; 6 succs { 8 7 }
;; 8 succs { 6 }
;; 7 succs { 1 }
Duplicating header of the loop 1 up to edge 4->3.


Registering new PHI nodes in block #9

Updating SSA information for statement D.2079_2 = (long unsigned int) len_1;

Updating SSA information for statement D.2080_26 = s_6(D) + D.2079_5;

Updating SSA information for statement D.2081_25 = *D.2080_7;

Updating SSA information for statement if (D.2081_8 != 0)



Registering new PHI nodes in block #3

Updating SSA information for statement len_10 = len_1 + 1;



Registering new PHI nodes in block #4

Updating SSA information for statement D.2079_5 = (long unsigned int) len_1;

Updating SSA information for statement D.2080_7 = s_6(D) + D.2079_5;

Updating SSA information for statement D.2081_8 = *D.2080_7;

Updating SSA information for statement if (D.2081_8 != 0)



Registering new PHI nodes in block #5



Registering new PHI nodes in block #6



Registering new PHI nodes in block #7



Registering new PHI nodes in block #8


SSA replacement table
N_i -> { O_1 ... O_j } means that N_i replaces O_1, ..., O_j

D.2079_2 -> { D.2079_5 }
len_3 -> { len_1 }
len_11 -> { len_1 }
D.2081_25 -> { D.2081_8 }
D.2080_26 -> { D.2080_7 }

Number of virtual NEW -> OLD mappings:       0
Number of real NEW -> OLD mappings:          5
Number of total NEW -> OLD mappings:         5

Number of virtual symbols: 0


Incremental SSA update started at block: 9

Number of blocks in CFG: 10
Number of blocks to update: 4 ( 40%)

Affected blocks: 3 4 5 9


Merging blocks 2 and 9
Merging blocks 3 and 4
Removing basic block 8
;; basic block 8, loop depth 1, count 0
;; prev block 6, next block 7
;; pred:
;; succ:       6 [100.0%]  (fallthru)
<bb 8>:
Invalid sum of incoming frequencies 0, should be 8200
goto <bb 6>;


Removing basic block 10
;; basic block 10, loop depth 1, count 0
;; prev block 2, next block 3
;; pred:
;; succ:       3 [100.0%]  (fallthru)
<bb 10>:
Invalid sum of incoming frequencies 0, should be 819


Removing basic block 11
;; basic block 11, loop depth 1, count 0
;; prev block 3, next block 5
;; pred:
;; succ:       3 [100.0%]  (fallthru)
<bb 11>:
Invalid sum of incoming frequencies 0, should be 8281
goto <bb 3>;


Scope blocks after cleanups:

{ Scope block #0

  { Scope block #0
    intD.2 lenD.2073;
    intD.2 sumD.2074;

    { Scope block #0
      intD.2 iD.2075;

    }

  }

}
int f(const char*, int) (const charD.3 * sD.2069, intD.2 nD.2070)
{
  intD.2 iD.2075;
  intD.2 sumD.2074;
  intD.2 lenD.2073;
  intD.2 D.2092;
  intD.2 D.2091;
  const charD.3 D.2090;
  const charD.3 * D.2089;
  long unsigned intD.7 D.2088;
  const charD.3 D.2081;
  const charD.3 * D.2080;
  long unsigned intD.7 D.2079;

  # BLOCK 2 freq:900
  # PRED: ENTRY [100.0%]  (fallthru,exec)
  [/out/probe.cpp : 1] D.2079_2 = 0;
  [/out/probe.cpp : 1] D.2080_26 = sD.2069_6(D) + D.2079_2;
  [/out/probe.cpp : 1] D.2081_25 = [/out/probe.cpp : 1] *D.2080_26;
  [/out/probe.cpp : 1] if (D.2081_25 != 0)
    goto <bb 3>;
  else
    goto <bb 4>;
  # SUCC: 3 [91.0%]  (true,exec) 4 [9.0%]  (false,exec)

  # BLOCK 3 freq:9100
  # PRED: 3 [91.0%]  (true,exec) 2 [91.0%]  (true,exec)
  # lenD.2073_11 = PHI <lenD.2073_10(3), 0(2)>
  [/out/probe.cpp : 1] lenD.2073_10 = lenD.2073_11 + 1;
  [/out/probe.cpp : 1] D.2079_5 = (long unsigned intD.7) lenD.2073_10;
  [/out/probe.cpp : 1] D.2080_7 = sD.2069_6(D) + D.2079_5;
  [/out/probe.cpp : 1] D.2081_8 = [/out/probe.cpp : 1] *D.2080_7;
  [/out/probe.cpp : 1] if (D.2081_8 != 0)
    goto <bb 3>;
  else
    goto <bb 4>;
  # SUCC: 3 [91.0%]  (true,exec) 4 [9.0%]  (false,exec)

  # BLOCK 4 freq:900
  # PRED: 3 [9.0%]  (false,exec) 2 [9.0%]  (false,exec)
  # lenD.2073_23 = PHI <lenD.2073_10(3), 0(2)>
  # SUCC: 5 [100.0%]  (fallthru)

  # BLOCK 5 freq:9100
  # PRED: 5 [90.1%]  (true,exec) 4 [100.0%]  (fallthru)
  # sumD.2074_27 = PHI <sumD.2074_18(5), 0(4)>
  # iD.2075_28 = PHI <iD.2075_19(5), 0(4)>
  [/out/probe.cpp : 1] D.2088_14 = (long unsigned intD.7) iD.2075_28;
  [/out/probe.cpp : 1] D.2089_15 = sD.2069_6(D) + D.2088_14;
  [/out/probe.cpp : 1] D.2090_16 = [/out/probe.cpp : 1] *D.2089_15;
  [/out/probe.cpp : 1] D.2091_17 = (intD.2) D.2090_16;
  [/out/probe.cpp : 1] sumD.2074_18 = D.2091_17 + sumD.2074_27;
  [/out/probe.cpp : 1] iD.2075_19 = iD.2075_28 + 1;
  [/out/probe.cpp : 1] if (iD.2075_19 <= lenD.2073_23)
    goto <bb 5>;
  else
    goto <bb 6>;
  # SUCC: 5 [90.1%]  (true,exec) 6 [9.9%]  (false,exec)

  # BLOCK 6 freq:900, starting at line 0
  # PRED: 5 [9.9%]  (false,exec)
  # sumD.2074_24 = PHI <sumD.2074_18(5)>
  [/out/probe.cpp : 1] D.2092_21 = sumD.2074_24 + nD.2070_20(D);
  return D.2092_21;
  # SUCC: EXIT [100.0%]

}
