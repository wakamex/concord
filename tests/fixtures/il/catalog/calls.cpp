// Calls, constructors and destructors, and calls through a pointer. One function per construct; see test_il_catalog.py.
struct C { int m; C(int a) : m(a) {} ~C() {} void Set(int v) { m = v; } int Get() { return m; } };
int callfree(int a, int b);
int g_x; int g_y; C* g_p;
int c_call2(int a, int b) { return callfree(a, b); }
void c_mset(C* p, int a) { p->Set(a); }
void c_mset0(C* p) { p->Set(3); }
int c_get(C* p) { return p->Get(); }
void c_ctor() { C c(5); }
int callee2(int x) { return x + 1; }
int f_fnaddr() { int (*p)(int); p = callee2; return p(1); }
