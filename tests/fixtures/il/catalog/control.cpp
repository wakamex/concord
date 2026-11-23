// Loops, branches, returns and short-circuit conditions. One function per construct; see test_il_catalog.py.
int g_x; int g_y;
void c_loop(int n) { for (int i = 0; i < n; i++) g_x += i; }
void c_while(int n) { while (n > 0) { n = n - 1; } }
void c_if(int a) { if (a > 2) g_x = 1; else g_y = 2; }
int branch_or(int a, int b) { if (a == 2 || b == 3) return 1; return 0; }
int branch_and(int a, int b) { if (a == 2 && b == 3) return 1; return 0; }
int branch_not(int a) { if (!a) return 1; return 0; }
void c_ret_void(int a) { if (a) return; g_x = 9; }
int c_switch(int a) { switch (a) { case 1: return 5; case 2: g_x = 3; break; default: g_y = 4; } return 0; }
