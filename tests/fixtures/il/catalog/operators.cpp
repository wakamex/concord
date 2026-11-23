int g_x; int g_y; int* g_p; char g_c; short g_s; float g_f; double g_d; unsigned g_u;
struct C { int m; int n; };
C* g_q;
int bin_add(int a, int b) { return a + b; }
int bin_sub(int a, int b) { return a - b; }
int bin_mul(int a, int b) { return a * b; }
int bin_div(int a, int b) { return a / b; }
int bin_mod(int a, int b) { return a % b; }
int bin_and(int a, int b) { return a & b; }
int bin_or(int a, int b) { return a | b; }
int bin_xor(int a, int b) { return a ^ b; }
int bin_shl(int a, int b) { return a << b; }
int bin_shr(int a, int b) { return a >> b; }
int bin_eq(int a, int b) { return a == b; }
int bin_ne(int a, int b) { return a != b; }
int bin_lt(int a, int b) { return a < b; }
int bin_le(int a, int b) { return a <= b; }
int bin_gt(int a, int b) { return a > b; }
int bin_ge(int a, int b) { return a >= b; }
int bin_land(int a, int b) { return a && b; }
int bin_lor(int a, int b) { return a || b; }
int un_neg(int a) { return -a; }
int un_not(int a) { return !a; }
int un_inv(int a) { return ~a; }
void asg_pluseq(int a) { g_x += a; }
void pasg_pluseq(int* p, int a) { *p += a; }
void asg_minuseq(int a) { g_x -= a; }
void pasg_minuseq(int* p, int a) { *p -= a; }
void asg_muleq(int a) { g_x *= a; }
void pasg_muleq(int* p, int a) { *p *= a; }
void asg_andeq(int a) { g_x &= a; }
void pasg_andeq(int* p, int a) { *p &= a; }
void asg_oreq(int a) { g_x |= a; }
void pasg_oreq(int* p, int a) { *p |= a; }
void asg_xoreq(int a) { g_x ^= a; }
void pasg_xoreq(int* p, int a) { *p ^= a; }
void asg_shleq(int a) { g_x <<= a; }
void pasg_shleq(int* p, int a) { *p <<= a; }
void asg_shreq(int a) { g_x >>= a; }
void pasg_shreq(int* p, int a) { *p >>= a; }
void postinc() { g_x++; }
void preinc() { ++g_x; }
void postdec() { g_x--; }
int ld_global() { return g_x; }
int ld_global_y() { return g_y; }
int ld_ptr(int* p) { return *p; }
int ld_member(C* p) { return p->n; }
int ld_gmember() { return g_q->n; }
int* addr_glob() { return &g_x; }
int addr_local(int a) { int* p = &a; return *p; }
void store_glob(int a) { g_x = a; }
void store_zero() { g_x = 0; }
void store_ptr(int* p) { *p = 5; }
void store_member(C* p, int a) { p->m = a; }
int idx_read(int* p, int i) { return p[i]; }
int cast_char(int a) { return (char)a; }
int cast_short(int a) { return (short)a; }
unsigned cast_uint(int a) { return (unsigned)a; }
int* cast_ptr(int a) { return (int*)a; }
int ld_char() { return g_c; }
int ld_short() { return g_s; }
unsigned ld_uint() { return g_u; }
float ld_float() { return g_f; }
double ld_double() { return g_d; }
float fadd(float a, float b) { return a + b; }
double dadd(double a, double b) { return a + b; }
int cond(int a, int b) { return a > b ? a : b; }
int comma(int a) { return (a, 3); }
int sizeofx() { return sizeof(int); }
int nested(int a, int b, int c) { return (a + b) * c; }
int consts() { return 1000 + 70000; }
int negconst() { return -1234; }
int bigconst() { return 0x12345678; }
