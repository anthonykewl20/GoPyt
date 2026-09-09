"""Keep development test modules out of the installed runtime wheel."""
from setuptools import setup
from setuptools.command.build_py import build_py
from pathlib import Path


class RuntimeBuild(build_py):
    def find_package_modules(self, package, package_dir):
        return [entry for entry in super().find_package_modules(package, package_dir)
                if not entry[1].startswith('test_')]

    def run(self):
        super().run()
        # setuptools reuses build/lib. Remove only stale generated test modules
        # from older builds; refusing in-place output protects the source tests.
        output=Path(self.build_lib)/'gopyt'
        if output.resolve()==Path(self.get_package_dir('gopyt')).resolve():
            raise RuntimeError('runtime build output must not be the source package')
        for path in output.glob('test_*.py'):
            path.unlink()


setup(cmdclass={'build_py':RuntimeBuild})
