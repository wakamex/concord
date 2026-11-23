// Members, arrays, pointers, wide constants and locals with members. One function per construct; see test_il_catalog.py.
struct S { int a[4]; int m; };
int g_arr[10]; int g_i; S gs; S* gp; int g_z;
int f_idx_member(S* s, int i) { return s->a[i]; }
int f_idx_const(S* s) { return s->a[2]; }
int f_gidx(int i) { return g_arr[i]; }
void f_wide_member(S* s) { s->m = 1234; }
void f_wide_ptr(int* p) { *p = 1234; }
void f_wide_glob() { g_z = 1234; }
int f_obj() { S s; s.m = 3; return s.m; }
void f_addr_gs() { gp = &gs; }
int f_short(short a) { return a + 1; }
