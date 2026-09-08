"""Resource-bounded compiler subprocess for language-server snapshots."""
import json
import resource
import sys


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    if sys.platform == 'linux':
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    from gopyt.check import check_package, load_package
    from gopyt.diag import CompileError
    from gopyt.testing import write_lock
    try:
        write_lock(sys.argv[1])
        check_package(load_package(sys.argv[1]))
        result = {}
    except CompileError as exc:
        result = {'file': exc.diag.file, 'line': exc.diag.line, 'code': exc.diag.code, 'message': str(exc)}
    except (OSError, ValueError, RecursionError, MemoryError):
        result = {'error': 'project checking failed within editor resource limits'}
    print(json.dumps(result))


if __name__ == '__main__': main()
