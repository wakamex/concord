// Independent reproducer for a finite unit budget shared by equal-cost calls.
struct Text
{
    char* data;
    unsigned length;
    Text(const char* source) : data(0), length(0) { assign(source); }
    void assign(const char* source)
    {
        unsigned n = 0;
        while (source[n]) ++n;
        char* replacement = new char[n + 1];
        for (unsigned i = 0; i <= n; ++i) replacement[i] = source[i];
        delete[] data;
        data = replacement;
        length = n;
    }
    ~Text() { delete[] data; }
};

struct Box
{
    Text name, text, portrait, sound;
    Box(const char* value) : name(""), text(value), portrait(""), sound("") {}
    virtual ~Box() {}
};

extern void consume(Box*);

void credits()
{
    consume(new Box("First"));
    consume(new Box("Second"));
    consume(new Box("Third"));
    consume(new Box("Fourth"));
    consume(new Box("Fifth"));
    consume(new Box("Sixth"));
}
