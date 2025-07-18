from conan import ConanFile
from conan.errors import ConanInvalidConfiguration, ConanException
from conan.tools.files import chdir, get, replace_in_file, copy
from conan.tools.layout import basic_layout
import fnmatch
import os
import shutil
import subprocess
import errno
import ctypes

required_conan_version = ">=1.47.0"


class lock:
    def __init__(self):
        self.handle = ctypes.windll.kernel32.CreateMutexA(None, 0, "Global\\ConanMSYS2".encode())
        if not self.handle:
            raise ctypes.WinError()

    def __enter__(self):
        status = ctypes.windll.kernel32.WaitForSingleObject(self.handle, 0xFFFFFFFF)
        if status not in [0, 0x80]:
            raise ctypes.WinError()

    def __exit__(self, exc_type, exc_val, exc_tb):
        status = ctypes.windll.kernel32.ReleaseMutex(self.handle)
        if not status:
            raise ctypes.WinError()

    def close(self):
        ctypes.windll.kernel32.CloseHandle(self.handle)

    __del__ = close

class MSYS2Conan(ConanFile):
    name = "msys2"
    description = "MSYS2 is a software distro and building platform for Windows"
    url = "https://github.com/conan-io/conan-center-index"
    homepage = "http://www.msys2.org"
    license = "MSYS license"
    topics = ("msys", "unix", "subsystem")

    settings = "os", "arch"
    # "exclude_files" "packages" "additional_packages" values are a comma separated list
    options = {
        "exclude_files": ["ANY"],
        "packages": ["ANY"],
        "additional_packages": [None, "ANY"],
        "no_kill": [True, False]
    }
    default_options = {
        "exclude_files": "*/link.exe",
        #"packages": "base-devel,binutils,gcc", # see config_options
        "additional_packages": None,
        "no_kill": False,
    }

    short_paths = True

    def layout(self):
        basic_layout(self, src_folder="src")

    def package_id(self):
        del self.info.options.no_kill

    def config_options(self):
        default_packages = "base-devel,binutils,gcc"
        if self.settings_target is not None and self.settings_target.arch == "armv8":
            # The mingw-w64-cross-mingwarm64-gcc contains tools required to target arm64
            default_packages += ",mingw-w64-cross-mingwarm64-gcc"
        self.options.packages = default_packages

    def validate_build(self):
        if self.settings.os != "Windows":
            raise ConanInvalidConfiguration("Only Windows supported")
        if self.settings.arch != "x86_64":
            raise ConanInvalidConfiguration("Only Windows x64 supported")
        
    def compatibility(self):
        if self.settings.arch == "armv8":
            # Fallback on x86_64 package when natively on Windows arm64
            return [{"settings": [("arch", "x86_64")]}]

    def source(self):
        get(self, **self.conan_data["sources"][self.version],
            destination=self.source_folder, strip_root=False) # Preserve tarball root dir (msys64/)

    def _update_pacman(self):
        with chdir(self, os.path.join(self._msys_dir, "usr", "bin")):
            try:
                self._kill_pacman()

                # https://www.msys2.org/docs/ci/
                self.run('bash -l -c "pacman --debug --noconfirm --ask 20 -Syuu"')  # Core update (in case any core packages are outdated)
                self._kill_pacman()
                self.run('bash -l -c "pacman --debug --noconfirm --ask 20 -Syuu"')  # Normal update
                self._kill_pacman()
                self.run('bash -l -c "pacman --debug -Rc dash --noconfirm"')
            except ConanException:
                self.run('bash -l -c "cat /var/log/pacman.log || echo nolog"')
                self._kill_pacman()
                raise

    # https://github.com/msys2/MSYS2-packages/issues/1966
    def _kill_pacman(self):
        if self.options.no_kill:
            return
        if (self.settings.os == "Windows"):
            taskkill_exe = os.path.join(os.environ.get('SystemRoot'), 'system32', 'taskkill.exe')

            log_out = True
            if log_out:
                out = subprocess.PIPE
                err = subprocess.STDOUT
            else:
                out = open(os.devnull, 'w', encoding='UTF-8')
                err = subprocess.PIPE

            if os.path.exists(taskkill_exe):
                taskkill_cmds = [
                    f"{taskkill_exe} /f /t /im pacman.exe",
                    f"{taskkill_exe} /f /im gpg-agent.exe",
                    f"{taskkill_exe} /f /im dirmngr.exe",
                    f'{taskkill_exe} /fi "MODULES eq msys-2.0.dll"',
                ]
                for taskkill_cmd in taskkill_cmds:
                    try:
                        proc = subprocess.Popen(taskkill_cmd, stdout=out, stderr=err, bufsize=1)
                        proc.wait()
                    except OSError as e:
                        if e.errno == errno.ENOENT:
                            raise ConanException("Cannot kill pacman") from e

    @property
    def _msys_dir(self):
        subdir = "msys64" # top-level directoy in tarball
        return os.path.join(self.source_folder, subdir)

    def build(self):
        with lock():
            self._do_build()

    def _do_build(self):
        packages = []
        if self.options.packages:
            packages.extend(str(self.options.packages).split(","))
        if self.options.additional_packages:
            packages.extend(str(self.options.additional_packages).split(","))

        self._update_pacman()

        with chdir(self, os.path.join(self._msys_dir, "usr", "bin")):
            for package in packages:
                self.run(f'bash -l -c "pacman -S {package} --noconfirm"')
            for package in ['pkgconf']:
                if self.run(f'bash -l -c "pacman -Qq {package}"', ignore_errors=True, quiet=True) == 0:
                    self.run(f'bash -l -c "pacman -Rs -d -d {package} --noconfirm"')
            self.run(f'bash -l -c "pacman -Scc --noconfirm"')

        self._kill_pacman()

        # create /tmp dir in order to avoid
        # bash.exe: warning: could not find /tmp, please create!
        tmp_dir = os.path.join(self._msys_dir, 'tmp')
        if not os.path.isdir(tmp_dir):
            os.makedirs(tmp_dir)
        tmp_name = os.path.join(tmp_dir, 'dummy')
        with open(tmp_name, 'a', encoding='UTF-8'):
            os.utime(tmp_name, None)

        # Prepend the PKG_CONFIG_PATH environment variable with an eventual PKG_CONFIG_PATH environment variable
        # Note: this is no longer needed when we exclusively support Conan 2 integrations
        replace_in_file(self, os.path.join(self._msys_dir, "etc", "profile"),
                              'PKG_CONFIG_PATH="', 'PKG_CONFIG_PATH="${PKG_CONFIG_PATH:+${PKG_CONFIG_PATH}:}')

    def package(self):
        excludes = None
        if self.options.exclude_files:
            excludes = tuple(str(self.options.exclude_files).split(","))
        for exclude in excludes:
            for root, _, filenames in os.walk(self._msys_dir):
                for filename in filenames:
                    fullname = os.path.join(root, filename)
                    if fnmatch.fnmatch(fullname, exclude):
                        os.unlink(fullname)
        # See https://github.com/conan-io/conan-center-index/blob/master/docs/error_knowledge_base.md#kb-h013-default-package-layout
        copy(self, "*", dst=os.path.join(self.package_folder, "bin", "msys64"), src=self._msys_dir, excludes=excludes)
        shutil.copytree(os.path.join(self._msys_dir, "usr", "share", "licenses"),
                        os.path.join(self.package_folder, "licenses"))

    def package_info(self):
        self.cpp_info.libdirs = []
        self.cpp_info.includedirs = []

        msys_root = os.path.join(self.package_folder, "bin", "msys64")
        msys_bin = os.path.join(msys_root, "usr", "bin")
        self.cpp_info.bindirs.append(msys_bin)

        self.buildenv_info.define_path("MSYS_ROOT", msys_root)
        self.buildenv_info.define_path("MSYS_BIN", msys_bin)

        self.conf_info.define("tools.microsoft.bash:subsystem", "msys2")
        self.conf_info.define("tools.microsoft.bash:path", os.path.join(msys_bin, "bash.exe"))

        if self.settings_target is not None and self.settings_target.arch == "armv8":
            # Expose /opt/bin to PATH, so that aarch64-w64-mingw32- prefixed tools can be found
            # Define autotools host/build triplet so that the right tools are used
            self.cpp_info.bindirs.insert(0, os.path.join(msys_root, "opt", "bin"))
            self.conf_info.define("tools.gnu:build_triplet", "x86_64-w64-mingw32")
            self.conf_info.define("tools.gnu:host_triplet", "aarch64-w64-mingw32")
