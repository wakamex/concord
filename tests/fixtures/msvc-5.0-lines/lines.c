/* Compiled with Microsoft Visual C++ 5.0, cl /nologo /c /O2 /Zd, as the fixture for
   COFF line records: each function's records count lines from its opening line. */
extern int scale(int value);

int first(int a, int b)
{
    int sum = a + b;
    if (sum < 0)
        return scale(sum);
    return sum;
}

int second(int a)
{
    a = scale(a);
    a = scale(a + 1);
    return a;
}
