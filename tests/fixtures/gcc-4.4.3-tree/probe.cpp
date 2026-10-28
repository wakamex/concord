int f(const char* s, int n) { int len=0; while (s[len]) ++len; int sum=0; for(int i=0;i<=len;++i) sum += s[i]; return sum+n; }
