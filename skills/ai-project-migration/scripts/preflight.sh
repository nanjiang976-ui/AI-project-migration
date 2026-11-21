#!/bin/sh
# Read-only native macOS environment checks, including when Python is absent.

print_check() {
    printf '{"name":"%s","status":"%s","detail":"%s"' "$1" "$2" "$3"
    if [ -n "${4-}" ]; then printf ',"observed":%s' "$4"; fi
    printf '}'
}

report() {
    printf '{"checks":['
    print_check system "$system_status" "$system_detail"
    printf ','
    print_check terminal "$terminal_status" "$terminal_detail"
    printf ','
    print_check python "$python_status" "$python_detail" "$python_observed"
    result=0
    if [ "$system_status" != passed ] || [ "$terminal_status" != passed ] || [ "$python_status" != passed ]; then
        printf ','
        print_check volumes pending 'Complete the native environment checks first.'
        printf ','
        print_check git pending 'Complete the native environment checks first.'
        result=1
    fi
    printf '],"python":%s}\n' "$python_info"
    exit "$result"
}

python_info=null
python_observed=
if [ "$(/usr/bin/uname -s)" != Darwin ]; then
    system_status=incompatible
    system_detail='This entry point requires native macOS.'
    terminal_status=pending
    terminal_detail='Check on the supported operating system.'
    python_status=pending
    python_detail='Check on the supported operating system.'
    report
fi
system_status=passed
system_detail='Native macOS.'

if [ -x /System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal ] ||
   [ -x /Applications/Utilities/Terminal.app/Contents/MacOS/Terminal ]; then
    terminal_status=passed
    terminal_detail='Terminal.app is available.'
else
    terminal_status=missing
    terminal_detail='Terminal.app could not be located.'
fi

probe="import json, os, sys
result = dict(path=os.path.realpath(sys.executable), version='.'.join(map(str, sys.version_info[:3])))
if sys.platform != 'darwin' or sys.implementation.name != 'cpython' or sys.version_info < (3, 12):
    result['implementation'] = sys.implementation.name
    print(json.dumps(result))
    raise SystemExit(3)
print(json.dumps(result))"
incompatible=false
failed=false

resolve_executable() (
    candidate=$1
    remaining_links=40
    while [ "$remaining_links" -gt 0 ]; do
        directory=$(cd -P "${candidate%/*}/" 2>/dev/null && pwd -P) || return 1
        candidate="$directory/${candidate##*/}"
        if [ ! -L "$candidate" ]; then
            [ -f "$candidate" ] && [ -x "$candidate" ] || return 1
            printf '%s\n' "$candidate"
            return 0
        fi
        link=$(/usr/bin/readlink "$candidate") || return 1
        case $link in
            /*) candidate=$link ;;
            *) candidate="$directory/$link" ;;
        esac
        remaining_links=$((remaining_links - 1))
    done
    return 1
)

check_python_directory() {
    case $1 in
        /*) [ -d "$1" ] || return 1 ;;
        *) return 1 ;;
    esac
    for candidate in "$1"/python3 "$1"/python3.*; do
        name=${candidate##*/}
        if [ "$name" != python3 ]; then
            case ${name#python3.} in ''|*[!0-9]*) continue ;; esac
        fi
        executable=$(resolve_executable "$candidate") || continue
        # Apple may use /usr/bin/python3 to launch a developer-tools installer.
        # Other version-manager shims may be shell scripts. Probe only native binaries.
        case $executable in /usr/bin/*|*/shims/*) continue ;; esac
        case $(LC_ALL=C /usr/bin/file -b "$executable" 2>/dev/null) in Mach-O*) ;; *) continue ;; esac

        candidate_info=$("$executable" -I -S -B -c "$probe" 2>/dev/null)
        case $? in
            0)
                case $candidate_info in
                    '{"path": "/'*', "version": "'*'"}') python_info=$candidate_info; return 0 ;;
                    *) failed=true ;;
                esac ;;
            3)
                incompatible=true
                case $candidate_info in
                    '{"path": "/'*', "version": "'*', "implementation": "'*'"}') python_observed=$candidate_info ;;
                esac ;;
            *) failed=true ;;
        esac
    done
    return 1
}

remaining_path=${PATH-}:
while [ -n "$remaining_path" ]; do
    directory=${remaining_path%%:*}
    if check_python_directory "$directory"; then break; fi
    remaining_path=${remaining_path#*:}
done
if [ "$python_info" = null ]; then
    for directory in /opt/homebrew/bin /usr/local/bin /opt/homebrew/opt/python@3.*/bin \
        /usr/local/opt/python@3.*/bin /Library/Frameworks/Python.framework/Versions/3.*/bin; do
        if check_python_directory "$directory"; then break; fi
    done
fi
if [ "$python_info" = null ]; then
    # Query only the already-selected developer tools, then use the actual
    # executable from that tree. Never run the /usr/bin discovery stub.
    developer_dir=$(/usr/bin/xcode-select --print-path 2>/dev/null)
    if [ -n "$developer_dir" ]; then check_python_directory "$developer_dir/usr/bin"; fi
fi

if [ "$python_info" != null ]; then
    python_status=passed
    python_detail='CPython 3.12 or newer is available.'
    python_observed=
elif [ "$incompatible" = true ]; then
    python_status=incompatible
    python_detail='No discovered interpreter met the CPython 3.12+ requirement.'
elif [ "$failed" = true ]; then
    python_status=blocked
    python_detail='A Python executable was found but could not be verified.'
else
    python_status=missing
    python_detail='No installed CPython executable was found in PATH or common install directories.'
fi
report
