/* c2wrap: stand in for MSVC 5.0's c2.exe through cl's /B2 option, copy the intermediate-language
   files cl hands the code generator into the directory named by C2CAPTURE, with the flags cl passed
   (in MSC_CMD_FLAGS) as flags.txt, then run the real c2.exe named by C2REAL. Built by
   concord's gruntz driver with the checkout's own cl. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <process.h>

int main(int argc, char **argv) {
    const char *capture = getenv("C2CAPTURE");
    const char *real = getenv("C2REAL");
    const char *flags = getenv("MSC_CMD_FLAGS");
    const char *il = flags ? strstr(flags, "-il ") : NULL;
    char prefix[MAX_PATH], dir[MAX_PATH], pattern[MAX_PATH], from[MAX_PATH], to[MAX_PATH];
    WIN32_FIND_DATA found;
    HANDLE h;
    FILE *out;
    char *slash;
    if (!real) {
        fprintf(stderr, "c2wrap: C2REAL is not set\n");
        return 2;
    }
    if (capture && il && sscanf(il + 4, "%259s", prefix) == 1) {
        strcpy(dir, prefix);
        slash = strrchr(dir, '\\');
        if (slash) slash[1] = 0; else dir[0] = 0;
        sprintf(pattern, "%s*", prefix);
        h = FindFirstFile(pattern, &found);
        if (h != INVALID_HANDLE_VALUE) {
            do {
                sprintf(from, "%s%s", dir, found.cFileName);
                sprintf(to, "%s\\%s", capture, found.cFileName);
                CopyFile(from, to, FALSE);
            } while (FindNextFile(h, &found));
            FindClose(h);
        }
        sprintf(to, "%s\\flags.txt", capture);
        out = fopen(to, "w");
        if (out) {
            fputs(flags, out);
            fclose(out);
        }
    }
    argv[0] = (char *)real;
    return _spawnv(_P_WAIT, real, (const char * const *)argv);
}
