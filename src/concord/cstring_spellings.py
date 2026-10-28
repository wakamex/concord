"""Bounded authored spellings of CString operations attributed by comparison-flips.

Each family changes one operator, anchored to its full declarator. The source
forms are hypotheses about value creation order, not reconstructed source.
"""

from concord.authored import HeaderFunctionEdit, Replacement

HEADER = "ox/core/CString.h"


def family(
    declarator: bytes, old: bytes, forms: dict[str, bytes]
) -> list[HeaderFunctionEdit]:
    return [
        HeaderFunctionEdit(name, HEADER, (Replacement(old, new, 1),), declarator)
        for name, new in forms.items()
    ]


ASSIGN = family(
    b"& operator=(const CString<T>& other)",
    b"""        const T* p = other.c_str();
        for (int i = 0; i < used; ++i, ++p)
            array[i] = *p;""",
    {
        "assign-while": b"""        const T* p = other.c_str();
        int i = 0;
        while (i < used)
        {
            array[i] = *p;
            ++i;
            ++p;
        }""",
        "assign-index": b"        for (int i = 0; i < used; ++i)\n            array[i] = other.array[i];",
        "assign-index-source": b"""        const T* p = other.c_str();
        for (int i = 0; i < used; ++i)
            array[i] = p[i];""",
        "assign-count-first": b"""        int count = used;
        const T* p = other.c_str();
        for (int i = 0; i < count; ++i, ++p)
            array[i] = *p;""",
        "assign-count-after": b"""        const T* p = other.c_str();
        int count = used;
        for (int i = 0; i < count; ++i, ++p)
            array[i] = *p;""",
        "assign-destination-first": b"""        T* destination = array;
        const T* p = other.c_str();
        for (int i = 0; i < used; ++i, ++p)
            destination[i] = *p;""",
        "assign-destination-after": b"""        const T* p = other.c_str();
        T* destination = array;
        for (int i = 0; i < used; ++i, ++p)
            destination[i] = *p;""",
        "assign-two-pointers": b"""        const T* p = other.c_str();
        T* destination = array;
        for (int i = 0; i < used; ++i, ++p, ++destination)
            *destination = *p;""",
        "assign-pointer-end": b"""        const T* p = other.c_str();
        T* destination = array;
        T* end = array + used;
        while (destination != end)
            *destination++ = *p++;""",
        "assign-index-before-source": b"""        int i = 0;
        const T* p = other.c_str();
        for (; i < used; ++i, ++p)
            array[i] = *p;""",
        "assign-increment-source-first": b"""        const T* p = other.c_str();
        for (int i = 0; i < used; ++p, ++i)
            array[i] = *p;""",
        "assign-mirrored-bound": b"""        const T* p = other.c_str();
        for (int i = 0; used > i; ++i, ++p)
            array[i] = *p;""",
        "assign-not-equal-bound": b"""        const T* p = other.c_str();
        for (int i = 0; i != used; ++i, ++p)
            array[i] = *p;""",
        "assign-preincrement-bound": b"""        const T* p = other.c_str();
        int i = -1;
        while (++i < used)
            array[i] = *p++;""",
    },
)

POINTER = family(
    b"& operator=(const B* c)",
    b"        for (int l = 0; l < len + 1; ++l)\n            array[l] = (T)c[l];",
    {
        "pointer-while": b"""        int l = 0;
        while (l < len + 1)
        {
            array[l] = (T)c[l];
            ++l;
        }""",
        "pointer-inclusive-bound": b"        for (int l = 0; l <= len; ++l)\n            array[l] = (T)c[l];",
        "pointer-mirrored-bound": b"        for (int l = 0; len + 1 > l; ++l)\n            array[l] = (T)c[l];",
        "pointer-used-bound": b"        for (int l = 0; l < used; ++l)\n            array[l] = (T)c[l];",
        "pointer-count-bound": b"        int count = len + 1;\n        for (int l = 0; l < count; ++l)\n            array[l] = (T)c[l];",
        "pointer-increment-length": b"        ++len;\n        for (int l = 0; l < len; ++l)\n            array[l] = (T)c[l];",
        "pointer-destination": b"""        T* destination = array;
        for (int l = 0; l < len + 1; ++l)
            destination[l] = (T)c[l];""",
        "pointer-source": b"""        const B* source = c;
        for (int l = 0; l < len + 1; ++l, ++source)
            array[l] = (T)*source;""",
        "pointer-two-pointers": b"""        const B* source = c;
        T* destination = array;
        for (int l = 0; l < len + 1; ++l)
            *destination++ = (T)*source++;""",
        "pointer-end": b"""        const B* source = c;
        T* destination = array;
        T* end = array + len + 1;
        while (destination != end)
            *destination++ = (T)*source++;""",
        "pointer-not-equal-bound": b"        for (int l = 0; l != len + 1; ++l)\n            array[l] = (T)c[l];",
        "pointer-preincrement-bound": b"        int l = -1;\n        while (++l < len + 1)\n            array[l] = (T)c[l];",
        "pointer-guarded-do": b"""        int l = 0;
        if (l < len + 1)
            do
            {
                array[l] = (T)c[l];
            } while (++l < len + 1);""",
    },
)

EQUAL = family(
    b"operator==(const CString<T>& other) const",
    b"""        for (int i1 = 0, i2 = 0; array[i1] && other.array[i2]; ++i1, ++i2)
            if (array[i1] != other.array[i2])
                return false;""",
    {
        "compare-reverse-condition": b"""        for (int i1 = 0, i2 = 0; other.array[i2] && array[i1]; ++i1, ++i2)
            if (array[i1] != other.array[i2])
                return false;""",
        "compare-reverse-equality": b"""        for (int i1 = 0, i2 = 0; array[i1] && other.array[i2]; ++i1, ++i2)
            if (other.array[i2] != array[i1])
                return false;""",
        "compare-reverse-initializers": b"""        for (int i2 = 0, i1 = 0; array[i1] && other.array[i2]; ++i1, ++i2)
            if (array[i1] != other.array[i2])
                return false;""",
        "compare-reverse-increments": b"""        for (int i1 = 0, i2 = 0; array[i1] && other.array[i2]; ++i2, ++i1)
            if (array[i1] != other.array[i2])
                return false;""",
        "compare-single-index": b"""        for (int i1 = 0; array[i1] && other.array[i1]; ++i1)
            if (array[i1] != other.array[i1])
                return false;""",
        "compare-while": b"""        int i1 = 0, i2 = 0;
        while (array[i1] && other.array[i2])
        {
            if (array[i1] != other.array[i2])
                return false;
            ++i1;
            ++i2;
        }""",
        "compare-temporaries-left-first": b"""        for (int i1 = 0, i2 = 0; array[i1] && other.array[i2]; ++i1, ++i2)
        {
            T left = array[i1];
            T right = other.array[i2];
            if (left != right)
                return false;
        }""",
        "compare-pointers-left-first": b"""        const T* left = array;
        const T* right = other.array;
        for (; *left && *right; ++left, ++right)
            if (*left != *right)
                return false;""",
        "compare-temporaries-right-first": b"""        for (int i1 = 0, i2 = 0; array[i1] && other.array[i2]; ++i1, ++i2)
        {
            T right = other.array[i2];
            T left = array[i1];
            if (left != right)
                return false;
        }""",
        "compare-pointers-right-first": b"""        const T* right = other.array;
        const T* left = array;
        for (; *left && *right; ++left, ++right)
            if (*left != *right)
                return false;""",
        "compare-eager-left-first": b"""        for (int i1 = 0, i2 = 0; ; ++i1, ++i2)
        {
            T left = array[i1];
            T right = other.array[i2];
            if (!left || !right)
                break;
            if (left != right)
                return false;
        }""",
        "compare-eager-right-first": b"""        for (int i1 = 0, i2 = 0; ; ++i1, ++i2)
        {
            T right = other.array[i2];
            T left = array[i1];
            if (!left || !right)
                break;
            if (left != right)
                return false;
        }""",
    },
)

LESS = family(
    b"operator<(const CString<T>& other) const",
    b"""        for (int i1 = 0, i2 = 0; array[i1] && other.array[i2]; ++i1, ++i2)
            if (array[i1] != other.array[i2])
                return (array[i1] < other.array[i2]);""",
    {
        "compare-reverse-condition": b"""        for (int i1 = 0, i2 = 0; other.array[i2] && array[i1]; ++i1, ++i2)
            if (array[i1] != other.array[i2])
                return (array[i1] < other.array[i2]);""",
        "compare-reverse-equality": b"""        for (int i1 = 0, i2 = 0; array[i1] && other.array[i2]; ++i1, ++i2)
            if (other.array[i2] != array[i1])
                return (array[i1] < other.array[i2]);""",
        "compare-reverse-initializers": b"""        for (int i2 = 0, i1 = 0; array[i1] && other.array[i2]; ++i1, ++i2)
            if (array[i1] != other.array[i2])
                return (array[i1] < other.array[i2]);""",
        "compare-reverse-increments": b"""        for (int i1 = 0, i2 = 0; array[i1] && other.array[i2]; ++i2, ++i1)
            if (array[i1] != other.array[i2])
                return (array[i1] < other.array[i2]);""",
        "compare-single-index": b"""        for (int i1 = 0; array[i1] && other.array[i1]; ++i1)
            if (array[i1] != other.array[i1])
                return (array[i1] < other.array[i1]);""",
        "compare-while": b"""        int i1 = 0, i2 = 0;
        while (array[i1] && other.array[i2])
        {
            if (array[i1] != other.array[i2])
                return (array[i1] < other.array[i2]);
            ++i1;
            ++i2;
        }""",
        "compare-temporaries-left-first": b"""        for (int i1 = 0, i2 = 0; array[i1] && other.array[i2]; ++i1, ++i2)
        {
            T left = array[i1];
            T right = other.array[i2];
            if (left != right)
                return left < right;
        }""",
        "compare-pointers-left-first": b"""        const T* left = array;
        const T* right = other.array;
        for (; *left && *right; ++left, ++right)
            if (*left != *right)
                return *left < *right;""",
        "compare-temporaries-right-first": b"""        for (int i1 = 0, i2 = 0; array[i1] && other.array[i2]; ++i1, ++i2)
        {
            T right = other.array[i2];
            T left = array[i1];
            if (left != right)
                return left < right;
        }""",
        "compare-pointers-right-first": b"""        const T* right = other.array;
        const T* left = array;
        for (; *left && *right; ++left, ++right)
            if (*left != *right)
                return *left < *right;""",
        "compare-eager-left-first": b"""        for (int i1 = 0, i2 = 0; ; ++i1, ++i2)
        {
            T left = array[i1];
            T right = other.array[i2];
            if (!left || !right)
                break;
            if (left != right)
                return left < right;
        }""",
        "compare-eager-right-first": b"""        for (int i1 = 0, i2 = 0; ; ++i1, ++i2)
        {
            T right = other.array[i2];
            T left = array[i1];
            if (!left || !right)
                break;
            if (left != right)
                return left < right;
        }""",
        "compare-mirrored-result": b"""        for (int i1 = 0, i2 = 0; array[i1] && other.array[i2]; ++i1, ++i2)
            if (array[i1] != other.array[i2])
                return (other.array[i2] > array[i1]);""",
    },
)


def registered() -> dict[tuple[str, str], list[HeaderFunctionEdit]]:
    return {
        (
            "daisy/gui/CGUIFileOpenDialog.cpp",
            "_ZN5daisy3gui18CGUIFileOpenDialog7OnEventERKN2ox5event6SEventE",
        ): ASSIGN,
        (
            "ox/core/CStringFunctions.cpp",
            "_ZN2ox4core16CStringFunctions18millisecondsToWideEfb",
        ): POINTER,
        (
            "ox/algo/CBase64url.cpp",
            "_ZN2ox4algo10CBase64url7encode2ERNS_4core7CStringIcEEPNS_2io9IReadFileE",
        ): POINTER,
        (
            "HarvestFull/harvest/gui/CHighscoreScreen.cpp",
            "_ZN7harvest3gui16CHighscoreScreen6updateEf",
        ): POINTER,
        (
            "daisy/io/CZipFileList.cpp",
            "_ZN5daisy2io12CZipFileListC1EPNS0_10CZipReaderEPKcS5_",
        ): EQUAL,
        (
            "daisy/io/CFileSystem.cpp",
            "_ZN5daisy2io11CFileSystem21intern_resolveAliasesEPKc",
        ): LESS,
    }
