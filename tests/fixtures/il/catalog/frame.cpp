// The function frame: no parameters, one, two, a return value, and a declaration of its own symbol. See test_il_catalog.py.
int g_a; int g_b;
void empty1() {}
int ret1() { return 7; }
int add2(int a, int b) { return a + b; }
void store1(int a) { g_a = a; }
int addlocal(int a, int b) { int c = a + b; return c; }
